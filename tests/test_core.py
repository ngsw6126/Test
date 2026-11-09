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


def test_step3_prefers_confirmed_then_rate():
    ps = [{"threads_check": "manual", "affiliate_rate": 20, "rank": 1},
          {"threads_check": "pass", "affiliate_rate": 10, "rank": 5},
          {"threads_check": "fail", "affiliate_rate": 30, "rank": 1}]
    picked = research.step3_pick(ps, 5)
    assert [p["threads_check"] for p in picked] == ["pass", "manual"]
    assert picked[0]["active"] and not picked[1]["active"]


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
