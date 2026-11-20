# GitHub CLI MCP Proxy Server

Model Context Protocol (MCP) サーバーとして動作し、GitHub CLI (gh) / git コマンドへの操作を提供します。

## 概要

このサーバーは、Claude CodeなどのMCPクライアントに対して、GitHubのreadonly操作と、
一部の書き込み操作（git push、Pull Request作成、デフォルトブランチのマージ / 同期、GitHub Projects へのアイテム追加とフィールド値の更新）を提供します。
ホスト側でサーバーを起動し、Dockerコンテナで動作するClaude Codeから HTTP経由でアクセスすることで、
認証情報を渡すことなくGitHubを操作できます。

## 必要要件

- Python 3.8 以上（標準ライブラリのみ使用）
- GitHub CLI (gh) 2.97.0 以上
  - gh_run_job_log は `gh api` の `--allow-escape-sequences` を使います。このフラグは 2.97.0 で追加されました。
    2.97.0 未満では未知のフラグとして gh がエラー終了し、gh_run_job_log はログを取得できません（他のツールには影響しません）
  - gh_project_item_edit は `gh project item-edit` の `--url` / `--field` / `--value` を使います。これらのフラグは 2.97.0 で追加されました
  - gh_issue_view の parent / subIssues / subIssuesSummary / blockedBy / blocking は 2.94.0 で追加されたフィールドです
  - gh_pr_view の closingIssuesReferences は 2.72.0、gh_issue_view の closedByPullRequestsReferences は 2.73.0 で追加されたフィールドです
  - 2.94.0 未満のバージョンでは未知の JSON フィールド指定として gh がエラー終了し、gh_issue_view / gh_pr_view は基本フィールドも含めて取得できません
- GitHub Enterprise Server を利用する場合は 3.19 以上（sub-issue は 3.17 以上、blockedBy / blocking は 3.19 以上が必要）
- git（git_push / git_merge_default_branch を利用する場合）
  - git_sync_default_branch を利用する場合は git 2.23 以上（`git switch` を使用します）
- GitHub認証済みの環境（`gh auth status` で確認可能）

## セットアップ

### 1. GitHub CLIのインストール

```bash
# macOS
brew install gh

# Ubuntu/Debian
# ディストリビューション標準の apt リポジトリの gh は古いバージョン（Ubuntu 24.04 では 2.4.0）のため、
# GitHub 公式の apt リポジトリを利用します
# 手順: https://github.com/cli/cli/blob/trunk/docs/install_linux.md

# その他のOSについては https://cli.github.com/ を参照
```

インストール後、`gh --version` で 2.97.0 以上であることを確認してください。

### 2. GitHub認証

```bash
gh auth login
```

readonly ツールのみ利用する場合、認証トークンのスコープは `repo:read` または `public_repo` のみに制限することを推奨します。
gh_pr_create を利用する場合は、対象リポジトリへの書き込みが可能なスコープ（`repo` 等）が必要です。
gh_project_ で始まるツールを利用する場合は `project` スコープが必要です。`gh auth login` の既定のスコープには含まれないため、
`gh auth refresh -s project` で追加してください。スコープがない場合、gh は
`error: your authentication token is missing required scopes [read:project]` のエラーを返します。
git_push は gh を利用せず、ホスト側の git の認証設定（credential helper 等）で push します。

### 3. サーバーの起動

```bash
# デフォルトポート（30721）、デフォルトタイムアウト（30秒）で起動
python3 tools/gh-proxy/gh-proxy.py

# カスタムポートで起動
GH_PROXY_PORT=30800 python3 tools/gh-proxy/gh-proxy.py

# カスタムタイムアウト（60秒）で起動
GH_PROXY_TIMEOUT=60 python3 tools/gh-proxy/gh-proxy.py

# ポートとタイムアウトの両方をカスタマイズ
GH_PROXY_PORT=30800 GH_PROXY_TIMEOUT=60 python3 tools/gh-proxy/gh-proxy.py
```

サーバーはすべてのネットワークインターフェースで待ち受けます（bind制限や認証はありません）。

サーバーは起動時のコードで動作し続けるため、gh-proxy.py を更新した場合はサーバーを再起動してください。
Dockerコンテナからは `host.docker.internal` 経由でアクセス可能です。

## Claude Codeとの連携

### Claude Code の設定

Claude Codeの `settings.json` に以下の設定を追加します：

```json
{
  "mcpServers": {
    "gh-proxy": {
      "url": "http://host.docker.internal:30721"
    }
  }
}
```

Docker環境の場合、`host.docker.internal` を使用することでホスト側のサーバーにアクセスできます。

## 提供されるツール

### 1. gh_repo_view

指定されたGitHubリポジトリの情報を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名

**例:**
```json
{
  "name": "gh_repo_view",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python"
  }
}
```

### 2. gh_pr_list

指定されたリポジトリのPull Request一覧を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `state` (任意): 取得するPRの状態。未指定時は `open`
  - `open`: オープン中のPRのみ
  - `closed`: クローズ済みのPR（マージ済みを含む）
  - `merged`: マージ済みのPRのみ
  - `all`: すべてのPR
- `limit` (任意): 取得する最大件数（1-100）
- `search` (任意): 検索クエリ（例: `created:>2024-01-01`, `updated:<2024-06-01`）

**返却フィールド:**
`gh pr list --json` の出力のうち `labels` をラベル名の文字列配列に変換して返します。
`labels` 以外のフィールドは gh の出力と同じ値です（再シリアライズするため空白や改行の表記は異なる場合があります）。

- `number`, `title`, `state`, `author`, `createdAt`, `updatedAt`
- `labels`: PRに付与されたラベル名の配列（例: `["bug", "enhancement"]`）。
  1件のPRにつき最大100件まで取得します。切り詰められているかどうかは出力から判別できません。
  ラベルの説明や色が必要な場合は gh_label_list を利用してください
