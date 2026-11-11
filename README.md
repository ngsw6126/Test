# Threadsアフィリエイト自動化

楽天アフィリエイト × Threads で月10万円を目指すノウハウを、GitHub Actions で自動で回すためのツールです。

## APIキーは不要です
調査と下書き作りは、**Claude Codeのスケジュールタスク**が担当します。決めた時刻にClaude Codeが起動し、`prompts/` の手順書に従って作業し、結果をこのリポジトリに保存します。使うのは、いまのClaudeのプランの利用枠です（Claude APIのキーは使いません）。

| いつ | 誰が | 何をする | 手順書 |
|---|---|---|---|
| 毎週月曜の朝 | Claude Code | ネットで売れ筋を調べて、5商品を選ぶ | `prompts/research.md` |
| 毎朝 | Claude Code | 下書きを作り、承認済みの下書きを今日の投稿シートに書き出す | `prompts/generate.md` |
| 毎日 | あなた | 下書きを承認して体験の一言を書き、投稿シートを予約投稿に貼る | — |

楽天とThreadsのAPIも使いません。キーを登録すれば、その部分だけ自動になります（下の「任意のAPI」を参照）。

## ノウハウと機能の対応

| ノウハウ | 自動化の内容 |
|---|---|
| 3-1 売れている商品を探す | Claude Codeがネットで、楽天ランキング・Amazon・@cosme・LIPS・ブログなどを横断して調べ、出典つきのレポートを `data/research/` に保存する |
| 3-2 Threadsで需要を確認する | 商品ごとにThreadsの検索URLを出す（ログインが必要なため、確認は人がやる） |
| 3-3 5商品に絞る | 需要の強い順に5商品を `data/products.yaml` に保存し、残っている確認事項を `todo` に書く |
| 4 AIを専門家として使う | Claude Codeが「どんな悩みの人に向くか」「読者像が選ぶ理由」を考えてから書く |
| 4 伸びている「型」で3本作る | `settings.yaml` の型で、1商品3本の下書きを作る |
| 4 自分の体験の一言を足す | 下書きに `{体験}` の枠を残す。**一言を書いた下書きだけが投稿シートに入る** |
| 5 毎日大量に投稿しない | 1日の上限を設定する（通常3本、セール重要日は6本） |
| 5 当たり投稿を「擦る」 | 閲覧数の多い投稿の、切り口を変えた版を作る |
| 5 セールの前日・開始日・終了日 | 投稿数を増やし、セール向けの文面にする。`data/sales.ics` をカレンダーに読み込める |

下書きは、プログラムが自動でチェックしてから受け付けます。チェックするのは、文字数、URLが入っていないか、体験の枠があるか、誇大・断定表現がないか、です。

## あなたの作業

### 商品の確認（週1回・15分）
`data/products.yaml` で、👀の商品（`todo` が残っている商品）を確認します。
- Threadsの検索URLを開き、いいね100以上の投稿が10個以上あれば `threads_check: confirmed` にする。無ければ `skip: true` にする
- 楽天アフィリエイトでリンクを作るときに料率を見て、`affiliate_rate` に書く（10未満なら `skip: true`）
- `affiliate_url` が空なら、作ったリンクを貼る

確認した内容は、翌週のリサーチで同じ商品が選ばれたときにも引き継がれます。

### 毎日（10分ほど）
1. `data/queue.yaml` で、よい下書きの `status` を `approved` にし、`personal_note` に体験の一言を書く（翌朝のシートに入る）
2. `data/投稿シート/今日の日付.md` の本文をThreadsアプリの予約投稿に貼り、投稿後にリプ欄へリンクを貼る
3. ときどき、インサイトで見た閲覧数といいね数を `data/posted.yaml` の `views` / `likes` に書く（当たり投稿は自動で擦られる）

## 設定
`settings.yaml` で、次の項目を編集します。
- 楽天アフィリエイトID（`rakuten.affiliate_id`）：書いておくと、リンクを自動で作ります。最初の1本だけ、自分で踏んでレポートに計上されるか確認してください
- ジャンル
- 読者像
- 型
- 投稿数
- セール日程：**仮の値です。** 楽天の公式発表に合わせて更新してください

## 任意のAPI
| キー | できること |
|---|---|
| `RAKUTEN_APP_ID` / `RAKUTEN_ACCESS_KEY` | 料率を自動で確認する |
| `THREADS_ACCESS_TOKEN` / `THREADS_USER_ID` | 自動投稿と成績の自動取得（`posting.mode` を `auto` にする。GitHub Actions の「3. 自動投稿」「4. 分析」で動く） |

## コマンド
```bash
pip install -r requirements.txt
python -m threads_affiliate status     # 確認事項と下書きの状況
python -m threads_affiliate calendar   # セールカレンダー
# 以下はスケジュールタスクが使う
python -m threads_affiliate import-products 候補.json
python -m threads_affiliate plan-drafts
python -m threads_affiliate import-drafts 下書き.json
python -m threads_affiliate export
```

## 守っていること
- **ブラウザを操作して投稿するボットは使いません。** 規約違反でアカウントが止まるリスクがあるためです。自動投稿は公式APIだけで行い、APIを使わない場合は予約投稿に自分で貼ります。
- **リサーチの数字には出典URLを付けます。** Claudeは読み違えることがあるため、`data/research/` のレポートで根拠を確認できます。
- **スケジュールタスクは `data/` だけを変更します。** 手順書でそう指示しています。
- **【PR】表記を必ず付けます（ステマ規制への対応）。** `pr_label` は消さないでください。
- **体験談をAIに作らせません。** 体験の一言は本人が書きます。`require_personal_note: true` のままにしておくと、一言のない下書きは投稿されません。
- **化粧品の効果を断定する表現などを避けるよう、AIに指示しています（薬機法・景品表示法）。** それでも、承認するときは自分の目で確認してください。
- **投稿数に上限を設け、擦った投稿も文面を変えます。** 大量投稿や同じ文面の繰り返しによるスパム判定を避けるためです。

## ファイル構成
```
settings.yaml            設定（ジャンル・型・投稿数・セール）
data/research/           Claudeのリサーチレポート（出典URLつき）
data/products.yaml       選んだ5商品と確認事項（todo）
data/投稿シート/          コピペ用の投稿シート
prompts/                 スケジュールタスクの手順書
data/queue.yaml          下書きと承認キュー（ここを編集して承認する）
data/posted.yaml         投稿の履歴と成績
data/report.md           型ごとの成績と当たり投稿
data/sales.ics           セールのカレンダー
threads_affiliate/       プログラム本体
.github/workflows/       自動実行のスケジュール
```
