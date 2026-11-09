"""投稿キュー: 下書き → 承認 → 投稿.

data/queue.yaml を GitHub 上（スマホでも可）で編集して承認する:
  - status を draft から approved に変える
  - personal_note に自分の体験の一言を書く（例: 私も冬になるとカサカサでした）
"""
from __future__ import annotations

import datetime as dt
import uuid

from .generator import NOTE_SLOT


def make_entries(result: dict, product: dict, kind: str, sale: str | None = None,
                 source_post_id: str | None = None) -> list[dict]:
    now = dt.datetime.now().isoformat(timespec="seconds")
    entries = []
    for d in result["drafts"]:
        entries.append({
            "id": uuid.uuid4().hex[:8],
            "status": "draft",
            "kind": kind,
            "item_code": product.get("item_code"),
            "product_name": product.get("name"),
            "affiliate_url": product.get("affiliate_url"),
            "template": d["template"],
            "text": d["text"],
            "personal_note": "",
            "personal_note_hint": d["personal_note_hint"],
            "sale": sale,
            "source_post_id": source_post_id,
            "created_at": now,
        })
    return entries


def compose(entry: dict, posting: dict) -> tuple[str, str | None]:
    """投稿本文とリプ欄の文章を組み立てる."""
    note = (entry.get("personal_note") or "").strip()
    lines = []
    for line in entry["text"].splitlines():
        if NOTE_SLOT in line:
            if note:
                lines.append(line.replace(NOTE_SLOT, note))
            continue  # 体験が未記入なら、その行ごと消す
        lines.append(line)
    body = "\n".join(lines).strip()
    label = posting.get("pr_label", "【PR】")
    url = entry.get("affiliate_url")
    if posting.get("link_in_reply", True):
        reply = f"{label}紹介した商品はこちら👇\n{url}" if url else None
        return f"{label}\n{body}", reply
    return f"{label}\n{body}\n\n{url or ''}".strip(), None


def ready(entry: dict, posting: dict) -> tuple[bool, str]:
    if posting.get("require_approval", True) and entry.get("status") != "approved":
        return False, "未承認"
    if entry.get("status") not in ("approved", "draft"):
        return False, entry.get("status", "")
    if posting.get("require_personal_note", True) and not (entry.get("personal_note") or "").strip():
        return False, "personal_note が未記入"
    if len(compose(entry, posting)[0]) > 500:
        return False, "500文字を超えています"
    return True, ""
