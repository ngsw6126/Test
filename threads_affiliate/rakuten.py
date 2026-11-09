"""楽天ウェブサービス（任意）: キーがあれば、ランキングと料率を正確に取れる."""
from __future__ import annotations

import os
import time

import requests

# 楽天のAPIはドメイン移行があり得るため、環境変数で上書きできるようにしている
BASE = os.environ.get("RAKUTEN_API_BASE", "https://app.rakuten.co.jp/services/api")
RANKING_URL = f"{BASE}/IchibaItem/Ranking/20220601"
SEARCH_URL = f"{BASE}/IchibaItem/Search/20220601"


class RakutenClient:
    def __init__(self, app_id: str, affiliate_id: str, access_key: str = "", session=None):
        self.app_id = app_id
        self.affiliate_id = affiliate_id
        self.access_key = access_key
        self.session = session or requests.Session()

    def _get(self, url: str, **params) -> dict:
        params = {"applicationId": self.app_id, "affiliateId": self.affiliate_id,
                  "format": "json", "formatVersion": 2, **params}
        if self.access_key:
            params["accessKey"] = self.access_key
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        time.sleep(1)  # 1秒1リクエストの目安を守る
        return resp.json()

    def ranking(self, genre_id: int, top_n: int = 30) -> list[dict]:
        """ジャンルのランキング上位を返す（1ページ30件）."""
        items: list[dict] = []
        page = 1
        while len(items) < top_n:
            data = self._get(RANKING_URL, genreId=genre_id, page=page)
            batch = [_unwrap(i) for i in data.get("Items", [])]
            if not batch:
                break
            items.extend(batch)
            page += 1
        return items[:top_n]

    def affiliate_rate(self, item_code: str) -> float | None:
        """ランキング結果に料率が無い場合、商品検索APIで補う."""
        data = self._get(SEARCH_URL, itemCode=item_code)
        items = [_unwrap(i) for i in data.get("Items", [])]
        if items and items[0].get("affiliateRate") is not None:
            return float(items[0]["affiliateRate"])
        return None


    def search(self, keyword: str = "", item_code: str = "") -> dict | None:
        """商品コードかキーワードで検索し、いちばん上の商品を返す（料率とアフィリエイトリンクの確認用）."""
        params = {"itemCode": item_code} if item_code else {"keyword": keyword[:100], "sort": "-reviewCount"}
        data = self._get(SEARCH_URL, hits=1, **params)
        items = [_unwrap(i) for i in data.get("Items", [])]
        return items[0] if items else None


def _unwrap(item: dict) -> dict:
    # formatVersion=1 だと {"Item": {...}} で包まれている
    return item.get("Item", item)


def to_product(item: dict, genre_name: str, rate: float) -> dict:
    return {
        "item_code": item.get("itemCode"),
        "name": item.get("itemName"),
        "price": item.get("itemPrice"),
        "shop": item.get("shopName"),
        "rank": item.get("rank"),
        "genre": genre_name,
        "affiliate_rate": rate,
        "affiliate_url": item.get("affiliateUrl") or item.get("itemUrl"),
        "caption": (item.get("itemCaption") or "")[:600],
        "review_average": item.get("reviewAverage"),
        "review_count": item.get("reviewCount"),
    }
