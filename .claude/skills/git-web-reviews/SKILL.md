---
name: git-web-reviews
description: >-
  git-web の diff view で付けたレビューコメント (.git-web/reviews/<sha>.jsonl) を読み取り、
  未解決の指摘を現在行へ翻訳し outdated を分離した一覧で返す。「(git-web の) レビューコメントに
  対応して」「diff のコメントを読んで直して」「コメントを反映して」等で使う。引数なしなら今の
  ブランチの差分 (base..HEAD) が対象。引数 from/to で対象リビジョン範囲を明示できる。
argument-hint: '[from] [to]'
context: fork
agent: general-purpose
---

# git-web レビューコメントの収集

ローカルツール **git-web** の diff view で付けたレビューコメントを読み取り、**未解決の指摘を
現在行へ翻訳し、outdated を分離した一覧で返す** スキル。コメントはリポジトリ内に JSONL で
保存され、git-web が起動していなくても読める。

このスキルは `context: fork` により**隔離サブコンテキストで実行**される。生 JSONL の列挙・
パース・resolved 畳み込み・行翻訳は同梱スクリプトがサブコンテキスト内で行い、**呼び出し元
(メイン) には最終出力 (live / outdated / 追従不可 に分類済みの一覧) だけを返す**ことでメインの
コンテキストを汚さない。コードの対応 (修正) はこの一覧を受け取った呼び出し元が行う。

## 引数

- `$1` = `from`, `$2` = `to` (いずれも任意): diff view と同じリビジョン範囲。
  指定があれば `from..to` に含まれるコミットのコメントだけを対象にする。
- 省略時 (デフォルト): **今のブランチの差分** = 既定ブランチとの `merge-base..HEAD` に
  含まれるコミットのコメントを対象にする。ベースブランチ上などで差分が無ければ
  「未解決コメントなし」を返す。

## 翻訳ターゲット (outdated 判定の基準)

各コメントは付けた時点の commitSHA 座標系でアンカーされる。これを **target リビジョン** の
行へ翻訳して現在行と outdated を判定する。

- `to` 引数があれば target = `to`。無ければ target = `HEAD` (引数優先)。
- target は git-web 本体 (ADR 0060) と同じく `git diff commentSHA..target` の hunk 走査で
  行翻訳する。アンカー行が削除されていれば **outdated**、target に path が無ければ **追従不可**。

## 実行手順 (このサブコンテキスト内で実行)

同梱スクリプトを 1 回実行し、その標準出力をそのまま呼び出し元へ返す。スクリプトが
列挙・畳み込み・翻訳・分類をすべて行う。

```
python3 ${CLAUDE_SKILL_DIR}/translate-reviews.py $1 $2
```

- 引数なしならデフォルト (今のブランチの差分, target=HEAD)。
- スクリプトは標準出力に分類済み一覧 (下記フォーマット) を、標準エラーに壊れ行の warn を出す。
- 標準出力を加工・要約せずそのまま返す。0 件なら「未解決コメントなし」。

## 出力フォーマット (スクリプトが生成)

```
## 未解決コメント (live)
- [id] <path> L<target開始>-<target終了> ← <commentSHA短> L<元開始>-<元終了>
  <body 原文>

## outdated (アンカー先が commentSHA..target で変更/削除済み — 参考)
- [id] <path> <commentSHA短> L<元開始>-<元終了> ※翻訳不能 (行が削除)
  <body 原文>

## 追従不可 (target に path が存在しない / rename — 範囲外)
- [id] <path> <commentSHA短> L<元開始>-<元終了> ※path 消失
  <body 原文>
```

- live: 行が現在も生きているコメント。`←` の左が target 行、右が元 (commentSHA) 行 (併記)。
- outdated: アンカー行が commentSHA..target で削除/変更されたコメント。**除外せず参考表示**。
  「コードが変わった」シグナルであって「指摘が解決済み」の証明ではない。
- 追従不可: target に path が無い (削除/rename)。rename 追従は範囲外 (ADR 0060 Tier3)。
- 空のセクションは省略。全件 0 なら「未解決コメントなし」。

## 禁止

- **`.git-web/reviews/` 配下のファイルを編集・追記・削除しない。resolved にもしない。**
  解決操作は git-web の UI からのみ行う仕様 (人間が UI で resolve)。本スキルは読むだけ。
- `translate-reviews.py` は git-web 本体ロジックの移植であり、本体を更新したら追従すること:
  - 行翻訳 (`translate_new_line` / `translate_range`) = `packages/front/src/diff/translate-line.ts`
  - resolved 畳み込み (`read_resolved`) = `packages/api/src/domain/review.ts` の `foldResolved`
    (append 出現順の後勝ち)

## 呼び出し元 (メイン) での扱い

返ってきた一覧をもとに対応する。

- **live**: `←` 左の target 行が現在のコード上の行。まずそこを見る。行が疑わしければ
  `git show <commentSHA>:<path>` で当時行を確認する。
- **outdated**: アンカー先が変わっている。既に対応済みの可能性があるが、指摘内容が別箇所に
  生きていることもある。body を読んで現在のコードに該当が残っていないか確認する。
- **追従不可**: rename/削除でアンカーを失っている。body と元 path から手動で対応箇所を探す。

対応後も自分で resolved にはせず、対応したコメント (id / path / 行 / 本文 / 対応概要) を
報告して resolve は人間に委ねる。
