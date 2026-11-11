import datetime as dt
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from threads_affiliate import queue as q
from threads_affiliate import research, sales
from threads_affiliate import drafting

JST = ZoneInfo("Asia/Tokyo")
CFG = {"genres": [{"id": 1, "name": "コスメ"}], "ranking_top_n": 30, "min_affiliate_rate": 10.0,
       "threads_min_likes": 100, "threads_min_hit_posts": 10, "pick_count": 5}
POSTING = {"pr_label": "【PR】", "link_in_reply": True, "require_approval": True,
           "require_personal_note": True}


class FakeThreads:
    def __init__(self, likes):
        self.likes = likes

    def keyword_search(self, query):
        return [{"text": "x", "like_count": v, "permalink": "p"} for v in self.likes]


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
    assert len(picked[0]["todo"]) == 2  # 料率とリンクが未確認
    assert picked[1]["todo"] == [] and research.is_ready(picked[1], CFG)
    assert not research.is_ready(picked[2], CFG)
    picked[2]["threads_check"] = "confirmed"  # 人が確認したら、その場で使えるようになる
    assert research.is_ready(picked[2], CFG)
    picked[2]["skip"] = True
    assert not research.is_ready(picked[2], CFG)


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


def candidate(**kw):
    c = {"name": "Aクリーム", "search_keyword": "A ハンドクリーム",
         "rakuten_url": "https://item.rakuten.co.jp/shopx/item-1/", "price": "1980円",
         "why_selling": "口コミで保湿力", "target_worries": ["乾燥"], "media_count": 3, "demand_score": 5,
         "sources": [{"media": "@cosme", "url": "https://example.com", "note": "1位"}]}
    c.update(kw)
    return c


def test_validate_candidates():
    assert research.validate_candidates({"candidates": [candidate()]}) == []
    errs = research.validate_candidates({"candidates": [
        candidate(sources=[]), candidate(rakuten_url="https://amzn.to/x"), candidate(demand_score=9)]})
    assert len(errs) == 3


def test_import_candidates_builds_links_and_keeps_previous_checks():
    data = {"genre": "コスメ", "candidates": [candidate(), candidate(name="B", rakuten_url="", demand_score=2)]}
    previous = [{"item_code": "shopx:item-1", "name": "Aクリーム", "affiliate_rate": 12,
                 "threads_check": "confirmed"}]
    picked = research.import_candidates(data, CFG, affiliate_id="AFF", previous=previous)
    a, b = picked
    assert a["item_code"] == "shopx:item-1"
    assert a["affiliate_url"].startswith("https://hb.afl.rakuten.co.jp/hgc/AFF/?pc=https%3A%2F%2Fitem.rakuten")
    assert a["todo"] == [] and research.is_ready(a, CFG)  # 前回の確認を引き継ぐ
    assert b["affiliate_url"] == "" and len(b["todo"]) == 3


def draft(**kw):
    d = {"kind": "new", "item_code": "shopx:item-1", "product_name": "Aクリーム", "source_post_id": None,
         "template": "生々しい一言", "text": "冬の手が痛い\n{体験}\nこれに変えた", "personal_note_hint": "h",
         "sale": None}
    d.update(kw)
    return d


def test_validate_draft_rules():
    assert drafting.validate_draft(draft()) == []
    assert drafting.validate_draft(draft(text="体験なし"))
    assert drafting.validate_draft(draft(text="{体験}\nhttps://x.com"))
    assert drafting.validate_draft(draft(text="{体験}\n必ず治る"))
    assert drafting.validate_draft(draft(text="【PR】{体験}"))
    assert drafting.validate_draft(draft(text="{体験}" + "あ" * 480))


def test_plan_and_import_drafts():
    settings = {"persona": "30代の母親", "sales": [], "research": CFG,
                "generation": {"drafts_per_product": 3, "templates": [{"name": "型"}]},
                "reuse": {"min_views": 3000, "variants_per_hit": 2, "cooldown_days": 14}}
    products = [{"item_code": "shopx:item-1", "name": "Aクリーム", "affiliate_url": "u", "affiliate_rate": 12,
                 "threads_check": "confirmed"},
                {"item_code": "shopy:z", "name": "Z", "affiliate_url": "", "threads_check": "manual"}]
    posted = [{"post_id": "p1", "product_name": "Aクリーム", "item_code": "shopx:item-1", "affiliate_url": "u",
               "text": "当たり", "views": 5000}]
    today = dt.date(2026, 11, 11)
    pl = drafting.plan(settings, products, [], posted, today, JST)
    assert [p["name"] for p in pl["new_products"]] == ["Aクリーム"]
    assert pl["reuse_hits"][0]["source_post_id"] == "p1"

    queue = []
    bad = {"drafts": [draft(), draft(item_code="none", product_name="none")]}
    added, errors = drafting.import_drafts(bad, products, queue, posted, "【PR】", today)
    assert errors and queue == []  # 1本でも問題があれば何も追加しない

    good = {"drafts": [draft(), draft(kind="reuse", source_post_id="p1", template="擦り: 冒頭")]}
    added, errors = drafting.import_drafts(good, products, queue, posted, "【PR】", today)
    assert errors == [] and len(queue) == 2 and queue[0]["affiliate_url"] == "u"
    assert posted[0]["last_reused_at"] == "2026-11-11"
    pl = drafting.plan(settings, products, queue, posted, today, JST)
    assert pl["new_products"] == [] and pl["reuse_hits"] == []  # 書いた分は次回の対象から外れる
