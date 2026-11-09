"""ノウハウ3: ジャンル・商品選定の3ステップ.

1. 売れている商品を集める
   - 標準: Claudeのネット検索で、楽天ランキング・Amazon・@cosme・LIPS・ブログなどを横断して調べる（APIの登録は不要）
   - 任意: 楽天APIのキーがあれば、料率（10%以上か）とアフィリエイトリンクを自動で確認する
2. Threadsで「いいね100以上」の投稿が10個以上あるか
   - 標準: 検索URLを出すので、人が開いて確認する
   - 任意: Threads APIの検索権限があれば自動で判定する
3. 5商品に絞る
"""
from __future__ import annotations

import datetime as dt
import re

from .config import DATA_DIR
from .threads_api import search_url
from .web_research import affiliate_link

RAKUTEN_ITEM_RE = re.compile(r"^https?://item\.rakuten\.co\.jp/[^/]+/[^/]+")


def short_query(name: str) -> str:
    """楽天の長い商品名から、Threads検索用の短いキーワードを作る."""
    name = re.sub(r"[【\[［(（<＜].*?[】\]］)）>＞]", " ", name)
    name = re.sub(r"(送料無料|ポイント\d+倍|楽天\S*位|公式|正規品|クーポン\S*)", " ", name)
    words = [w for w in re.split(r"[\s　/／|｜,、]+", name) if w]
    return " ".join(words[:2])


# ---------- 手順1: 売れている商品を集める ----------

def step1_web(researcher, genres: list[dict], persona: str, affiliate_id: str = "",
              save_reports: bool = True) -> list[dict]:
    products = []
    today = dt.date.today().isoformat()
    for genre in genres:
        report, candidates = researcher.research(genre, persona)
        if save_reports:
            path = DATA_DIR / "research" / f"{today}_{genre['id']}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {genre['name']} リサーチ（{today}）\n\n{report}\n", encoding="utf-8")
        for c in candidates:
            url = c.get("rakuten_url") or ""
            if not RAKUTEN_ITEM_RE.match(url):
                url = ""  # 楽天の商品ページ以外は使わない
            products.append({
                # 楽天の商品コードは「ショップ:商品」の形
                "item_code": ":".join(url.split("item.rakuten.co.jp/")[1].split("/")[:2]) if url else None,
                "name": c["name"],
                "genre": genre["name"],
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


def step1_ranking(rakuten, cfg: dict) -> list[dict]:
    """（楽天APIを使う場合の旧方式）ランキング1〜30位から料率10%以上を抽出する."""
    from .rakuten import to_product
    candidates = []
    for genre in cfg["genres"]:
        for item in rakuten.ranking(genre["id"], cfg["ranking_top_n"]):
            rate = item.get("affiliateRate")
            if rate is None and item.get("itemCode"):
                rate = rakuten.affiliate_rate(item["itemCode"])
            if rate is None or float(rate) < cfg["min_affiliate_rate"]:
                continue
            p = to_product(item, genre["name"], float(rate))
            p["threads_query"] = short_query(p["name"])
            candidates.append(p)
    return candidates


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
    if p.get("threads_check") != "pass":
        todo.append(f"Threadsで検索し、いいね{cfg['threads_min_likes']}以上の投稿が"
                    f"{cfg['threads_min_hit_posts']}個以上あるか確認する → {p.get('threads_search_url')}")
    if p.get("affiliate_rate") is None:
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
        p["todo"] = todo_for(p, cfg)
        # 確認が残っている商品は、人が確認して active: true にするまで投稿を作らない
        p["active"] = not p["todo"]
    return picked


def run(cfg: dict, persona: str, researcher=None, rakuten=None, threads=None,
        affiliate_id: str = "") -> list[dict]:
    if cfg.get("source", "web") == "rakuten_api":
        candidates = step1_ranking(rakuten, cfg)
    else:
        candidates = step1_web(researcher, cfg["genres"], persona, affiliate_id)
        if rakuten is not None:
            candidates = enrich_with_rakuten(rakuten, candidates, cfg["min_affiliate_rate"])
    step2_threads_check(threads, candidates, cfg)
    return step3_pick(candidates, cfg)
