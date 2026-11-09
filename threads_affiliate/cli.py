"""コマンドライン入口.

  python -m threads_affiliate research   # 3. 商品選定（ランキング→Threads確認→5商品）
  python -m threads_affiliate generate   # 4. 下書き生成（新規＋当たり投稿の擦り）
  python -m threads_affiliate post       # 5. 承認済みを投稿（1日の上限つき）
  python -m threads_affiliate analyze    # 閲覧数などを取得し、当たり投稿を判定
  python -m threads_affiliate calendar   # セールカレンダー(.ics)を書き出す
  python -m threads_affiliate status     # キューの状況を表示
"""
from __future__ import annotations

import argparse
import datetime as dt

from . import queue as q
from . import research, sales, store
from .config import DATA_DIR, env, load_dotenv, load_settings, tz


def _threads(required: bool = True):
    from .threads_api import ThreadsClient
    token = env("THREADS_ACCESS_TOKEN", required)
    if not token:
        return None
    return ThreadsClient(token, env("THREADS_USER_ID"))


def cmd_research(s: dict, args) -> None:
    from .rakuten import RakutenClient
    rakuten = RakutenClient(env("RAKUTEN_APP_ID"), env("RAKUTEN_AFFILIATE_ID"),
                            env("RAKUTEN_ACCESS_KEY", required=False))
    picked = research.run(rakuten, _threads(required=False), s["research"])
    store.save(store.PRODUCTS, picked)
    print(f"{len(picked)}商品を data/products.yaml に保存しました")
    for p in picked:
        mark = {"pass": "✅ 需要あり", "manual": "👀 要手動確認"}[p["threads_check"]]
        print(f"- {mark} 料率{p['affiliate_rate']}% {p['name'][:40]}")
        if p["threads_check"] == "manual":
            print(f"    検索して「いいね100以上が10件以上」なら active: true にする → {p['threads_search_url']}")


def cmd_generate(s: dict, args) -> None:
    from .generator import Generator
    gen = Generator(s["generation"]["model"])
    queue = store.load(store.QUEUE, [])
    today = dt.datetime.now(tz(s)).date()
    sale = sales.today_sale(s.get("sales"), today, tz(s))
    if not sale:  # 前日に生成しておけるよう、明日がセール重要日かも見る
        sale = sales.today_sale(s.get("sales"), today + dt.timedelta(days=1), tz(s))
    pending_codes = {e["item_code"] for e in queue if e["status"] in ("draft", "approved")}
    added = 0

    for p in store.load(store.PRODUCTS, []):
        if not p.get("active") or (p["item_code"] in pending_codes and not args.force):
            continue
        result = gen.new_posts(p, s["persona"], s["generation"]["templates"],
                               s["generation"]["drafts_per_product"], sale)
        queue += q.make_entries(result, p, "new", sale)
        added += len(result["drafts"])

    # 当たり投稿を擦る
    r = s.get("reuse", {})
    posted = store.load(store.POSTED, [])
    cutoff = (today - dt.timedelta(days=r.get("cooldown_days", 14))).isoformat()
    for hit in posted:
        if (hit.get("views") or 0) < r.get("min_views", 3000):
            continue
        if (hit.get("last_reused_at") or "") > cutoff:
            continue
        product = {"item_code": hit.get("item_code"), "name": hit.get("product_name"),
                   "affiliate_url": hit.get("affiliate_url")}
        result = gen.reuse_posts(hit, r.get("variants_per_hit", 2))
        queue += q.make_entries(result, product, "reuse", sale, hit["post_id"])
        hit["last_reused_at"] = today.isoformat()
        added += len(result["drafts"])

    store.save(store.QUEUE, queue)
    store.save(store.POSTED, posted)
    print(f"下書きを{added}本追加しました（data/queue.yaml）。"
          "status を approved にし、personal_note に体験の一言を書くと投稿されます")


