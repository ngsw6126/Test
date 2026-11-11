"""コマンドライン入口（Claude APIのキーは使わない）.

Claude Codeのスケジュールタスクが使うもの:
  python -m threads_affiliate import-products <候補.json>  # 3. 調べた候補を検証して5商品を選ぶ
  python -m threads_affiliate plan-drafts                  # 4. 今日書く下書きの材料を出す
  python -m threads_affiliate import-drafts <下書き.json>  # 4. 書いた下書きを検証してキューに追加する
  python -m threads_affiliate export                       # 5. 承認済みを、コピペ用の投稿シートに書き出す

あなたが使うもの:
  python -m threads_affiliate status     # 商品の確認事項と、キューの状況を表示
  python -m threads_affiliate calendar   # セールカレンダー(.ics)を書き出す

任意（Threads APIを使う場合）:
  python -m threads_affiliate post       # 承認済みをThreads APIで自動投稿
  python -m threads_affiliate analyze    # 閲覧数などを取得し、当たり投稿を判定
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

from . import drafting, research, sales, store
from . import queue as q
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


def _affiliate_id(s: dict) -> str:
    # アフィリエイトIDは、リンクに含まれて公開される情報なので settings.yaml に書いてよい
    return str(s.get("rakuten", {}).get("affiliate_id") or "") or env("RAKUTEN_AFFILIATE_ID", required=False)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_import_products(s: dict, args) -> None:
    cfg = s["research"]
    data = _read_json(args.file)
    errors = research.validate_candidates(data)
    if errors:
        print("候補のデータに問題があります。直してからもう一度実行してください:")
        print("\n".join(f"- {e}" for e in errors))
        raise SystemExit(1)
    picked = research.import_candidates(
        data, cfg, affiliate_id=_affiliate_id(s), rakuten=_rakuten(),
        threads=_threads(required=False), previous=store.load(store.PRODUCTS, []))
    store.save(store.PRODUCTS, picked)
    print(f"{len(picked)}商品を data/products.yaml に保存しました")
    _print_products(s, picked)


def _print_products(s: dict, products: list[dict]) -> None:
    cfg = s["research"]
    for p in products:
        rate = f"料率{float(p['affiliate_rate']):g}%" if p.get("affiliate_rate") is not None else "料率未確認"
        mark = "⏸" if p.get("skip") else ("✅" if research.is_ready(p, cfg) else "👀")
        print(f"- {mark} {rate} 需要{p.get('demand_score') or '-'} {p['name'][:40]}")
        if not p.get("skip"):
            for t in research.todo_for(p, cfg):
                print(f"    □ {t}")


def cmd_plan_drafts(s: dict, args) -> None:
    zone = tz(s)
    data = drafting.plan(s, store.load(store.PRODUCTS, []), store.load(store.QUEUE, []),
                         store.load(store.POSTED, []), dt.datetime.now(zone).date(), zone, args.force)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_import_drafts(s: dict, args) -> None:
    queue = store.load(store.QUEUE, [])
    posted = store.load(store.POSTED, [])
    added, errors = drafting.import_drafts(
        _read_json(args.file), store.load(store.PRODUCTS, []), queue, posted,
        s["posting"].get("pr_label", "【PR】"), dt.datetime.now(tz(s)).date())
    if errors:
        print("下書きがルールに合いません。直してからもう一度実行してください（何も追加していません）:")
        print("\n".join(f"- {e}" for e in errors))
        raise SystemExit(1)
    store.save(store.QUEUE, queue)
    store.save(store.POSTED, posted)
    print(f"下書きを{len(added)}本 data/queue.yaml に追加しました")


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
    print("## 商品（👀 = あなたの確認待ち）")
    _print_products(s, store.load(store.PRODUCTS, []))
    print("\n## 下書き")
    for e in store.load(store.QUEUE, []):
        if e["status"] in ("posted", "exported"):
            continue
        ok, why = q.ready(e, s["posting"])
        print(f"{e['id']} [{e['status']}] {'投稿可' if ok else why} / {e['template']} / {e['product_name'][:25]}")


def main(argv=None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="threads_affiliate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ip = sub.add_parser("import-products")
    ip.add_argument("file")
    pd = sub.add_parser("plan-drafts")
    pd.add_argument("--force", action="store_true", help="未投稿の下書きがある商品も含める")
    idr = sub.add_parser("import-drafts")
    idr.add_argument("file")
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
    globals()[f"cmd_{args.cmd.replace('-', '_')}"](s, args)