- `headRefName`: PR の元ブランチ名
- `isCrossRepository`: fork から作られた PR の場合 `true`。
  fork 由来の PR の `headRefName` は fork 側のブランチ名であり、元リポジトリの同名ブランチとは別物です

**例:**
```json
{
  "name": "gh_pr_list",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "state": "open",
    "limit": 10
  }
}
```

### 3. gh_pr_view

指定されたPull Requestの詳細情報を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `number` (必須): PR番号

**返却フィールド:**
`gh pr view --json` の出力をそのまま返します。

- `number`, `title`, `body`, `state`, `author`, `createdAt`, `updatedAt`, `mergeable`, `mergedAt`
- `labels`: PRに付与されたラベル。`[{ "id", "name", "description", "color" }]` のフラットな配列。
  1件のPRにつき最大100件まで取得します。`totalCount` は付かないため、100件を超えて切り詰められているかどうかは出力から判別できません
- `closingIssuesReferences`: このPRがクローズ対象とするIssue（development リンクまたは本文の closing keyword で紐付けられたもの）。
  `[{ "id", "number", "url", "repository": { "id", "name", "owner": { "id", "login" } } }]` のフラットな配列。
  `title` と `state` は含まれないため、必要な場合は `number` を用いて gh_issue_view を追加実行してください。
  `totalCount` や次ページの有無を示す情報は付かないため、紐付け件数が多い場合に切り詰められているかどうかは出力から判別できません

本文中の `#123` 形式の単なる言及や timeline の cross-reference は含まれません（他の issue / Pull Request からの言及は gh_issue_references で取得できます）。

**例:**
```json
{
  "name": "gh_pr_view",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "number": 123
  }
}
```

### 4. gh_issue_list

指定されたリポジトリのIssue一覧を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `state` (任意): Issueの状態 (`open`, `closed`, `all`)
- `limit` (任意): 取得する最大件数（1-100）
- `search` (任意): 検索クエリ（例: `created:>2024-01-01`, `updated:<2024-06-01`）

**返却フィールド:**
`gh issue list --json` の出力のうち `labels` をラベル名の文字列配列に変換して返します。
`labels` 以外のフィールドは gh の出力と同じ値です（再シリアライズするため空白や改行の表記は異なる場合があります）。

- `number`, `title`, `state`, `author`, `createdAt`, `updatedAt`
- `labels`: Issueに付与されたラベル名の配列（例: `["bug", "enhancement"]`）。
  1件のIssueにつき最大100件まで取得します。切り詰められているかどうかは出力から判別できません。
  ラベルの説明や色が必要な場合は gh_label_list を利用してください

**例:**
```json
{
  "name": "gh_issue_list",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "state": "open",
    "limit": 10
  }
}
```

### 5. gh_issue_view

指定されたIssueの詳細情報を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `number` (必須): Issue番号

**返却フィールド:**
`gh issue view --json` の出力をそのまま返します。

- `number`, `title`, `body`, `state`, `author`, `createdAt`, `updatedAt`
- `stateReason`: Issueが現在の状態になった理由。`COMPLETED` / `NOT_PLANNED` / `DUPLICATE` / `REOPENED` のいずれか。
  理由が記録されていない場合 (一度もクローズされていない open の Issue など) は空文字になります
  （gh CLI がこのフィールドを文字列型で扱うため、GraphQL の null は空文字として出力されます）。
  `DUPLICATE` の場合も重複先の Issue は含まれません（gh の `--json` で取得できないため）。
  GitHub Enterprise Server ではバージョンにより `DUPLICATE` が返らず `NOT_PLANNED` となる可能性があります
- `labels`: Issueに付与されたラベル。`[{ "id", "name", "description", "color" }]` のフラットな配列。
  1件のIssueにつき最大100件まで取得します。`totalCount` は付かないため、100件を超えて切り詰められているかどうかは出力から判別できません
- `closedByPullRequestsReferences`: このIssueをクローズする（した）PR。
  `[{ "id", "number", "url", "repository": { "id", "name", "owner": { "id", "login" } } }]` のフラットな配列。
  `title` と `state` は含まれないため、必要な場合は `number` を用いて gh_pr_view を追加実行してください。
  `totalCount` や次ページの有無を示す情報は付かないため、紐付け件数が多い場合に切り詰められているかどうかは出力から判別できません
- `parent`: 親Issue
- `subIssues`: サブIssueの一覧
- `subIssuesSummary`: サブIssueの件数サマリー（total / completed / percentCompleted）
- `blockedBy`: このIssueをブロックしているIssue
- `blocking`: このIssueがブロックしているIssue

`subIssues`, `blockedBy`, `blocking` は `{ "nodes": [...], "totalCount": N }` の形状です。
各ノードおよび `parent` には `id`, `number`, `title`, `url`, `state`, `repository.nameWithOwner` が含まれます。
サブIssueが多いIssueではレスポンスサイズが大きくなります。
本文中の `#123` 形式の単なる言及や timeline の cross-reference は含まれません（他の issue / Pull Request からの言及は gh_issue_references で取得できます）。

**例:**
```json
{
  "name": "gh_issue_view",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "number": 456
  }
}
```

### 6. gh_pr_comments

指定されたPull Requestのコメント一覧を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `number` (必須): PR番号

**例:**
```json
{
  "name": "gh_pr_comments",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "number": 123
  }
}
```

### 7. gh_issue_comments

指定されたIssueのコメント一覧を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `number` (必須): Issue番号

**例:**
```json
{
  "name": "gh_issue_comments",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "number": 456
  }
}
```

### 8. gh_pr_create

指定されたリポジトリにPull Requestを作成します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `branch` (必須): head となるブランチ名
- `title` (必須): Pull Request のタイトル（空文字不可）
- `body` (必須): Pull Request の本文（空文字可）
- `base` (任意): base となるブランチ名。省略時はリポジトリのデフォルトブランチを使用

`gh api repos/{owner}/{repo}/pulls` への POST で作成するため、git リポジトリ文脈に依存せず非対話で動作します。
head となるブランチは事前に push されている必要があります。

