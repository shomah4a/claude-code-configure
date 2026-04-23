---
name: codex-second-opinion
description: Codexに意見を求める。mycodex execでプロンプトを実行し、結果をそのまま出力する。
allowed-tools: Bash, Read
---

# Codex Second Opinion

Codexに意見を求めるスキル。スキル引数をプロンプトとして `mycodex exec` に渡し、結果をそのまま出力する。

## 引数

スキル引数全体をCodexへのプロンプトとして使用する。引数が空の場合はユーザーに入力を求めること。

## 実行手順

1. 以下のコマンドを実行する:

```bash
mycodex exec "{スキル引数}"
```

`mycodex` がエイリアスとして解決できない場合は、以下のフルコマンドで実行すること:

```bash
codex -s danger-full-access -a on-request exec "{スキル引数}"
```

**注意**: Bashツールの `timeout` は `600000`（10分）に設定すること。Codexの応答には時間がかかる場合がある。

2. コマンドの出力をそのまま加工せずにユーザーに表示する。要約や編集は行わないこと。
