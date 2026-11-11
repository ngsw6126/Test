"""ノウハウ4・5: 下書き作りの「材料の準備」と「受け取ったときのチェック」.

文章そのものは、Claude Codeのスケジュールタスクが prompts/generate.md に従って書く
（Claude APIのキーは使わない）。このモジュールは、
  - 何を書けばいいか（plan）をまとめ、
  - 書かれた下書きがルールを守っているか（validate）を機械的に確かめ、
  - キューに追加する（import）
だけを担当する。
"""
from __future__ import annotations

import datetime as dt
import re

from . import sales
from .research import is_ready

NOTE_SLOT = "{体験}"
MAX_LEN = 500

# 薬機法・景品表示法で問題になりやすい言い回し（見つけたら下書きを受け付けない）
BANNED = ["必ず", "絶対", "治る", "治す", "治り", "完治", "消える", "消せる", "100%", "１００％",
          "副作用なし", "最強", "日本一", "世界一", "誰でも", "飲むだけで", "塗るだけで"]
URL_RE = re.compile(r"https?://|www\.|\.com\b|\.co\.jp\b")


def validate_draft(d: dict, pr_label: str = "【PR】") -> list[str]:
    errors = []
    text = d.get("text") or ""
    for key in ("template", "text", "personal_note_hint"):
        if not str(d.get(key) or "").strip():
            errors.append(f"{key} が空です")
    if text.count(NOTE_SLOT) != 1:
        errors.append(f"本文に {NOTE_SLOT} をちょうど1回入れてください")
    if URL_RE.search(text):
        errors.append("本文にURLを書かないでください（リンクはリプ欄に自動で入ります）")
    if "PR" in text or "広告" in text:
        errors.append("PR表記は自動で付くので、本文には書かないでください")
    hit = [w for w in BANNED if w in text]
    if hit:
        errors.append(f"誇大・断定表現があります: {'、'.join(hit)}")
    if len(pr_label) + 1 + len(text.replace(NOTE_SLOT, "")) + 40 > MAX_LEN:
        errors.append(f"長すぎます（体験の一言を足しても{MAX_LEN}文字以内に収まるように）")
    return errors


def plan(settings: dict, products: list[dict], queue: list[dict], posted: list[dict],
         today: dt.date, zone, force: bool = False) -> dict:
    """スケジュールタスクに渡す「今日書くもの」の一覧."""
    sale = sales.today_sale(settings.get("sales"), today, zone) or \
        sales.today_sale(settings.get("sales"), today + dt.timedelta(days=1), zone)
    pending = {e.get("product_key") or e.get("item_code") for e in queue
               if e.get("status") in ("draft", "approved")}

    new = []
    for p in products:
        key = p.get("item_code") or p["name"]
        if not is_ready(p, settings["research"]):
            continue
        if key in pending and not force:
            continue
        new.append({k: p.get(k) for k in (
            "item_code", "name", "price", "shop", "why_selling", "target_worries", "sources",
            "caption", "review_average", "review_count", "threads_examples")})

    r = settings.get("reuse", {})
    cutoff = (today - dt.timedelta(days=r.get("cooldown_days", 14))).isoformat()
    reuse = []
    for hit in posted:
        if (hit.get("views") or 0) < r.get("min_views", 3000):
            continue
        if (hit.get("last_reused_at") or "") > cutoff:
            continue
        reuse.append({"source_post_id": hit.get("post_id") or hit.get("queue_id"),
                      "product_name": hit.get("product_name"), "text": hit.get("text"),
                      "views": hit.get("views"), "likes": hit.get("likes")})

    g = settings["generation"]
    return {
        "date": today.isoformat(),
        "persona": settings["persona"],
        "sale": sale,
        "drafts_per_product": g["drafts_per_product"],
        "variants_per_hit": r.get("variants_per_hit", 2),
        "templates": g["templates"],
        "new_products": new,
        "reuse_hits": reuse,
    }


def import_drafts(data: dict, products: list[dict], queue: list[dict], posted: list[dict],
                  pr_label: str, today: dt.date) -> tuple[list[dict], list[str]]:
    """下書きを検証してキューに追加する。1本でもルール違反があれば、何も追加しない."""
    from .queue import make_entry

    by_key = {(p.get("item_code") or p["name"]): p for p in products}
    by_name = {p["name"]: p for p in products}
    hits = {(h.get("post_id") or h.get("queue_id")): h for h in posted}
    errors, entries = [], []

    for i, d in enumerate(data.get("drafts", [])):
        where = f"drafts[{i}]"
        errs = validate_draft(d, pr_label)
        kind = d.get("kind", "new")
        if kind == "new":
            product = by_key.get(d.get("item_code")) or by_name.get(d.get("product_name"))
            if product is None:
                errs.append("item_code が products.yaml の商品と一致しません")
            src = None
        elif kind == "reuse":
            hit = hits.get(d.get("source_post_id"))
            if hit is None:
                errs.append("source_post_id が posted.yaml の投稿と一致しません")
                product = None
            else:
                product = {"item_code": hit.get("item_code") or hit.get("product_name"),
                           "name": hit.get("product_name"), "affiliate_url": hit.get("affiliate_url")}
            src = d.get("source_post_id")
        else:
            errs.append("kind は new か reuse にしてください")
            product, src = None, None
        if errs:
            errors += [f"{where}: {e}" for e in errs]
            continue
        entries.append(make_entry(d, product, kind, d.get("sale"), src))

    if errors:
        return [], errors
    for e in entries:
        if e["kind"] == "reuse" and e["source_post_id"] in hits:
            hits[e["source_post_id"]]["last_reused_at"] = today.isoformat()
    queue.extend(entries)
    return entries, []