**例:**
```json
{
  "name": "gh_pr_create",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "branch": "feature/new-api",
    "title": "新APIの追加",
    "body": "変更内容の説明"
  }
}
```

### 9. git_push

クローン済みリポジトリのブランチを origin へ push します。

**引数:**
- `path` (必須): クローン済みリポジトリルートの絶対パス（サーバーが動作するホスト側ファイルシステム上のパス）
- `branch` (必須): push するブランチ名

remote は `origin` 固定です。force push、ブランチ削除、その他のオプション指定はできません。

push 先ブランチに制限はなく、デフォルトブランチ（main 等）へも push できます。
デフォルトブランチの保護が必要な場合は、リポジトリ側の branch protection / ruleset で制限してください。

git_push は gh を利用せず git のみで動作するため、GitHub 認証は不要です。
origin が GitHub 以外の remote であっても push を実行します。

**例:**
```json
{
  "name": "git_push",
  "arguments": {
    "path": "/home/user/work/my-repo",
    "branch": "feature/new-api"
  }
}
```

### 10. git_merge_default_branch

クローン済みリポジトリで `git fetch --all --prune --no-prune-tags` を実行し、origin のデフォルトブランチを現在チェックアウトされているブランチへ `--no-edit` でマージします。

**引数:**
- `path` (必須): クローン済みリポジトリルートの絶対パス（サーバーが動作するホスト側ファイルシステム上のパス）

fetch は `--prune` 付きで実行するため、remote で削除されたブランチの remote-tracking ref（`refs/remotes/<remote>/*`）は削除されます。
削除されるのは remote-tracking ref のみで、ローカルブランチは削除しません。
tag は `fetch.pruneTags` 設定の有無によらず削除しないよう `--no-prune-tags` を明示しています。
削除された remote-tracking ref はその reflog ごと消えるため、ローカルブランチから辿れないコミットは復元できなくなる場合があります。
`--all` を指定しているため、origin 以外の remote も prune の対象になります。

デフォルトブランチは対象リポジトリを作業ディレクトリとして `gh repo view` で判定します。
判定できない場合（GitHub 以外の remote、gh 未認証等）はマージを実行しません（fail-closed）。
このため利用には gh と GitHub 認証が必要です。

マージがコンフリクトで失敗した場合は `git merge --abort` で自動的に巻き戻し、
コンフリクト内容を含むエラーを返します。abort にも失敗した場合はリポジトリがマージ途中状態のまま残ります。

リポジトリが既にマージ途中状態（MERGE_HEAD が存在）の場合は、ツール外で開始された
マージを破棄しないよう、何も実行せずエラーを返します（fail-closed）。

**注意事項:**
- マージ前に作業ツリーが clean であるかの検証は行いません。未コミット変更がある状態で実行すると
  マージ結果と未コミット変更が混在し、コンフリクト時の abort による復元が不完全になる可能性があります
- fetch または merge がタイムアウト（デフォルト30秒）で中断された場合、リポジトリが中間状態
  （MERGE_HEAD 残留等）になる可能性があります。大きいリポジトリでは `GH_PROXY_TIMEOUT` の延長を検討してください
- detached HEAD 状態での実行は検証していません

**例:**
```json
{
  "name": "git_merge_default_branch",
  "arguments": {
    "path": "/home/user/work/my-repo"
  }
}
```

### 11. git_sync_default_branch

クローン済みリポジトリの**メインワークツリー**を origin のデフォルトブランチへ切り替え、
`git merge --ff-only` で origin の内容に同期します。

**引数:**
- `path` (必須): git リポジトリのワークツリールートの絶対パス（サーバーが動作するホスト側ファイルシステム上のパス）。
  リンクワークツリーのパスでもよい

`path` にリンクワークツリーを指定した場合も、`git worktree list --porcelain` の先頭エントリから
メインワークツリーを解決し、**指定パスとは別のディレクトリを書き換えます**。
リポジトリのサブディレクトリを指定した場合は `.git` が存在しないためエラーになります。

デフォルトブランチは対象リポジトリを作業ディレクトリとして `gh repo view` で判定します。
判定できない場合（GitHub 以外の remote、gh 未認証等）は同期を実行しません（fail-closed）。
remote は `origin` 固定です。gh の base repo 解決が `upstream` 等 origin 以外を向いている
fork 運用では、判定されたブランチ名と origin の内容が食い違う余地があります。

同期の前に `git fetch --prune --no-prune-tags origin` を実行します。

fetch は `--prune` 付きで実行するため、origin で削除されたブランチの remote-tracking ref（`refs/remotes/origin/*`）は削除されます。
削除されるのは remote-tracking ref のみで、ローカルブランチは削除しません。
tag は `fetch.pruneTags` 設定の有無によらず削除しないよう `--no-prune-tags` を明示しています。
削除された remote-tracking ref はその reflog ごと消えるため、ローカルブランチから辿れないコミットは復元できなくなる場合があります。
上記の fork 運用で origin に判定されたブランチが存在しない場合、古い `refs/remotes/origin/<デフォルトブランチ>` も prune で削除されるため、
古い ref で同期を進めることはなく後述の検証で中断します。

**ブランチを切り替える前に以下を検証し、該当する場合は何も変更せず中断します（fail-closed）:**
- git の操作（マージ / リベース / cherry-pick / revert / bisect）が進行中
- メインワークツリーが detached HEAD
- 追跡対象ファイルに未コミットの変更がある（untracked ファイルのみの場合は無視して続行します）
- デフォルトブランチが別のワークツリーでチェックアウト済み
- メインワークツリーが bare リポジトリ
- `refs/remotes/origin/<デフォルトブランチ>` が存在しない
- ローカルのデフォルトブランチが `origin/<デフォルトブランチ>` の祖先でない
  （未 push のコミットがある、または歴史が分岐している）

ブランチ切り替えには `git checkout` ではなく `git switch` を使い、切り替え後に HEAD が
目的のブランチを指していることを確認してからマージします。

