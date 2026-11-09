"""Threads公式API（Meta）クライアント.

ブラウザ操作ではなく公式APIだけを使う（規約違反でアカウントが止まるのを避けるため）。
"""
from __future__ import annotations

import time
import urllib.parse

import requests

BASE = "https://graph.threads.net/v1.0"


class ThreadsClient:
    def __init__(self, access_token: str, user_id: str, session=None, publish_wait: float = 30):
        self.token = access_token
        self.user_id = user_id
        self.session = session or requests.Session()
        self.publish_wait = publish_wait

    def _req(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self.token
        resp = self.session.request(method, f"{BASE}/{path}", params=params, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"Threads API {resp.status_code}: {resp.text}")
        return resp.json()

    # ---- 投稿 ----
    def post_text(self, text: str, reply_to_id: str | None = None) -> str:
        """テキスト投稿を作成して公開し、投稿IDを返す."""
        params = {"media_type": "TEXT", "text": text}
        if reply_to_id:
            params["reply_to_id"] = reply_to_id
        container = self._req("POST", f"{self.user_id}/threads", **params)["id"]
        # Metaの推奨: コンテナ作成から公開まで少し待つ
        time.sleep(self.publish_wait)
        return self._req("POST", f"{self.user_id}/threads_publish", creation_id=container)["id"]

    def permalink(self, media_id: str) -> str | None:
        return self._req("GET", media_id, fields="permalink").get("permalink")

    # ---- 分析 ----
    def insights(self, media_id: str) -> dict:
        data = self._req("GET", f"{media_id}/insights",
                         metric="views,likes,replies,reposts,quotes")
        result = {}
        for m in data.get("data", []):
            if "total_value" in m:
                result[m["name"]] = m["total_value"].get("value", 0)
            else:
                values = m.get("values") or [{}]
                result[m["name"]] = values[-1].get("value", 0)
        return result

    # ---- リサーチ ----
    def keyword_search(self, query: str, limit: int = 50) -> list[dict]:
        """人気順のキーワード検索.

        他人の投稿を検索するには、アプリに threads_keyword_search 権限（Metaの審査）が必要。
        いいね数は返らないことがあるため、呼び出し側で手動確認に切り替える。
        """
        data = self._req("GET", "keyword_search", q=query, search_type="TOP", limit=limit,
                         fields="id,text,permalink,timestamp,like_count")
        return data.get("data", [])


def search_url(query: str) -> str:
    return "https://www.threads.com/search?q=" + urllib.parse.quote(query) + "&serp_type=default"
