# Threadsアフィリエイト自動化

楽天アフィリエイト × Threads で月10万円を目指すノウハウを、GitHub Actions で自動で回すためのツールです。

## 必要なのは Claude API のキーだけ
楽天とThreadsのAPIは使わなくても動きます。キーを登録すれば、その部分も自動になります。

| 作業 | 標準（APIなし） | 楽天・ThreadsのAPIがある場合 |
|---|---|---|
| 売れている商品を探す | Claudeがネット検索で、楽天ランキング・Amazon・@cosme・LIPS・ブログなどを横断して調べる | ＋ 楽天APIで料率とリンクを正確に確認する |
| 料率10%以上か | 楽天アフィリエイトでリンクを作るときに自分で確認する | 自動 |
| Threadsで「いいね100以上が10個以上」か | 出力された検索URLを開いて自分で確認する | 自動（Metaの審査が必要） |
| 投稿 | 毎朝「投稿シート」を作る → Threadsアプリの予約投稿に貼る | 自動投稿 |
| 成績（閲覧数・いいね数） | Threadsのインサイトを見て `data/posted.yaml` に書く | 自動取得 |

## ノウハウと機能の対応

| ノウハウ | 自動化の内容 | コマンド |
|---|---|---|
| 3-1 売れている商品を探す | Claudeがネットで調べ、複数の媒体で売れている商品を出典つきで集める（`data/research/`） | `research` |
| 3-2 Threadsで需要を確認する | 商品ごとにThreadsの検索URLを出す（APIがあれば自動で判定する） | `research` |
| 3-3 5商品に絞る | 需要の強い順に5商品を `data/products.yaml` に保存し、残っている確認事項を `todo` に書く | `research` |
| 4 AIを専門家として使う | リサーチで分かった「売れている理由」「買う人の悩み」を元に、Claudeが分析する | `generate` |
| 4 伸びている「型」で3本作る | `settings.yaml` の型で下書きを作る | `generate` |
| 4 自分の体験の一言を足す | 下書きに `{体験}` の枠を残す。**自分で書いた下書きだけが使われる** | 手作業（承認） |
| 5 毎日大量に投稿しない | 1日の上限を設定する（通常3本、セール重要日は6本） | `export` / `post` |
| 5 当たり投稿を「擦る」 | 閲覧数の多い投稿の、切り口を変えた版を作る | `generate` |
| 5 セールの前日・開始日・終了日 | 投稿数を増やし、セール向けの文面にする。`.ics` をカレンダーに読み込める | `export` / `calendar` |

## 毎日の流れ（標準のAPIなし方式）

```
月曜 9:00  research … Claudeがネットで調べて5商品を選ぶ
   あなた   … products.yaml の todo を確認する
              （Threads検索URLを開く／楽天アフィリエイトで料率を見てリンクを作る）
              → 問題なければ active: true にする
毎朝 6:00  generate … 下書きを作る → data/queue.yaml
           export   … 承認済みの下書きを data/投稿シート/今日の日付.md に書き出す
   あなた   … ① queue.yaml で、よい下書きを approved にして personal_note を書く（翌朝のシートに入る）
              ② 投稿シートの本文を、Threadsアプリの予約投稿に貼る。リプ欄にリンクを貼る
   ときどき … インサイトで見た閲覧数・いいね数を posted.yaml に書く → 当たり投稿は自動で擦る
```

## セットアップ

### 1. キーを用意する
| 名前 | 必要か | 取得先 |
|---|---|---|
| `ANTHROPIC_API_KEY` | **必須** | [Claude Console](https://platform.claude.com/) |
| `RAKUTEN_AFFILIATE_ID` | あると便利 | 楽天ウェブサービスまたは楽天アフィリエイトで確認できるID。あると、商品URLからアフィリエイトリンクを自動で作る（APIの呼び出しはしない） |
| `RAKUTEN_APP_ID` / `RAKUTEN_ACCESS_KEY` | 任意 | [楽天ウェブサービス](https://webservice.rakuten.co.jp/)。料率を自動で確認する |
| `THREADS_ACCESS_TOKEN` / `THREADS_USER_ID` | 任意 | [Meta for Developers](https://developers.facebook.com/)。使う場合は `settings.yaml` の `posting.mode` を `auto` にする |

> 自動で作ったアフィリエイトリンクは、最初の1本だけ、自分で踏んで楽天アフィリエイトのレポートに計上されるか確認してください。

### 2. GitHubに登録する
リポジトリの **Settings → Secrets and variables → Actions** に、用意したキーを登録します。

### 3. 設定する
`settings.yaml` で、ジャンル、読者像、型、投稿数、セール日程を編集します。
**セール日程は仮の値です。** 楽天の公式発表に合わせて更新してください。

### ローカルで動かす場合
```bash
pip install -r requirements.txt
cp .env.example .env   # キーを記入する
python -m threads_affiliate research
python -m threads_affiliate generate
python -m threads_affiliate status
python -m threads_affiliate export
```

## 守っていること
- **ブラウザを操作して投稿するボットは使いません。** 規約違反でアカウントが止まるリスクがあるためです。自動投稿は公式APIだけで行い、APIを使わない場合は予約投稿に自分で貼ります。
- **リサーチの数字には出典URLを付けます。** Claudeは読み違えることがあるため、`data/research/` のレポートで根拠を確認できます。
- **【PR】表記を必ず付けます（ステマ規制への対応）。** `pr_label` は消さないでください。
- **体験談をAIに作らせません。** 体験の一言は本人が書きます。`require_personal_note: true` のままにしておくと、一言のない下書きは投稿されません。
- **化粧品の効果を断定する表現などを避けるよう、AIに指示しています（薬機法・景品表示法）。** それでも、承認するときは自分の目で確認してください。
- **投稿数に上限を設け、擦った投稿も文面を変えます。** 大量投稿や同じ文面の繰り返しによるスパム判定を避けるためです。

## ファイル構成
```
settings.yaml            設定（ジャンル・型・投稿数・セール）
data/research/           Claudeのリサーチレポート（出典URLつき）
data/products.yaml       選んだ5商品と確認事項（todo）
data/投稿シート/          コピペ用の投稿シート（APIなし方式）
data/queue.yaml          下書きと承認キュー（ここを編集して承認する）
data/posted.yaml         投稿の履歴と成績
data/report.md           型ごとの成績と当たり投稿
data/sales.ics           セールのカレンダー
threads_affiliate/       プログラム本体
.github/workflows/       自動実行のスケジュール
```