成功時は、解決されたメインワークツリーの絶対パス・元のブランチ名と SHA・同期後の SHA を返します。

**注意事項:**
- 祖先関係を検証していても、マージは失敗することがあります（origin 側で追加されたファイルと同名の
  untracked ファイルが存在する場合、index.lock 競合、パーミッション、sparse-checkout、
  post-checkout / post-merge フックの失敗、タイムアウト）。この場合は元のブランチへ戻した上で
  エラーを返します。戻す操作自体が失敗した場合は、デフォルトブランチをチェックアウトした状態のまま残ります
- 未コミット変更の検証と実際の切り替えの間に別プロセスがワークツリーを変更した場合、
  衝突しない変更はブランチをまたいで持ち越されます。実行中は対象リポジトリを操作しないでください
- fetch / switch / merge がタイムアウト（デフォルト30秒）で中断された場合、`index.lock` の残留や
  作業ツリーの部分更新が起こる可能性があります。大きいリポジトリでは `GH_PROXY_TIMEOUT` の延長を検討してください
- `--untracked-files=no` は submodule の dirty 判定を抑制しないため、submodule が dirty なだけで中断します
- `--recurse-submodules` を付けないため、ブランチ切り替え後も submodule のワークツリーは旧 commit のまま残ります
- ワークツリーのパスに改行を含む場合、`git worktree list --porcelain` の出力を正しく解釈できません
- `git switch` を使うため git 2.23 以上が必要です

**例:**
```json
{
  "name": "git_sync_default_branch",
  "arguments": {
    "path": "/home/user/work/my-repo/.claude/worktrees/feature"
  }
}
```

### 12. gh_label_list

指定されたリポジトリのラベル一覧を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `limit` (任意): 取得する最大件数（1-100）。未指定時は100
- `search` (任意): ラベル名と説明を対象とする検索文字列

**返却フィールド:**
`gh label list --json` の出力をそのまま返します。0件の場合、gh は何も出力しないため空配列 `[]` を返します。

- `color`, `createdAt`, `description`, `id`, `isDefault`, `name`, `updatedAt`, `url`

総件数は返らないため、ラベルが `limit` を超えて存在する場合に切り詰められているかどうかは出力から判別できません。
`limit` の上限が100のため、101件以上のラベルを持つリポジトリでは全件を取得できません。

**例:**
```json
{
  "name": "gh_label_list",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "search": "bug"
  }
}
```

### 13. gh_pr_run_list

指定されたPull Requestのheadブランチに対する GitHub Actions の workflow run 一覧を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `number` (必須): PR番号
- `limit` (任意): 取得する最大件数（1-100）。未指定時は20
- `workflow` (任意): 絞り込む workflow の名前またはファイル名（例: `CI`, `ci.yml`）

`gh run list` には PR 番号で絞り込む手段がないため、`gh pr view` で PR の head ブランチ名を取得し、
`gh run list --branch` でそのブランチの run を取得します（gh を2回実行します）。

**返却フィールド:**
- `pullRequest`: PR の head ブランチ情報
  - `headRefName`: PR の元ブランチ名
  - `headRefOid`: PR の現在の head コミットの SHA
  - `isCrossRepository`: fork から作られた PR の場合 `true`
- `runs`: `gh run list --json` の出力（run の配列）。0件の場合は空配列
  - `attempt`, `conclusion`, `createdAt`, `databaseId`, `displayTitle`, `event`, `headBranch`, `headSha`,
    `name`, `number`, `startedAt`, `status`, `updatedAt`, `url`, `workflowDatabaseId`, `workflowName`
  - `databaseId` が run ID です

**注意事項:**
- ブランチ名で絞り込むため、PR の現在の head コミット以外に対する run も含まれます。
  現在の head コミットに対する run かどうかは、run の `headSha` と `pullRequest.headRefOid` を比較して判別してください
  - 過去の push に対する run
  - 同じブランチの `push` イベントなど、`pull_request` 以外のイベントで起動した run
  - fork 由来の PR（`isCrossRepository` が `true`）の場合、元リポジトリの同名ブランチに対する run
- `pull_request_target` や `workflow_run` など、base 側のブランチに紐づいて起動する run は含まれない可能性があります（未検証）
- 総件数は返らないため、run が `limit` を超えて存在する場合に切り詰められているかどうかは出力から判別できません
- gh を2回実行するため、タイムアウトまでの時間は最大で `GH_PROXY_TIMEOUT` の2倍になります

**例:**
```json
{
  "name": "gh_pr_run_list",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "number": 123,
    "limit": 10
  }
}
```

### 14. gh_run_job_list

指定された GitHub Actions の workflow run の job と step の一覧を取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `run_id` (必須): workflow run の ID。gh_pr_run_list の `runs` の `databaseId` から得られます

`gh api repos/{owner}/{repo}/actions/runs/{run_id}/jobs` の出力から、以下のフィールドを取り出して返します。
REST API の出力を利用するため、フィールド名は snake_case です（gh_pr_run_list などの camelCase とは異なります）。

**返却フィールド:**
- `total_count`: run の job の総数
- `jobs`: job の配列
  - `id`: job ID。gh_run_job_log の `job_id` に指定します
  - `name`, `status`, `conclusion`, `started_at`, `completed_at`, `html_url`
  - `steps`: step の配列。skipped の job では空配列になります
    - `number`: step 番号。gh_run_job_log の `step_number` に指定します。連番とは限りません（例: 8 の次が 14）
    - `name`, `status`, `conclusion`, `started_at`, `completed_at`

**注意事項:**
- run の最新の attempt の job を返します。再実行前の attempt の job は含まれません
- 取得するのは先頭100件までです。`total_count` が `jobs` の件数より大きい場合は切り詰められています。
  続きを取得する手段はありません

**例:**
```json
{
  "name": "gh_run_job_list",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "run_id": 44665981074
  }
}
```

### 15. gh_run_job_log

