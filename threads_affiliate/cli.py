"""コマンドライン入口.

  python -m threads_affiliate research   # 3. 商品選定（ランキング→Threads確認→5商品）
  python -m threads_affiliate generate   # 4. 下書き生成（新規＋当たり投稿の擦り）
  python -m threads_affiliate export     # 5. 承認済みを、コピペ用の投稿シートに書き出す（API不要）
  python -m threads_affiliate post       # 5. 承認済みをThreads APIで自動投稿（任意）
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


def _rakuten():
    """楽天APIのキーがあれば使う（無くても動く）."""
    app_id = env("RAKUTEN_APP_ID", required=False)
    if not app_id:
        return None
    from .rakuten import RakutenClient
    return RakutenClient(app_id, env("RAKUTEN_AFFILIATE_ID", required=False),
                         env("RAKUTEN_ACCESS_KEY", required=False))


def cmd_research(s: dict, args) -> None:
    cfg = s["research"]
    rakuten = _rakuten()
    researcher = None
    if cfg.get("source", "web") == "web":
        from .web_research import WebResearcher
        researcher = WebResearcher(s["generation"]["model"])
    elif rakuten is None:
        raise SystemExit("research.source が rakuten_api のときは RAKUTEN_APP_ID が必要です")
    picked = research.run(cfg, s["persona"], researcher=researcher, rakuten=rakuten,
                          threads=_threads(required=False),
                          affiliate_id=env("RAKUTEN_AFFILIATE_ID", required=False))
    store.save(store.PRODUCTS, picked)
    print(f"{len(picked)}商品を data/products.yaml に保存しました（調査レポートは data/research/）")
    for p in picked:
        rate = f"料率{p['affiliate_rate']:g}%" if p.get("affiliate_rate") is not None else "料率未確認"
        print(f"- {'✅' if p['active'] else '👀'} {rate} 需要{p.get('demand_score') or '-'} {p['name'][:40]}")
        for t in p["todo"]:
            print(f"    □ {t}")
    if any(not p["active"] for p in picked):
        print("確認が終わった商品は、data/products.yaml で active: true にしてください")


def cmd_generate(s: dict, args) -> None:
    from .generator import Generator
    gen = Generator(s["generation"]["model"])
    queue = store.load(store.QUEUE, [])
    today = dt.datetime.now(tz(s)).date()
    sale = sales.today_sale(s.get("sales"), today, tz(s))
    if not sale:  # 前日に生成しておけるよう、明日がセール重要日かも見る
        sale = sales.today_sale(s.get("sales"), today + dt.timedelta(days=1), tz(s))
    pending_codes = {e.get("product_key") or e["item_code"] for e in queue
                     if e["status"] in ("draft", "approved")}
    added = 0

    for p in store.load(store.PRODUCTS, []):
        if not p.get("active") or ((p.get("item_code") or p["name"]) in pending_codes and not args.force):
            continue
        if not p.get("affiliate_url"):
            print(f"affiliate_url が空なのでスキップ: {p['name'][:30]}")
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
        product["item_code"] = product["item_code"] or hit.get("product_name")
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
    if _manual_mode(s) and not args.dry_run:
        print("手動投稿モードです（Threads APIを使わない）。export で投稿シートを作ってください")
        return
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


def _manual_mode(s: dict) -> bool:
    return s["posting"].get("mode", "manual") == "manual" or not env("THREADS_ACCESS_TOKEN", required=False)


def cmd_export(s: dict, args) -> None:
    """Threads APIを使わない場合: 今日投稿する分を、コピペ用のシートに書き出す.

    Threadsアプリの予約投稿に貼り付ければ、時間指定で投稿できる。
    """
    posting = s["posting"]
    zone = tz(s)
    now = dt.datetime.now(zone)
    today = now.date()
    sale = sales.today_sale(s.get("sales"), today, zone)
    limit = posting["sale_daily_limit"] if sale else posting["daily_limit"]
    posted = store.load(store.POSTED, [])
    remaining = limit - sum(1 for x in posted if str(x.get("posted_at", "")).startswith(today.isoformat()))
    if remaining <= 0:
        print(f"本日の上限（{limit}本）に達しています")
        return
    queue = store.load(store.QUEUE, [])
    candidates = sorted(queue, key=lambda e: (not (sale and e.get("sale")), e["created_at"]))
    path = DATA_DIR / "投稿シート" / f"{today.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    already = limit - remaining
    picked = []
    for entry in candidates:
        if len(picked) >= remaining:
            break
        if q.ready(entry, posting)[0]:
            picked.append(entry)
    if not picked:
        print("書き出せる下書きがありません（承認と personal_note を確認してください）")
        return
    times = posting.get("manual_times", ["07:30", "12:15", "21:00", "19:30", "20:30", "22:30"])
    lines = [] if path.exists() else [
        f"# {today.isoformat()} の投稿" + (f"（{sale}）" if sale else ""), "",
        "Threadsアプリで本文を貼り付け、「…」→「予約」で時間を指定します。",
        "投稿したら、リプ欄にリンクの文章を貼ります。", ""]
    for i, entry in enumerate(picked, start=already):
        body, reply = q.compose(entry, posting)
        lines += [f"## {i + 1}. {times[i % len(times)]} ごろ（{entry['template']}）", "",
                  "### 本文", "```", body, "```", ""]
        if reply:
            lines += ["### リプ欄", "```", reply, "```", ""]
        entry["status"] = "exported"
        posted.append({
            "post_id": None, "queue_id": entry["id"], "kind": entry["kind"],
            "item_code": entry["item_code"], "product_name": entry["product_name"],
            "affiliate_url": entry["affiliate_url"], "template": entry["template"],
            "text": body, "sale": entry.get("sale"),
            "posted_at": now.isoformat(timespec="seconds"),
            "permalink": None, "views": None, "likes": None,
        })
    lines += ["> 数日後、Threadsのインサイトで見た閲覧数といいね数を data/posted.yaml の",
              "> views / likes に書くと、当たり投稿を自動で「擦り」ます。", ""]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    store.save(store.QUEUE, queue)
    store.save(store.POSTED, posted)
    print(f"{len(picked)}本を {path} に書き出しました")


def cmd_analyze(s: dict, args) -> None:
    posted = store.load(store.POSTED, [])
    threads = None if _manual_mode(s) else _threads()
    if threads is None:
        print("Threads APIを使わないため、data/posted.yaml に手で書いた数字で集計します")
        _report(s, posted)
        return
    since = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    for x in posted:
        if str(x.get("posted_at", "")) < since or not x.get("post_id"):
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
    sub.add_parser("export", help="Threads APIを使わない場合の、コピペ用投稿シート")
    sub.add_parser("calendar")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    s = load_settings()
    globals()[f"cmd_{args.cmd}"](s, args)
