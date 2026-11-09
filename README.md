# Threadsアフィリエイト自動化

楽天アフィリエイト × Threads で月10万円を目指すノウハウを、GitHub Actions で自動で回すためのツールです。

## ノウハウと機能の対応

| ノウハウ | 自動化の内容 | コマンド |
|---|---|---|
| 3-1 楽天ランキング1〜30位、料率10%以上 | 楽天APIでランキングを取得し、料率で絞り込む | `research` |
| 3-2 Threadsで「いいね100以上が10個以上」を確認 | Threads APIのキーワード検索で判定する。判定できないときは検索URLを出す | `research` |
| 3-3 5商品に絞る | 需要と料率の順に5商品を `data/products.yaml` に保存する | `research` |
| 4 AIを専門家として使う | Claudeが「どんな悩みの人に向くか」「読者像が選ぶ理由」を分析する | `generate` |
| 4 伸びている「型」で3本作る | `settings.yaml` の型と、Threadsで伸びている実例を使って下書きを作る | `generate` |
| 4 自分の体験の一言を足す | 下書きに `{体験}` の枠を残す。**自分で書いた下書きだけが投稿される** | 手作業（承認） |
| 5 毎日大量に投稿しない | 1日の上限を設定する（通常3本、セール重要日は6本） | `post` |
| 5 当たり投稿を「擦る」 | 閲覧数の多い投稿を検出し、切り口や最後の一言を変えた版を作る | `analyze` → `generate` |
| 5 セールの前日・開始日・終了日 | その日は投稿枠を増やし、セール向けの文面にする。`.ics` をカレンダーに読み込める | `post` / `calendar` |

## 毎日の流れ

```
月曜 9:00  research … 5商品を選ぶ（👀の商品は自分で確認して active: true にする）
毎朝 6:00  generate … 下書きを作る → data/queue.yaml
   あなた   … スマホのGitHubアプリで queue.yaml を開き、
              よい下書きの status を approved に変え、personal_note に体験の一言を書く
7:30 / 12:15 / 21:00  post … 承認済みを1本ずつ投稿（本文に【PR】、リンクはリプ欄）
毎晩 0:00  analyze … 閲覧数・いいね数を取得 → data/report.md、当たり投稿は翌朝「擦る」
```

あなたの作業は、1日5〜10分の承認と一言を書くことだけです。

## セットアップ

### 1. 必要なアカウントとキー
| 名前 | 取得先 |
|---|---|
| `RAKUTEN_APP_ID` / `RAKUTEN_AFFILIATE_ID` | [楽天ウェブサービス](https://webservice.rakuten.co.jp/)でアプリを登録する |
| `RAKUTEN_ACCESS_KEY` | 楽天の新しいAPIでアクセスキーが求められる場合だけ |
| `ANTHROPIC_API_KEY` | [Claude Console](https://platform.claude.com/) |
| `THREADS_ACCESS_TOKEN` / `THREADS_USER_ID` | [Meta for Developers](https://developers.facebook.com/)でThreads APIのアプリを作り、長期アクセストークンを発行する。権限は `threads_basic`、`threads_content_publish`、`threads_manage_insights`、（リサーチ用に）`threads_keyword_search` |

> 他人の投稿をキーワード検索するには、`threads_keyword_search` 権限についてMetaの審査が必要です。審査が通るまでは、リサーチの手順2は「👀 要手動確認」になり、検索URLが出力されます。

### 2. GitHubに登録する
リポジトリの **Settings → Secrets and variables → Actions** に、上のキーを登録します。

### 3. 設定する
`settings.yaml` で、ジャンル、読者像、型、投稿数、セール日程を編集します。
**セール日程は仮の値です。** 楽天の公式発表に合わせて更新してください。

### 4. 動作確認
Actions タブで「3. 自動投稿」を手動で実行します。`dry_run` にチェックを入れておくと、実際には投稿せず、投稿内容だけを確認できます。

### ローカルで動かす場合
```bash
pip install -r requirements.txt
cp .env.example .env   # キーを記入する
python -m threads_affiliate research
python -m threads_affiliate generate
python -m threads_affiliate status
python -m threads_affiliate post --dry-run
```

## 守っていること
- **Threads公式APIだけを使います。** ブラウザ操作で投稿するボットは規約違反でアカウントが止まるリスクがあるため、使いません。
- **【PR】表記を必ず付けます（ステマ規制への対応）。** `pr_label` は消さないでください。
- **体験談をAIに作らせません。** 体験の一言は本人が書きます。`require_personal_note: true` のままにしておくと、一言のない下書きは投稿されません。
- **化粧品の効果を断定する表現などを避けるよう、AIに指示しています（薬機法・景品表示法）。** それでも、承認するときは自分の目で確認してください。
- **投稿数に上限を設け、擦った投稿も文面を変えます。** 大量投稿や同じ文面の繰り返しによるスパム判定を避けるためです。

## ファイル構成
```
settings.yaml            設定（ジャンル・型・投稿数・セール）
data/products.yaml       選んだ5商品
data/queue.yaml          下書きと承認キュー（ここを編集して承認する）
data/posted.yaml         投稿の履歴と成績
data/report.md           型ごとの成績と当たり投稿
data/sales.ics           セールのカレンダー
threads_affiliate/       プログラム本体
.github/workflows/       自動実行のスケジュール
```