指定された GitHub Actions の job のログを、行単位で取得します。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `job_id` (必須): job の ID。gh_run_job_list の `jobs` の `id` から得られます
- `step_number` (任意): step 番号。gh_run_job_list の `steps` の `number` を指定します。指定時はその step のログのみを取得します
- `offset` (任意): 先頭からの行オフセット（0以上）。未指定時は0
- `limit` (任意): 返す最大行数（1-2000）。未指定時は500
- `tail` (任意): 末尾から返す行数（1-2000）。`offset` / `limit` と同時に指定するとエラーになります

`gh api --allow-escape-sequences repos/{owner}/{repo}/actions/jobs/{job_id}/logs`
（`step_number` 指定時は `.../jobs/{job_id}/steps/{step_number}/logs`）でログ全文を取得し、指定された範囲の行を返します。

**返却フィールド:**
- `totalLines`: ログの総行数
- `offset`: 返した範囲の開始行（0始まり）。`tail` 指定時も実際の開始行が入るため、それより前の行は `offset` 指定で取得できます
- `returnedLines`: 返した行数
- `nextOffset`: 続きがある場合は次に指定する `offset`、最終行まで返した場合は `null`
- `log`: ログ本文。行を `\n` で連結した文字列

`offset` が `totalLines` 以上の場合は、エラーにせず0行（`log` は空文字）を返します。

**行の数え方:**
- 先頭の BOM は除去します（GitHub Actions のログは先頭に UTF-8 の BOM が付きます）
- 末尾の改行は1つだけ除き、`\n` で分割します
- `\r\n` と単独の `\r` は `\n` として扱います。進捗表示などで `\r` を出力するログでは、その位置でも行が分かれます
- 各行の先頭には、GitHub が付与したタイムスタンプが付きます

**注意事項:**
- skipped の job や、完了していない job にはログがなく、`gh: HTTP 404` のエラーになります
- 呼び出しのたびにログ全文を取得し直します。ページ数に比例して取得時間がかかるため、
  `step_number` で対象を絞るか、`tail` で末尾のみを取得すると呼び出し回数を減らせます
- 応答の上限は行数のみで、文字数やバイト数の上限はありません。1行が長いログでは応答が大きくなり、
  クライアント側の出力上限を超える可能性があります。その場合は `limit` を小さくしてください
  （実測例: 5750行で約714KB、平均124バイト/行、最長行3452バイト。この場合500行で約62KBになります）
- エスケープシーケンス（ANSI カラーなど）は除去せず、そのまま `log` に含めます
- UTF-8 として解釈できないバイト列を含むログは、全体が取得できずエラーになります
- job の実行中に内容が変わるログでは、ページ間で `totalLines` や行の位置が変わる可能性があります

**例:**
```json
{
  "name": "gh_run_job_log",
  "arguments": {
    "owner": "anthropics",
    "repository_name": "anthropic-sdk-python",
    "job_id": 136055310860,
    "step_number": 7,
    "tail": 200
  }
}
```

### 16. gh_project_list

指定された owner の GitHub Projects の一覧を取得します。プロジェクト番号を調べるために使います。

**引数:**
- `owner` (必須): プロジェクトのオーナー名（user または organization の login）。`@me` は指定できません
- `limit` (任意): 取得する最大件数（1-100）。未指定時は30

`gh project list --owner <owner> --limit <n> --format json` の出力から、各プロジェクトの `readme` を除いて返します。

**返却フィールド:**
- `totalCount`: owner のプロジェクトの総数。closed のプロジェクトを含みます
- `projects`: プロジェクトの配列。closed のプロジェクトは含みません
  - `number`: プロジェクト番号。gh_project_ で始まる他のツールの `project_number` に指定します
  - `title`, `url`, `shortDescription`, `public`, `closed`, `id`
  - `items`, `fields`: それぞれ `{ "totalCount": N }` の形状
  - `owner`: `{ "type", "login" }`。`type` は `User` または `Organization`

**注意事項:**
- gh は `limit` 件を取得してから closed のプロジェクトを除きます。このため `projects` の件数は `limit` より少なくなることがあります
- closed のプロジェクトを取得する手段はありません

**例:**
```json
{
  "name": "gh_project_list",
  "arguments": {
    "owner": "octocat"
  }
}
```

### 17. gh_project_field_list

指定されたプロジェクトのフィールド一覧を取得します。Status などに設定できる選択肢名を調べるために使います。

**引数:**
- `owner` (必須): プロジェクトのオーナー名（user または organization の login）
- `project_number` (必須): プロジェクト番号。gh_project_list の `projects` の `number` から得られます
- `limit` (任意): 取得する最大件数（1-100）。未指定時は30

**返却フィールド:**
`gh project field-list <project_number> --owner <owner> --format json` の出力をそのまま返します。

- `fields`: フィールドの配列
  - `id`, `name`
  - `type`: `ProjectV2Field` / `ProjectV2SingleSelectField` / `ProjectV2IterationField` のいずれか
  - `options`: single select のフィールド（`type` が `ProjectV2SingleSelectField`）のみ。`[{ "id", "name" }]` の配列で、
    `name` が gh_project_item_edit の `value` に指定する選択肢名です
- `totalCount`: フィールドの総数。`fields` の件数より大きい場合は切り詰められています

Title、Assignees、Labels など、issue / Pull Request 側の属性を表すフィールドも含まれます（`type` は `ProjectV2Field`）。
iteration のフィールドが持つ iteration の一覧は含まれません。

**例:**
```json
{
  "name": "gh_project_field_list",
  "arguments": {
    "owner": "octocat",
    "project_number": 1
  }
}
```

### 18. gh_project_item_list

指定されたプロジェクトのアイテム（issue / Pull Request / draft issue）と、各アイテムのフィールド値の一覧を取得します。

**引数:**
- `owner` (必須): プロジェクトのオーナー名（user または organization の login）
- `project_number` (必須): プロジェクト番号
- `limit` (任意): 取得する最大件数（1-100）。未指定時は30
- `query` (任意): Projects のフィルタ構文による絞り込み（例: `status:"In Progress"`, `-status:Done`, `assignee:octocat is:issue`）

