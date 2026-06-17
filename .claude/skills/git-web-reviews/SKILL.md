---
name: git-web-reviews
description: >-
  git-web の diff view で付けたレビューコメント (.git-web/reviews/<sha>.jsonl) を読み取り、
  未解決の指摘を蒸留した一覧で返す。「(git-web の) レビューコメントに対応して」「diff のコメントを
  読んで直して」「コメントを反映して」等で使う。引数 from/to で対象リビジョン範囲を絞れる。
argument-hint: "[from] [to]"
context: fork
agent: general-purpose
---

# git-web レビューコメントの収集

ローカルツール **git-web** の diff view で付けたレビューコメントを読み取り、**未解決の指摘を
蒸留した一覧で返す** スキル。コメントはリポジトリ内に JSONL で保存され、git-web が起動して
いなくても読める。

このスキルは `context: fork` により**隔離サブコンテキストで実行**される。生 JSONL の列挙・
パース・resolved 畳み込みはこのサブコンテキスト内で行い、**呼び出し元 (メイン) には最終出力の
未解決コメント一覧だけを返す**ことでメインのコンテキストを汚さない。コードの対応 (修正) は
この一覧を受け取った呼び出し元が行う。

## 引数

- `$1` = `from`, `$2` = `to` (いずれも任意): diff view と同じリビジョン範囲。
  指定があれば `from..to` に含まれるコミットのコメントだけを対象にする。
- 省略時: `.git-web/reviews/` の全コメント (全コミット) を対象にする。

## 読み取りルール (固定フォーマット)

- 置き場: **メイン (ルート) worktree のトップレベル直下** `.git-web/reviews/`
  - メイン worktree ルート = `git rev-parse --path-format=absolute --git-common-dir` の親
- 本体: `<40桁commitSHA>.jsonl` — 1 行 1 コメントの JSONL (追記専用)
  `{"id","sha","path","newLineStart","newLineEnd","body","createdAt"}`
- 解決: `<40桁commitSHA>.resolved.jsonl` — `{"id","resolved","ts"}` の追記ログ。
  同一 `id` は **最後の行 (最新 ts) が勝ち**。
- 壊れた行 (JSON parse 不能) はスキップ。
- アンカー: 「コミット `sha` 時点の `path` の new 側 `newLineStart`〜`newLineEnd` 行
  (1-based, inclusive)」。

## 手順 (このサブコンテキスト内で実行)

1. メイン worktree ルートを求める:
   `root=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")`
   - `$root/.git-web/reviews/` が無ければ「未解決コメントなし」と返して終了
2. 対象コミット SHA を決める:
   - `from`/`to` 指定あり: `git rev-list <from>..<to>` ∪ `git rev-parse <to>` を、
     `$root/.git-web/reviews/` に実在する `<sha>.jsonl` と交差させる
   - 指定なし: `<sha>.jsonl` (`*.resolved.jsonl` を除く) の全 SHA
3. 各 `<sha>.jsonl` を 1 行ずつ JSON parse する (壊れ行はスキップ)
4. 各 `<sha>.resolved.jsonl` を id ごと最後勝ちで畳み込み、resolved を判定する
5. **未解決 (resolved が true でない) コメントだけ** を、次の蒸留形式の一覧で返す
   (生 JSONL は返さない):
   - `sha` (短縮可) / `path` / `L<start>-<end>` / `body` (原文) / `id`
   - 0 件なら「未解決コメントなし」と返す

## 禁止

- **`.git-web/reviews/` 配下のファイルを編集・追記・削除しない。resolved にもしない。**
  解決操作は git-web の UI からのみ行う仕様 (人間が UI で resolve)。本スキルは読むだけ。
- 補助スクリプトは使わない。列挙・畳み込みは上記手順に従って行う。

## 呼び出し元 (メイン) での扱い

返ってきた未解決コメント一覧をもとに、各コメントの該当コード箇所を特定して対応する。
行がずれている疑いがあれば `git show <sha>:<path>` で当時行を確認する。対応後も自分で
resolved にはせず、対応したコメント (id / path / 行 / 本文 / 対応概要) を報告して resolve は
人間に委ねる。
