---
name: codex-review
description: Codexにコードレビューを依頼する。差分の対象（diff/branch/from:to）を引数で指定可能。
allowed-tools: Bash, Read, Write
---

# Codex Review

Codexにコードレビューを依頼するスキル。レビュー対象の差分を引数で指定し、`mycodex exec` でレビューを実行する。

## 引数

引数でレビュー対象を指定する。

| 引数 | 意味 |
|------|------|
| `diff`（デフォルト/引数なし） | 現在のワーキングツリーの差分 |
| `branch` | mainブランチとworktreeの差分 |
| `{from}:{to}` | 指定リビジョン間の差分（引数に `:` が含まれる場合） |

## 実行手順

### Step 1: プロンプトを組み立てる

引数に応じて以下のプロンプトを使用する:

- `diff` または引数なし:
  - `現在のワーキングツリーの差分をレビューしてください`
- `branch`:
  - `mainブランチとworktreeの差分をレビューしてください`
- `{from}:{to}` （`:` を含む引数）:
  - `{from}から{to}までの差分をレビューしてください`

### Step 2: Codexを実行する

```bash
mycodex exec "{組み立てたプロンプト}"
```

`mycodex` がエイリアスとして解決できない場合は、以下のフルコマンドで実行すること:

```bash
codex -s danger-full-access -a on-request exec "{組み立てたプロンプト}"
```

### Step 3: 結果を出力する

1. コマンドの出力全文を `.claude/tmp/YYYY-MM-DD_HH-MM_codex-review.md` に書き出す（`YYYY-MM-DD_HH-MM` は実行日時）。加工・要約せず、そのまま全文を書き出すこと。
2. コンソールにはレビュー結果の要約を出力する。