`gh project item-list <project_number> --owner <owner> --limit <n> --format json` の出力から、
各アイテムの `content.body`（issue / Pull Request の本文）を除いて返します。
本文が必要な場合は gh_issue_view / gh_pr_view を利用してください。

**返却フィールド:**
- `totalCount`: 条件に一致するアイテムの総数
- `items`: アイテムの配列。0件の場合は空配列
  - `id`: アイテムの ID
  - `content`: アイテムの内容
    - issue / Pull Request: `type`（`Issue` / `PullRequest`）, `title`, `number`, `repository`（`owner/name` 形式）, `url`
    - draft issue: `type`（`DraftIssue`）, `title`, `id`（draft issue の ID。値がある場合のみ）
    - gh が種類を判別できないアイテムでは `null`
  - フィールド値: フィールドごとに1つのキー

**フィールド値のキー名と値:**
- キー名は、フィールド名の先頭1文字のみを小文字にしたものです（gh の変換規則）。
  `Status` は `status`、`Target Date` は `target Date` になります（スペースは残ります）
- single select の値は選択肢名の文字列です（例: `"status": "In Progress"`）
- text / date は文字列、number は数値、iteration は `{ "title", "startDate", "duration", "iterationId" }` です
- labels / assignees などの複数値は文字列の配列です

**注意事項:**
- `totalCount` が `items` の件数より大きい場合は切り詰められています。続きを取得する手段はないため、`query` で絞り込んでください。
  `limit` の上限が100のため、条件に一致するアイテムが101件以上ある場合は全件を取得できません
- `query` は github.com と GitHub Enterprise Server 3.20 以上で利用できます。
  対応していないホストでは `the --query flag is not supported on this GitHub host` のエラーになります
- draft issue は `url` を持たないため、gh_project_item_edit の対象にできません

**例:**
```json
{
  "name": "gh_project_item_list",
  "arguments": {
    "owner": "octocat",
    "project_number": 1,
    "query": "-status:Done"
  }
}
```

### 19. gh_project_item_add

既存の issue または Pull Request を、指定されたプロジェクトにアイテムとして追加します。

**引数:**
- `owner` (必須): プロジェクトのオーナー名（user または organization の login）
- `project_number` (必須): プロジェクト番号
- `url` (必須): 追加する issue または Pull Request の URL。
  `https://github.com/<owner>/<repo>/issues/<番号>` または `https://github.com/<owner>/<repo>/pull/<番号>` の形式のみ受け付けます

`gh project item-add <project_number> --owner <owner> --url <url> --format json` の出力から `body` を除いて返します。

**返却フィールド:**
- `id`: 追加したアイテムの ID
- `title`: issue / Pull Request のタイトル
- `type`: `Issue` または `PullRequest`
- `url`: issue / Pull Request の URL

**動作:**
- 追加済みの issue / Pull Request を指定した場合は、重複して追加されず、既存のアイテムと同じ `id` が返ります（エラーになりません）
- issue / Pull Request のタイトル・本文・状態は変更しません。
  GitHub の GraphQL API には、プロジェクトへの追加を表す issue / Pull Request のタイムラインイベント（`AddedToProjectV2Event`）が定義されています
- プロジェクトの組み込み workflow が有効な場合、追加をきっかけに workflow が動きます（例: 「Item added to project」による Status の自動設定）

**注意事項:**
- 対象ホストは github.com のみです。gh は URL のホストではなく gh に設定されたホストの API を呼び出すため、
  GitHub Enterprise Server の URL は受け付けません
- 追加したアイテムをプロジェクトから外すツールはありません。誤って追加した場合は、GitHub の Web UI またはホスト上の gh で外してください
- draft issue の作成はできません

**例:**
```json
{
  "name": "gh_project_item_add",
  "arguments": {
    "owner": "octocat",
    "project_number": 1,
    "url": "https://github.com/octocat/hello-world/issues/23"
  }
}
```

### 20. gh_project_item_edit

プロジェクトに追加済みの issue または Pull Request の、フィールド値を1つ更新します。Status の変更に使います。

**引数:**
- `owner` (必須): プロジェクトのオーナー名（user または organization の login）
- `project_number` (必須): プロジェクト番号
- `url` (必須): フィールド値を更新する issue または Pull Request の URL。gh_project_item_add の `url` と同じ形式のみ受け付けます
- `field` (必須): 更新するフィールドの名前（例: `Status`）
- `value` (必須): 設定する値。single select のフィールドでは選択肢名（例: `In Progress`）。
  選択肢名は gh_project_field_list の `options` の `name` から得られます

`gh project item-edit <project_number> --owner <owner> --url <url> --field <field> --value <value> --format json`
の出力から `body` を除いて返します。project ID・アイテム ID・フィールド ID・選択肢 ID は gh が解決します。
この名前指定の経路（`--url` / `--field` / `--value`）は gh 2.97.0 で追加されました。

**返却フィールド:**
- `id`, `title`, `type`, `url`: 更新したアイテム（gh_project_item_add と同じ形状）。更新後のフィールド値は含みません

**更新できるフィールド:**
- single select（`value` は選択肢名）、text、number、date（`value` は `YYYY-MM-DD`）
- iteration のフィールドと、Assignees / Labels など issue / Pull Request 側の属性を表すフィールドは gh がエラーにします
- 1回の呼び出しで更新できるフィールドは1つです。フィールド値のクリアはできません

**エラーになる場合:**
- 存在しない選択肢名を指定した場合。エラーに利用できる選択肢名の一覧が含まれます
  （例: `option "no-such-option" not found on field "Status"; available options: Todo, In Progress, Done`）
- プロジェクトに追加されていない issue / Pull Request を指定した場合
  （例: `... is not an item in project 1; add it first with gh project item-add`）。先に gh_project_item_add で追加してください

