import datetime as dt
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from threads_affiliate import queue as q
from threads_affiliate import research, sales
from threads_affiliate.generator import Generator

JST = ZoneInfo("Asia/Tokyo")
CFG = {"genres": [{"id": 1, "name": "コスメ"}], "ranking_top_n": 30, "min_affiliate_rate": 10.0,
       "threads_min_likes": 100, "threads_min_hit_posts": 10, "pick_count": 5}
POSTING = {"pr_label": "【PR】", "link_in_reply": True, "require_approval": True,
           "require_personal_note": True}


class FakeRakuten:
    def ranking(self, genre_id, top_n):
        return [
            {"itemCode": "a", "itemName": "【送料無料】うるおいクリーム 50g", "rank": 1, "affiliateRate": 4},
            {"itemCode": "b", "itemName": "高保湿 ハンドクリーム 3本セット", "rank": 2, "affiliateRate": 12},
            {"itemCode": "c", "itemName": "ボディミルク", "rank": 3},  # 料率なし → 検索APIで補う
        ]

    def affiliate_rate(self, code):
        return 10.0


class FakeThreads:
    def __init__(self, likes):
        self.likes = likes

    def keyword_search(self, query):
        return [{"text": "x", "like_count": v, "permalink": "p"} for v in self.likes]


def test_step1_filters_by_rate_and_fills_missing_rate():
    products = research.step1_ranking(FakeRakuten(), CFG)
    assert [p["item_code"] for p in products] == ["b", "c"]
    assert products[1]["affiliate_rate"] == 10.0


def test_step2_pass_fail_and_manual():
    p = [{"name": "高保湿 ハンドクリーム"}]
    research.step2_threads_check(FakeThreads([150] * 10), p, CFG)
    assert p[0]["threads_check"] == "pass"
    research.step2_threads_check(FakeThreads([150] * 9 + [50]), p, CFG)
    assert p[0]["threads_check"] == "fail"
    research.step2_threads_check(FakeThreads([None, None]), p, CFG)
    assert p[0]["threads_check"] == "manual"
    research.step2_threads_check(None, p, CFG)
    assert p[0]["threads_check"] == "manual" and "threads.com/search" in p[0]["threads_search_url"]


def test_step3_prefers_confirmed_and_lists_todo():
    ps = [{"threads_check": "manual", "affiliate_rate": 20, "rank": 1, "affiliate_url": "u"},
          {"threads_check": "pass", "affiliate_rate": 10, "rank": 5, "affiliate_url": "u"},
          {"threads_check": "pass", "affiliate_rate": None, "demand_score": 5, "affiliate_url": ""},
          {"threads_check": "fail", "affiliate_rate": 30, "rank": 1}]
    picked = research.step3_pick(ps, CFG)
    assert [p["threads_check"] for p in picked] == ["pass", "pass", "manual"]
    assert not picked[0]["active"] and len(picked[0]["todo"]) == 2  # 料率とリンクが未確認
    assert picked[1]["active"] and picked[1]["todo"] == []
    assert not picked[2]["active"]


def test_short_query():
    assert research.short_query("【楽天1位】送料無料 うるおい クリーム 50g") == "うるおい クリーム"


def entry(**kw):
    e = {"status": "approved", "text": "手がガサガサ\n{体験}\nこれに変えた", "personal_note": "私もそうでした",
         "affiliate_url": "https://hb.afl.rakuten.co.jp/x"}
    e.update(kw)
    return e


def test_compose_puts_pr_label_and_link_in_reply():
    body, reply = q.compose(entry(), POSTING)
    assert body == "【PR】\n手がガサガサ\n私もそうでした\nこれに変えた"
    assert reply.startswith("【PR】") and "https://hb.afl.rakuten.co.jp/x" in reply
    body, _ = q.compose(entry(personal_note=""), POSTING)
    assert "{体験}" not in body


def test_ready_rules():
    assert q.ready(entry(), POSTING)[0]
    assert not q.ready(entry(status="draft"), POSTING)[0]
    assert not q.ready(entry(personal_note=" "), POSTING)[0]
    assert not q.ready(entry(status="posted"), POSTING)[0]
    assert not q.ready(entry(text="あ" * 600), POSTING)[0]


def test_sale_key_days_handle_midnight_end():
    sale = {"name": "スーパーSALE", "start": "2026-12-04T20:00", "end": "2026-12-11T01:59"}
    days = sales.key_days(sale, JST)
    assert days == {"前日": dt.date(2026, 12, 3), "開始日": dt.date(2026, 12, 4), "終了日": dt.date(2026, 12, 10)}
    assert sales.today_sale([sale], dt.date(2026, 12, 10), JST) == "スーパーSALE（終了日）"
    assert sales.today_sale([sale], dt.date(2026, 12, 7), JST) is None
    ics = sales.to_ics([sale], JST)
    assert ics.count("BEGIN:VEVENT") == 3 and "DTSTART;VALUE=DATE:20261203" in ics


