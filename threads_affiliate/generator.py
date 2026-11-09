"""ノウハウ4: AIを専門家として使い、伸びている「型」で投稿の下書きを作る."""
from __future__ import annotations

import json

import anthropic

NOTE_SLOT = "{体験}"

SYSTEM = f"""あなたはThreadsで楽天の商品を紹介するアフィリエイターの、文章アシスタントです。

書き方の方針:
- 専門家ぶらず、初心者が素直に調べて「これいいかも」と思った目線で書く。
- たった1人の読者の悩みに深く刺さる、具体的で生々しい一言を大事にする。
- 1投稿は500文字以内。改行を多めにして、スマホで読みやすくする。
- 本文に URL は書かない（リンクはリプ欄に置く）。ハッシュタグは0〜1個。
- 投稿者本人の体験は捏造しない。本人の体験の一言を入れる位置に、そのまま {NOTE_SLOT} と1行だけ書く。
- 誇大な表現、効果効能の断定、「必ず」「絶対」「治る」「シミが消える」などは書かない。
  化粧品・健康食品は薬機法、価格や比較は景品表示法に触れない範囲にとどめる。
- 「PR」の表記はシステムが自動で付けるので、本文には書かない。"""

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {
            "type": "object",
            "properties": {
                "target_worries": {"type": "array", "items": {"type": "string"}},
                "why_persona_chooses": {"type": "string"},
                "cautions": {"type": "string"},
            },
            "required": ["target_worries", "why_persona_chooses", "cautions"],
            "additionalProperties": False,
        },
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "template": {"type": "string"},
                    "text": {"type": "string"},
                    "personal_note_hint": {"type": "string"},
                },
                "required": ["template", "text", "personal_note_hint"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["analysis", "drafts"],
    "additionalProperties": False,
}


class Generator:
    def __init__(self, model: str, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.model = model

    def _call(self, prompt: str) -> dict:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
            # 安全フィルタで断られたときは、サーバー側で別モデルに切り替えて続ける
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claudeが生成を断りました。商品や指示を見直してください。")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("出力が途中で切れました。")
        text = "".join(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    def new_posts(self, product: dict, persona: str, templates: list[dict], count: int,
                  sale: str | None = None) -> dict:
        examples = product.get("threads_examples") or []
        prompt = f"""次の商品について、まず専門家として分析し、その後で投稿の下書きを{count}本作ってください。

# 読者像
{persona}

# 商品
- 商品名: {product['name']}
- 価格: {product.get('price')}円 / ショップ: {product.get('shop')}
- レビュー: {product.get('review_average')}（{product.get('review_count')}件）
- 商品説明（抜粋）: {product.get('caption') or '不明'}

# ネットのリサーチで分かったこと（出典つき）
- 売れている理由: {product.get('why_selling') or '不明'}
- 買っている人の悩み: {', '.join(product.get('target_worries') or []) or '不明'}
- 出典: {json.dumps(product.get('sources') or [], ensure_ascii=False)}

# 分析してほしいこと
- この商品はどんな悩みの人に向いているか
- 「{persona}」がこれを選ぶ理由
- 紹介するときに注意すべき表現

# 使う「型」（{count}本それぞれ別の型を使う。足りなければ同じ型で切り口を変える）
{json.dumps(templates, ensure_ascii=False, indent=2)}

# Threadsで実際に伸びている投稿（構成とテンポを参考にする。文章は丸写ししない）
{json.dumps(examples, ensure_ascii=False, indent=2) if examples else "（なし）"}
"""
        if sale:
            prompt += f"\n# 時期\n今は「{sale}」の時期です。ついで買いやポイントの話を自然に入れてください。\n"
        prompt += f"\npersonal_note_hint には、{NOTE_SLOT} の位置に本人が書くとよい体験の例を1つ書いてください。"
        return self._call(prompt)

    def reuse_posts(self, hit: dict, count: int) -> dict:
        """ノウハウ5: 過去の当たり投稿を「擦る」（切り口や最後の一言を変えて再利用）."""
        prompt = f"""次の投稿は、過去に大きく伸びた投稿です（閲覧 {hit.get('views')} / いいね {hit.get('likes')}）。
伸びた理由を分析し、同じ商品・同じ骨格のまま、
「冒頭の切り口」か「最後の一言」を変えたバリエーションを{count}本作ってください。
元の文章と同じ文は2文までにしてください（スパム判定を避けるため）。

# 元の投稿
{hit['text']}

# 商品
{hit.get('product_name')}

analysis.target_worries には伸びた理由を、why_persona_chooses には変えた切り口の説明を書いてください。
template には「擦り: <変えた点>」と書いてください。
"""
        return self._call(prompt)
