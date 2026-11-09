"""Claudeのネット検索で、媒体を問わず「いま売れている商品」を調べる.

楽天やThreadsのAPIを使わない。Claude APIのサーバー側ツール（web_search / web_fetch）だけで、
楽天ランキングのページ、Amazon、@cosme、LIPS、ブログ、まとめ記事などを横断して調べる。
"""
from __future__ import annotations

import json
import urllib.parse

import anthropic

RESEARCH_SYSTEM = """あなたは、楽天アフィリエイトの商品リサーチを担当する調査員です。
ネット検索とページの取得を使って、媒体を問わず「いま実際に買われている商品」を探します。

守ること:
- 数字（順位、レビュー件数、価格など）は、実際に読んだページに書いてあるものだけを書く。
  見つからなければ「不明」と書き、推測で埋めない。
- 根拠ごとに、出典のURLを必ず書く。
- 1つの媒体だけでなく、複数の媒体で名前が挙がっている商品を高く評価する。
- 広告だけで押されている商品より、口コミやリピートの声がある商品を優先する。"""

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "search_keyword": {"type": "string"},
                    "rakuten_url": {"type": "string"},
                    "price": {"type": "string"},
                    "why_selling": {"type": "string"},
                    "target_worries": {"type": "array", "items": {"type": "string"}},
                    "media_count": {"type": "integer"},
                    "demand_score": {"type": "integer"},
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "media": {"type": "string"},
                                "url": {"type": "string"},
                                "note": {"type": "string"},
                            },
                            "required": ["media", "url", "note"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "search_keyword", "rakuten_url", "price", "why_selling",
                             "target_worries", "media_count", "demand_score", "sources"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def ranking_url(genre_id: int | str) -> str:
    return f"https://ranking.rakuten.co.jp/daily/{genre_id}/"


class WebResearcher:
    def __init__(self, model: str, client: anthropic.Anthropic | None = None,
                 max_searches: int = 15, max_fetches: int = 10, max_continuations: int = 5):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.tools = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches,
             "user_location": {"type": "approximate", "country": "JP", "timezone": "Asia/Tokyo"}},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_fetches},
        ]
        self.max_continuations = max_continuations

    # ---- 1回目: ネットで調べて、出典つきのレポートを書く ----
    def _investigate(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        for _ in range(self.max_continuations + 1):
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=64000,
                system=RESEARCH_SYSTEM,
                messages=messages,
                tools=self.tools,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
            if response.stop_reason == "pause_turn":
                # サーバー側の検索ループが上限に達しただけ。そのまま続きを頼む
                messages = [messages[0], {"role": "assistant", "content": response.content}]
                continue
            if response.stop_reason == "refusal":
                raise RuntimeError("Claudeがリサーチを断りました。ジャンルや指示を見直してください。")
            return "".join(b.text for b in response.content if b.type == "text")
        raise RuntimeError("ネット検索が長くなりすぎたため中断しました。")

    # ---- 2回目: レポートを決まった形のデータに整える ----
    def _to_candidates(self, report: str, count: int) -> list[dict]:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            messages=[{"role": "user", "content": f"""次のリサーチレポートから、商品候補を最大{count}件、データにしてください。
レポートに書かれていない情報は追加しないでください。不明な項目は空文字にしてください。
- search_keyword: Threadsで検索するときの短いキーワード（ブランド名＋商品の種類など、2〜3語）
- rakuten_url: レポート中の楽天市場の商品ページURL（item.rakuten.co.jp など）。無ければ空文字
- media_count: 名前が挙がっていた媒体の数
- demand_score: 1〜5（複数の媒体で売れている根拠が強いほど高い）

# レポート
{report}"""}],
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": CANDIDATE_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"候補の整理に失敗しました（{response.stop_reason}）")
        text = "".join(b.text for b in response.content if b.type == "text")
        return json.loads(text)["candidates"]

    def research(self, genre: dict, persona: str, count: int = 15) -> tuple[str, list[dict]]:
        prompt = f"""ジャンル「{genre['name']}」で、いま実際に売れている商品を調べてください。

# 調べ方
1. まず楽天市場のデイリーランキング {ranking_url(genre['id'])} を開き、1〜30位の商品を確認する
   （開けなければ、楽天ランキングを検索して最新のものを探す）。
2. 同じジャンルを、Amazonの売れ筋ランキング、@cosme、LIPS、ブログやまとめ記事、
   SNSの口コミを紹介している記事などでも調べ、複数の媒体で名前が挙がる商品を探す。
3. 候補ごとに、楽天市場で買える商品ページのURLを探す。
4. 「{persona}」が、どんな悩みでその商品を買っているのかを、口コミから読み取る。

# 書いてほしいレポート
候補を最大{count}件。候補ごとに、次の5点を書く。
- 商品名
- 楽天の商品ページのURL
- 価格
- 売れている根拠（媒体名、URL、そこに書いてあったこと）
- 買っている人の悩み
最後に、複数の媒体で根拠が強いものから順に並べ替えた一覧を付ける。"""
        report = self._investigate(prompt)
        return report, self._to_candidates(report, count)


def affiliate_link(item_url: str, affiliate_id: str) -> str:
    """楽天の商品URLとアフィリエイトIDから、アフィリエイトリンクを作る（APIは使わない）."""
    q = urllib.parse.quote(item_url, safe="")
    return f"https://hb.afl.rakuten.co.jp/hgc/{affiliate_id}/?pc={q}&m={q}"