def test_generator_parses_structured_output():
    payload = {"analysis": {"target_worries": ["乾燥"], "why_persona_chooses": "時短", "cautions": ""},
               "drafts": [{"template": "生々しい一言", "text": "t\n{体験}", "personal_note_hint": "h"}]}
    calls = {}

    def create(**kw):
        calls.update(kw)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(payload))])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    gen = Generator("claude-opus-5-5", client=client)
    result = gen.new_posts({"name": "クリーム"}, "30代の母親", [{"name": "型"}], 3, sale="SALE（開始日）")
    assert result == payload
    assert calls["output_config"]["format"]["type"] == "json_schema"
    assert "SALE（開始日）" in calls["messages"][0]["content"]
    entries = q.make_entries(result, {"item_code": "b", "name": "クリーム", "affiliate_url": "u"}, "new")
    assert entries[0]["status"] == "draft" and entries[0]["personal_note"] == ""


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def test_web_research_resumes_pause_turn_and_builds_products(tmp_path, monkeypatch):
    from threads_affiliate import web_research
    monkeypatch.setattr(research, "DATA_DIR", tmp_path)
    streams = [SimpleNamespace(stop_reason="pause_turn", content=[SimpleNamespace(type="server_tool_use")]),
               SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="レポート本文")])]
    seen = []

    def stream(**kw):
        seen.append(kw)
        return FakeStream(streams.pop(0))

    cands = {"candidates": [
        {"name": "Aクリーム", "search_keyword": "A ハンドクリーム",
         "rakuten_url": "https://item.rakuten.co.jp/shopx/item-1/", "price": "1980円",
         "why_selling": "口コミで保湿力", "target_worries": ["乾燥"], "media_count": 3, "demand_score": 5,
         "sources": [{"media": "@cosme", "url": "https://example.com", "note": "1位"}]},
        {"name": "B", "search_keyword": "B", "rakuten_url": "https://amzn.to/x", "price": "",
         "why_selling": "", "target_worries": [], "media_count": 1, "demand_score": 2, "sources": []}]}

    def create(**kw):
        assert "レポート本文" in kw["messages"][0]["content"]
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(cands))])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream, create=create)))
    r = web_research.WebResearcher("claude-opus-5-5", client=client)
    products = research.step1_web(r, [{"id": 100939, "name": "コスメ"}], "30代の母親", affiliate_id="AFF")
    assert len(seen) == 2 and seen[1]["messages"][1]["role"] == "assistant"
    assert "ranking.rakuten.co.jp/daily/100939" in seen[0]["messages"][0]["content"]
    assert {t["type"] for t in seen[0]["tools"]} == {"web_search_20260209", "web_fetch_20260209"}
    a, b = products
    assert a["item_code"] == "shopx:item-1"
    assert a["affiliate_url"].startswith("https://hb.afl.rakuten.co.jp/hgc/AFF/?pc=https%3A%2F%2Fitem.rakuten")
    assert b["rakuten_url"] == "" and b["affiliate_url"] == ""  # 楽天以外のURLは使わない
    assert (tmp_path / "research").exists()


def test_export_sheet_appends_and_respects_limit(tmp_path, monkeypatch):
    from threads_affiliate import cli, store
    monkeypatch.setattr(store, "QUEUE", tmp_path / "q.yaml")
    monkeypatch.setattr(store, "POSTED", tmp_path / "p.yaml")
    monkeypatch.setattr(cli, "DATA_DIR", tmp_path)
    store.save(store.QUEUE, [dict(entry(), id=str(i), kind="new", item_code="b", product_name="P",
                                  template="型", created_at=f"2026-11-0{i}") for i in range(1, 6)])
    s = {"posting": dict(POSTING, daily_limit=3, sale_daily_limit=6, timezone="Asia/Tokyo"), "sales": []}
    cli.cmd_export(s, None)
    cli.cmd_export(s, None)  # 2回目は上限に達しているので何もしない
    sheet = next((tmp_path / "投稿シート").glob("*.md")).read_text(encoding="utf-8")
    assert sheet.count("### 本文") == 3 and "【PR】紹介した商品はこちら" in sheet
    statuses = [e["status"] for e in store.load(store.QUEUE, [])]
    assert statuses.count("exported") == 3