def cmd_post(s: dict, args) -> None:
    posting = s["posting"]
    zone = tz(s)
    today = dt.datetime.now(zone).date()
    sale = sales.today_sale(s.get("sales"), today, zone)
    if args.sale_only and not sale:
        print("セールの重要日ではないので、この枠はスキップします")
        return
    limit = posting["sale_daily_limit"] if sale else posting["daily_limit"]
    posted = store.load(store.POSTED, [])
    done_today = sum(1 for x in posted if str(x.get("posted_at", "")).startswith(today.isoformat()))
    remaining = min(limit - done_today, args.max)
    if remaining <= 0:
        print(f"本日の上限（{limit}本）に達しています")
        return

    queue = store.load(store.QUEUE, [])
    # セール中はセール向けの下書きを優先する
    candidates = sorted(queue, key=lambda e: (not (sale and e.get("sale")), e["created_at"]))
    threads = None if args.dry_run else _threads()
    count = 0
    for entry in candidates:
        if count >= remaining:
            break
        ok, why = q.ready(entry, posting)
        if not ok:
            continue
        body, reply = q.compose(entry, posting)
        if args.dry_run:
            print(f"--- [dry-run] {entry['id']}\n{body}\n  └ リプ: {reply}")
            count += 1
            continue
        post_id = threads.post_text(body)
        if reply:
            threads.post_text(reply, reply_to_id=post_id)
        entry["status"] = "posted"
        posted.append({
            "post_id": post_id, "queue_id": entry["id"], "kind": entry["kind"],
            "item_code": entry["item_code"], "product_name": entry["product_name"],
            "affiliate_url": entry["affiliate_url"], "template": entry["template"],
            "text": body, "sale": entry.get("sale"),
            "posted_at": dt.datetime.now(zone).isoformat(timespec="seconds"),
            "permalink": threads.permalink(post_id),
        })
        count += 1
        print(f"投稿しました: {entry['id']} → {posted[-1]['permalink']}")
        # 1件ごとに保存して、途中で失敗しても二重投稿しないようにする
        store.save(store.QUEUE, queue)
        store.save(store.POSTED, posted)
    if count == 0:
        print("投稿できる下書きがありません（承認と personal_note を確認してください）")


def cmd_analyze(s: dict, args) -> None:
    threads = _threads()
    posted = store.load(store.POSTED, [])
    since = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    for x in posted:
        if str(x.get("posted_at", "")) < since:
            continue
        try:
            x.update(threads.insights(x["post_id"]))
        except RuntimeError as e:
            print(f"取得失敗 {x['post_id']}: {e}")
    store.save(store.POSTED, posted)
    _report(s, posted)


def _report(s: dict, posted: list[dict]) -> None:
    min_views = s.get("reuse", {}).get("min_views", 3000)
    by_template: dict[str, list[int]] = {}
    for x in posted:
        by_template.setdefault(x.get("template", "?"), []).append(x.get("views") or 0)
    lines = ["# 投稿レポート", "", "## 型ごとの平均閲覧数", "", "| 型 | 本数 | 平均閲覧 |", "|---|---|---|"]
    for t, v in sorted(by_template.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        lines.append(f"| {t} | {len(v)} | {sum(v) // len(v)} |")
    lines += ["", f"## 当たり投稿（閲覧 {min_views} 以上 → 擦る対象）", ""]
    for x in sorted(posted, key=lambda x: -(x.get("views") or 0)):
        if (x.get("views") or 0) >= min_views:
            lines.append(f"- 閲覧{x.get('views')} いいね{x.get('likes')} {x.get('product_name', '')[:30]} {x.get('permalink')}")
    (DATA_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def cmd_calendar(s: dict, args) -> None:
    path = DATA_DIR / "sales.ics"
    path.write_text(sales.to_ics(s.get("sales"), tz(s)), encoding="utf-8")
    print(f"{path} を書き出しました。スマホのカレンダーに読み込んでください")


def cmd_status(s: dict, args) -> None:
    queue = store.load(store.QUEUE, [])
    for e in queue:
        if e["status"] == "posted":
            continue
        ok, why = q.ready(e, s["posting"])
        print(f"{e['id']} [{e['status']}] {'投稿可' if ok else why} / {e['template']} / {e['product_name'][:25]}")


def main(argv=None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="threads_affiliate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("research")
    g = sub.add_parser("generate")
    g.add_argument("--force", action="store_true", help="未投稿の下書きがあっても生成する")
    p = sub.add_parser("post")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=1, help="1回の実行で投稿する最大数")
    p.add_argument("--sale-only", action="store_true", help="セールの前日・開始日・終了日だけ投稿する")
    a = sub.add_parser("analyze")
    a.add_argument("--days", type=int, default=7)
    sub.add_parser("calendar")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    s = load_settings()
    globals()[f"cmd_{args.cmd}"](s, args)
