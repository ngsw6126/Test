"""ノウハウ3: ジャンル・商品選定の3ステップ.

1. 楽天ランキング1〜30位をチェック → 料率10%以上だけ残す
2. Threadsで検索 → 「いいね100以上」の投稿が10個以上あるか
3. 5商品に絞る
"""
from __future__ import annotations

import datetime as dt
import re

from .rakuten import RakutenClient, to_product
from .threads_api import ThreadsClient, search_url


def short_query(name: str) -> str:
    """楽天の長い商品名から、Threads検索用の短いキーワードを作る."""
    name = re.sub(r"[【\[［(（<＜].*?[】\]］)）>＞]", " ", name)
    name = re.sub(r"(送料無料|ポイント\d+倍|楽天\S*位|公式|正規品|クーポン\S*)", " ", name)
    words = [w for w in re.split(r"[\s　/／|｜,、]+", name) if w]
    return " ".join(words[:2])


def step1_ranking(rakuten: RakutenClient, cfg: dict) -> list[dict]:
    candidates = []
    for genre in cfg["genres"]:
        for item in rakuten.ranking(genre["id"], cfg["ranking_top_n"]):
            rate = item.get("affiliateRate")
            if rate is None and item.get("itemCode"):
                rate = rakuten.affiliate_rate(item["itemCode"])
            if rate is None or float(rate) < cfg["min_affiliate_rate"]:
                continue
            candidates.append(to_product(item, genre["name"], float(rate)))
    return candidates


def step2_threads_check(threads: ThreadsClient | None, products: list[dict], cfg: dict) -> None:
    """各商品にThreadsでの需要チェック結果を書き込む."""
    for p in products:
        q = short_query(p["name"])
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
            # APIがいいね数を返さない → 人が検索URLを開いて確認する
            p["threads_check"] = "manual"
            p["threads_posts_found"] = len(posts)
            continue
        hits = [x for x in posts if (x.get("like_count") or 0) >= cfg["threads_min_likes"]]
        p["threads_hit_posts"] = len(hits)
        p["threads_check"] = "pass" if len(hits) >= cfg["threads_min_hit_posts"] else "fail"
        p["threads_examples"] = [{"text": (x.get("text") or "")[:300], "likes": x.get("like_count"),
                                  "permalink": x.get("permalink")} for x in hits[:5]]


def step3_pick(products: list[dict], count: int) -> list[dict]:
    """Threadsで需要が確認できたもの → 未確認のもの、の順に、料率・順位で並べて上位を選ぶ."""
    order = {"pass": 0, "manual": 1}
    usable = [p for p in products if p.get("threads_check") in order]
    usable.sort(key=lambda p: (order[p["threads_check"]], -p["affiliate_rate"], p.get("rank") or 999))
    picked = usable[:count]
    today = dt.date.today().isoformat()
    for p in picked:
        p["selected_at"] = today
        # 手動確認が必要な商品は、確認するまで投稿生成に回さない
        p["active"] = p["threads_check"] == "pass"
    return picked


def run(rakuten: RakutenClient, threads: ThreadsClient | None, cfg: dict) -> list[dict]:
    candidates = step1_ranking(rakuten, cfg)
    step2_threads_check(threads, candidates, cfg)
    return step3_pick(candidates, cfg["pick_count"])