**注意事項:**
- プロジェクトの組み込み workflow が有効な場合、フィールド値の変更をきっかけに workflow が動きます。
  「Auto-close issue」が有効なプロジェクトでは、Status の変更をきっかけに issue がクローズされることがあります。
  issue の状態を変えたくない場合は、プロジェクトの設定（Workflows）でこの workflow を無効にしてください。
  このツールは workflow の有効 / 無効を確認しません
- draft issue は `url` を持たないため、このツールでは更新できません
- タイムアウトで中断した場合、更新が反映されたかどうかは応答から判別できません。gh_project_item_list で確認してください

**例:**
```json
{
  "name": "gh_project_item_edit",
  "arguments": {
    "owner": "octocat",
    "project_number": 1,
    "url": "https://github.com/octocat/hello-world/issues/23",
    "field": "Status",
    "value": "In Progress"
  }
}
```

### 21. gh_issue_references

指定された issue または Pull Request を参照している（本文やコメントで言及している）issue と Pull Request の一覧を取得します。
別のリポジトリからの言及も含みます。

**引数:**
- `owner` (必須): リポジトリのオーナー名
- `repository_name` (必須): リポジトリ名
- `number` (必須): 参照される側の issue または Pull Request の番号
- `limit` (任意): 取得する最大件数（1-100）。未指定時は30

`gh api graphql` で、対象の `timelineItems` を `CROSS_REFERENCED_EVENT` に絞り込んで取得し、参照元ごとの情報に整形して返します。
このサーバーで GraphQL を使うのは、このツールのみです。

**返却フィールド:**
- `targetType`: `number` で指定した対象の種別。`Issue` または `PullRequest`
- `totalCount`: GitHub の GraphQL API が返す `totalCount` の値（下記の注意事項を参照）
- `references`: 参照元の配列。古い順。0件の場合は空配列
  - `type`: 参照元の種別。`Issue` または `PullRequest`
  - `number`, `title`, `url`: 参照元の番号・タイトル・URL
  - `state`: 参照元の状態。issue は `OPEN` / `CLOSED`、Pull Request は `OPEN` / `CLOSED` / `MERGED`
  - `repository`: 参照元のリポジトリ（`owner/name` 形式）
  - `isCrossRepository`: 参照元が、対象とは別のリポジトリにある場合 `true`
  - `willCloseTarget`: 参照元がマージされたときに対象がクローズされる場合 `true`
  - `referencedAt`: 言及された日時
  - `actor`: 言及したユーザーの login。取得できない場合（削除されたユーザーなど）は `null`

参照元の本文は含みません。必要な場合は gh_issue_view / gh_pr_view を利用してください。

**含まれないもの:**
- コミットメッセージからの参照
- Development 欄で手動リンクされた Pull Request（gh_issue_view の `closedByPullRequestsReferences` で取得できます）
- コメント、ラベルの変更、クローズなど、タイムラインの他のイベント

**注意事項:**
- `totalCount` の意味は、対象の種別によって異なります（gh 2.103.3 での実測）
  - 対象が issue の場合: 言及の総数です。`references` の件数より大きい場合は切り詰められています
  - 対象が Pull Request の場合: 言及の件数と一致しない値になります。言及が0件の Pull Request で `12` や `17` が返ることを確認しています。
    絞り込みを指定しない場合に返るイベントの件数とも一致せず、何を数えた値かは分かっていません。
    Pull Request では `totalCount` を件数や切り詰めの判定に使わないでください。
    このため Pull Request では、言及が `limit` を超えていても、切り詰められているかどうかを返却から判別できません。
    `references` の件数が `limit` と同じ場合は、切り詰められている可能性があります
- 続きを取得する手段はありません。`limit` の上限が100のため、言及が101件以上ある場合は、新しい側の言及を取得できません
- GitHub がエラーを返した場合は、取得できた分を返さず、全体がエラーになります。
  応答に `null` の参照元が含まれる場合も、除外せずエラーにします。
  参照元にアクセスできないリポジトリのものが含まれる場合にどうなるかは確認できていません
- 存在しないリポジトリを指定するとエラーになります（`gh: Could not resolve to a Repository with the name '...'.`）。
  存在しない番号を指定した場合の挙動は確認できていません
- 同じ参照元が複数回現れるかどうかは確認できていません。重複の除去は行いません

**例:**
```json
{
  "name": "gh_issue_references",
  "arguments": {
    "owner": "octocat",
    "repository_name": "hello-world",
    "number": 23
  }
}
```

## セキュリティ考慮事項

### 1. 提供する操作の範囲

このサーバーは以下のreadonly操作を提供します：
- リポジトリ情報の取得
- Pull Requestの取得（一覧/詳細/コメント）
- Issueの取得（一覧/詳細/コメント）
- ラベル一覧の取得
- GitHub Actions の workflow run 一覧の取得（Pull Request 単位）
- GitHub Actions の job / step 一覧の取得（workflow run 単位）
- GitHub Actions の job / step のログの取得
- GitHub Projects のプロジェクト一覧の取得
- GitHub Projects のフィールド一覧の取得
- GitHub Projects のアイテムとフィールド値の一覧の取得
- issue / Pull Request を参照している issue / Pull Request の一覧の取得

gh_run_job_log が返すログ本文は、workflow の出力そのものです。以下の点を考慮してください。
- ログには、GitHub がマスクしなかった機微な値（トークン、接続文字列、個人情報など）が含まれる可能性があり、
  それらがクライアント（Claude）のコンテキストに入ります
- ログは信頼できない入力です。fork 由来の PR の workflow のログなど、第三者が内容を制御できるログには、
  クライアントへの指示を装った文字列（プロンプトインジェクション）が含まれる可能性があります。
  このサーバーは同じ接続で書き込み操作（git_push、gh_pr_create 等）も提供しているため、
  ログの内容に基づいて書き込み操作を承認する際は注意してください
- このサーバーはすべてのネットワークインターフェースで待ち受け、認証を行いません。
  ポートに到達できるクライアントは、gh の認証情報で読み取れるリポジトリのログを取得できます
