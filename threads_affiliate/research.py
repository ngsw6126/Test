"""ノウハウ3: ジャンル・商品選定の3ステップ.

1. 売れている商品を集める
   - 標準: Claude Codeのスケジュールタスクが prompts/research.md に従い、ネット検索で
     楽天ランキング・Amazon・@cosme・LIPS・ブログなどを横断して調べ、候補をJSONで渡す（APIキー不要）
   - 任意: 楽天APIのキーがあれば、料率（10%以上か）とアフィリエイトリンクを自動で確認する
2. Threadsで「いいね100以上」の投稿が10個以上あるか
   - 標準: 検索URLを出すので、人が開いて確認する
   - 任意: Threads APIの検索権限があれば自動で判定する
3. 5商品に絞る
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.parse

from .threads_api import search_url

RAKUTEN_ITEM_RE = re.compile(r"^https?://item\.rakuten\.co\.jp/[^/]+/[^/]+")


def short_query(name: str) -> str:
    """楽天の長い商品名から、Threads検索用の短いキーワードを作る."""
    name = re.sub(r"[【\[［(（<＜].*?[】\]］)）>＞]", " ", name)
    name = re.sub(r"(送料無料|ポイント\d+倍|楽天\S*位|公式|正規品|クーポン\S*)", " ", name)
    words = [w for w in re.split(r"[\s　/／|｜,、]+", name) if w]
    return " ".join(words[:2])


# ---------- 手順1: 売れている商品を集める ----------

CANDIDATE_FIELDS = ["name", "search_keyword", "rakuten_url", "price", "why_selling",
                    "target_worries", "media_count", "demand_score", "sources"]


def ranking_url(genre_id) -> str:
    return f"https://ranking.rakuten.co.jp/daily/{genre_id}/"


def affiliate_link(item_url: str, affiliate_id: str) -> str:
    """楽天の商品URLとアフィリエイトIDから、アフィリエイトリンクを作る（APIは使わない）."""
    q = urllib.parse.quote(item_url, safe="")
    return f"https://hb.afl.rakuten.co.jp/hgc/{affiliate_id}/?pc={q}&m={q}"


def validate_candidates(data: dict) -> list[str]:
    errors = []
    cands = data.get("candidates")
    if not isinstance(cands, list) or not cands:
        return ["candidates が空です"]
    for i, c in enumerate(cands):
        missing = [f for f in CANDIDATE_FIELDS if f not in c]
        if missing:
            errors.append(f"candidates[{i}]: 項目が足りません: {', '.join(missing)}")
            continue
        if not str(c["name"]).strip():
            errors.append(f"candidates[{i}]: name が空です")
        if not isinstance(c["sources"], list) or not c["sources"]:
            errors.append(f"candidates[{i}]: sources（出典）が1つもありません")
        elif any(not str(x.get("url", "")).startswith("http") for x in c["sources"]):
            errors.append(f"candidates[{i}]: sources の url が不正です")
        if c["rakuten_url"] and not RAKUTEN_ITEM_RE.match(c["rakuten_url"]):
            errors.append(f"candidates[{i}]: rakuten_url は item.rakuten.co.jp の商品ページにしてください")
        if not isinstance(c["demand_score"], int) or not 1 <= c["demand_score"] <= 5:
            errors.append(f"candidates[{i}]: demand_score は1〜5の整数です")
    return errors


def products_from_candidates(candidates: list[dict], genre_name: str, affiliate_id: str = "") -> list[dict]:
    products = []
    for c in candidates:
        url = c.get("rakuten_url") or ""
        if not RAKUTEN_ITEM_RE.match(url):
            url = ""  # 楽天の商品ページ以外は使わない
        products.append({
            # 楽天の商品コードは「ショップ:商品」の形
            "item_code": ":".join(url.split("item.rakuten.co.jp/")[1].split("/")[:2]) if url else None,
            "name": c["name"],
            "genre": genre_name,
            "price": c.get("price"),
            "rakuten_url": url,
            "affiliate_url": affiliate_link(url, affiliate_id) if url and affiliate_id else "",
            "affiliate_rate": None,
            "threads_query": c.get("search_keyword") or short_query(c["name"]),
            "why_selling": c.get("why_selling"),
            "target_worries": c.get("target_worries"),
            "media_count": c.get("media_count"),
            "demand_score": c.get("demand_score"),
            "sources": c.get("sources"),
        })
    return products


def enrich_with_rakuten(rakuten, products: list[dict], min_rate: float) -> list[dict]:
    """楽天APIのキーがある場合だけ: 料率とアフィリエイトリンクを正確に埋め、料率が低いものを外す."""
    kept = []
    for p in products:
        item = rakuten.search(item_code=p["item_code"]) if p.get("item_code") else None
        item = item or rakuten.search(keyword=p["name"])
        if item:
            rate = item.get("affiliateRate")
            p["affiliate_rate"] = float(rate) if rate is not None else None
            p["affiliate_url"] = item.get("affiliateUrl") or p["affiliate_url"]
            p["item_code"] = item.get("itemCode") or p["item_code"]
            p["caption"] = (item.get("itemCaption") or "")[:600]
            p["review_average"] = item.get("reviewAverage")
            p["review_count"] = item.get("reviewCount")
        if p["affiliate_rate"] is not None and p["affiliate_rate"] < min_rate:
            continue
        kept.append(p)
    return kept


# ---------- 手順2: Threadsで需要を確認する ----------

def step2_threads_check(threads, products: list[dict], cfg: dict) -> None:
    for p in products:
        q = p.get("threads_query") or short_query(p["name"])
        p["threads_query"] = q
        p["threads_search_url"] = search_url(q)
        if threads is None:
            p["threads_check"] = "manual"
            continue
        try:
            posts = threads.keyword_search(q)
        except RuntimeError as e:
            p["threads_check"] = "manual"
            p["threads_error"] = str(e)[:200]
            continue
        likes = [x.get("like_count") for x in posts]
        if all(v is None for v in likes):
            p["threads_check"] = "manual"  # いいね数が返らない → 人が確認する
            continue
        hits = [x for x in posts if (x.get("like_count") or 0) >= cfg["threads_min_likes"]]
        p["threads_hit_posts"] = len(hits)
        p["threads_check"] = "pass" if len(hits) >= cfg["threads_min_hit_posts"] else "fail"
        p["threads_examples"] = [{"text": (x.get("text") or "")[:300], "likes": x.get("like_count"),
                                  "permalink": x.get("permalink")} for x in hits[:5]]


# ---------- 手順3: 5商品に絞る ----------

def todo_for(p: dict, cfg: dict) -> list[str]:
    """投稿を始める前に、人が確認すべきこと."""
    todo = []
    if p.get("threads_check") not in ("pass", "confirmed"):
        todo.append(f"Threadsで検索し、いいね{cfg['threads_min_likes']}以上の投稿が"
                    f"{cfg['threads_min_hit_posts']}個以上あれば threads_check を confirmed にする"
                    f"（なければ skip: true）→ {p.get('threads_search_url')}")
    rate = p.get("affiliate_rate")
    if rate is not None and float(rate) < cfg["min_affiliate_rate"]:
        todo.append(f"料率が{cfg['min_affiliate_rate']:g}%未満です。skip: true にしてください")
    if rate is None:
        todo.append(f"楽天アフィリエイトでリンクを作るときに、料率が{cfg['min_affiliate_rate']:g}%以上か確認し、"
                    "affiliate_rate に書く")
    if not p.get("affiliate_url"):
        todo.append("楽天アフィリエイトで作ったリンクを affiliate_url に貼る")
    return todo


def step3_pick(products: list[dict], cfg: dict) -> list[dict]:
    order = {"pass": 0, "manual": 1}
    usable = [p for p in products if p.get("threads_check") in order]
    usable.sort(key=lambda p: (order[p["threads_check"]], -(p.get("demand_score") or 0),
                               -(p.get("affiliate_rate") or 0), p.get("rank") or 999))
    picked = usable[:cfg["pick_count"]]
    today = dt.date.today().isoformat()
    for p in picked:
        p["selected_at"] = today
        # 確認が残っている商品は、todo が空になるまで下書きを作らない
        p["todo"] = todo_for(p, cfg)
    return picked


def is_ready(p: dict, cfg: dict) -> bool:
    """下書きを作ってよい商品か（毎回その場で判定するので、確認を書き込めばすぐ反映される）."""
    return not p.get("skip") and not todo_for(p, cfg)


def merge_previous(picked: list[dict], previous: list[dict]) -> None:
    """前回あなたが確認・記入した内容（料率、リンク、Threadsの確認、skip）を引き継ぐ."""
    old = {(p.get("item_code") or p["name"]): p for p in previous}
    for p in picked:
        prev = old.get(p.get("item_code") or p["name"])
        if not prev:
            continue
        for key in ("affiliate_rate", "affiliate_url", "threads_check"):
            if prev.get(key) and not p.get(key):
                p[key] = prev[key]
        if prev.get("threads_check") == "confirmed":
            p["threads_check"] = "confirmed"
        if prev.get("skip"):
            p["skip"] = True


def import_candidates(data: dict, cfg: dict, affiliate_id: str = "", rakuten=None, threads=None,
                      previous: list[dict] | None = None) -> list[dict]:
    products = products_from_candidates(data["candidates"], data.get("genre", ""), affiliate_id)
    if rakuten is not None:
        products = enrich_with_rakuten(rakuten, products, cfg["min_affiliate_rate"])
    step2_threads_check(threads, products, cfg)
    picked = step3_pick(products, cfg)
    if previous:
        merge_previous(picked, previous)
        for p in picked:
            p["todo"] = todo_for(p, cfg)
    return picked