- エスケープシーケンスは除去しません。JSON-RPC の応答では `\u001b` としてエスケープされますが、
  クライアントが `log` をそのまま端末に出力すると解釈されます

加えて、以下の書き込み操作を提供します：
- ブランチのpush（git_push）: remote は origin 固定。force push・ブランチ削除・オプション指定は不可。
  push 先ブランチの制限はなく、デフォルトブランチへの push も許可します（1.3.x までは拒否していました）。
  デフォルトブランチの保護はリポジトリ側の branch protection / ruleset で行ってください。
  gh を利用しないため、origin が GitHub 以外の remote であっても push を実行します
- Pull Request作成（gh_pr_create）
- GitHub Projects へのアイテム追加（gh_project_item_add）: 既存の issue / Pull Request をプロジェクトに追加します。
  gh のトークンで書き込めるすべてのプロジェクトが対象になります。追加したアイテムを外すツールは提供しません
- GitHub Projects のフィールド値の更新（gh_project_item_edit）: プロジェクトに追加済みのアイテムのフィールド値を1つ更新します。
  更新できるのはプロジェクト側のフィールド（single select / text / number / date）のみで、
  issue / Pull Request 側の属性は更新できません。ただし、プロジェクトの組み込み workflow（Auto-close issue など）が
  有効な場合は、フィールド値の変更をきっかけに issue がクローズされることがあります
- デフォルトブランチのマージ（git_merge_default_branch）: ローカルリポジトリの作業ツリー・
  インデックス・HEAD を書き換える操作です
- デフォルトブランチへの同期（git_sync_default_branch）: ローカルリポジトリの作業ツリー・
  インデックス・HEAD を書き換える操作です

git_push / git_merge_default_branch / git_sync_default_branch の `path` には
「絶対パス・ディレクトリ存在・.git 存在」以外の制限を設けていません。
サーバープロセスから到達可能な任意の git リポジトリを操作対象に指定できるため、
信頼できないクライアントからアクセス可能な環境で運用する場合はこの点を考慮してください。

git_sync_default_branch は、指定された `path` そのものではなく、そこから解決した
**メインワークツリー**を書き換えます。呼び出し時に提示される引数と実際に書き換わる
ディレクトリが一致しないため、承認する際はこの点を考慮してください。
また、switch / merge は対象リポジトリの post-checkout / post-merge フックを実行します。

### 2. 引数バリデーション

すべてのツール引数は厳密にバリデーションされます：
- オーナー名・リポジトリ名: 正規表現パターンマッチング
- ブランチ名: 正規表現パターンマッチング。先頭の `-` / `:` / `+` および `..` を拒否
  （オプション注入・削除refspec・force refspecの防止）
- オーナー名・リポジトリ名・ブランチ名は、末尾に改行を含む値を拒否
  （パターンの末尾を `$` ではなく `\Z` としています。Python の `$` は末尾の改行の直前にも一致するためです）
- リポジトリパス: 絶対パス・ディレクトリ存在・.git 存在の確認
- PRタイトル: 空文字の拒否
- issue / Pull Request の URL: 正規表現パターンマッチング。github.com の issue / Pull Request の URL のみ受け付け
- 数値: 範囲チェック
- 状態: 列挙値チェック

### 3. コマンドインジェクション対策

- `subprocess.run()` を `shell=False` で実行
- 引数をリスト形式で個別に指定
- タイムアウト設定（デフォルト30秒、環境変数`GH_PROXY_TIMEOUT`で変更可能）

### 4. 認証トークンのスコープ制限

GitHub認証トークンは利用するツールに応じて必要最小限のスコープに制限することを推奨します：
- readonly ツールのみ利用する場合: `repo:read` または `public_repo`
- gh_pr_create を利用する場合: 対象リポジトリへの書き込みが可能なスコープ（`repo` 等）
- gh_project_ で始まるツールを利用する場合: `project`

なお、git_push は gh を利用せず、ホスト側の git の認証設定（credential helper 等）を利用します。

## トラブルシューティング

### gh コマンドが見つかりません

```
エラー: gh コマンドが見つかりません。GitHub CLI をインストールしてください
```

GitHub CLIをインストールし、PATHが通っていることを確認してください。

### 認証エラー

```
エラー: gh repo view failed: To get started with GitHub CLI, please run:  gh auth login
```

`gh auth login` を実行して認証を完了してください。

### タイムアウトエラー

```
エラー: コマンド実行がタイムアウトしました（30秒）
```

ネットワークが遅い場合やリポジトリが非常に大きい場合に発生する可能性があります。
環境変数`GH_PROXY_TIMEOUT`でタイムアウトを延長できます：

```bash
GH_PROXY_TIMEOUT=60 python3 tools/gh-proxy/gh-proxy.py
```

gh_run_job_log は呼び出しのたびに job のログ全文を取得するため、大きいログではタイムアウトしやすくなります。
gh_pr_run_list は gh を2回実行するため、最大でタイムアウトの2倍の時間がかかります。
サーバーは1リクエストずつ処理するため、時間のかかる呼び出しの間は他のツール呼び出しが待たされます。

### gh_run_job_log で --allow-escape-sequences が未知のフラグとしてエラーになる

gh のバージョンが 2.97.0 未満です。gh を 2.97.0 以上に更新してください。

### gh_run_job_log で HTTP 404 になる

```
エラー: gh job log failed: gh: HTTP 404
```

skipped の job や完了していない job にはログがありません。gh_run_job_list で job の `status` と `conclusion` を確認してください。
job ID や step 番号が誤っている場合も同じエラーになります。

### ポート既に使用中

```
OSError: [Errno 98] Address already in use
```

別のポート番号を指定してください：

```bash
GH_PROXY_PORT=30800 python3 tools/gh-proxy/gh-proxy.py
```

## プロトコル仕様

このサーバーは以下の仕様に準拠しています：
- Model Context Protocol (MCP) バージョン 2024-11-05
- JSON-RPC 2.0

## ライセンス

このプロジェクトのライセンスについては、リポジトリのルートディレクトリを参照してください。
