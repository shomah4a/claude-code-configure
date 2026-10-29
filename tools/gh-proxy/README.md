# GitHub CLI MCP Proxy Server

Model Context Protocol (MCP) サーバーとして動作し、GitHub CLI (gh) / git コマンドへの操作を提供します。

## 概要

このサーバーは、Claude CodeなどのMCPクライアントに対して、GitHubのreadonly操作と、
一部の書き込み操作（git push、Pull Request作成、デフォルトブランチのマージ / 同期）を提供します。
ホスト側でサーバーを起動し、Dockerコンテナで動作するClaude Codeから HTTP経由でアクセスすることで、
認証情報を渡すことなくGitHubを操作できます。

## 必要要件

- Python 3.8 以上（標準ライブラリのみ使用）
- GitHub CLI (gh) 2.94.0 以上
  - gh_issue_view の parent / subIssues / subIssuesSummary / blockedBy / blocking は 2.94.0 で追加されたフィールドです
  - gh_pr_view の closingIssuesReferences は 2.72.0、gh_issue_view の closedByPullRequestsReferences は 2.73.0 で追加されたフィールドです
  - 要件未満のバージョンでは未知の JSON フィールド指定として gh がエラー終了し、gh_issue_view / gh_pr_view は基本フィールドも含めて取得できません
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

インストール後、`gh --version` で 2.94.0 以上であることを確認してください。

### 2. GitHub認証

```bash
gh auth login
```

readonly ツールのみ利用する場合、認証トークンのスコープは `repo:read` または `public_repo` のみに制限することを推奨します。
gh_pr_create を利用する場合は、対象リポジトリへの書き込みが可能なスコープ（`repo` 等）が必要です。
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
- `state` (任意): PRの状態 (`open`, `closed`, `merged`, `all`)
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

本文中の `#123` 形式の単なる言及や timeline の cross-reference は含まれません。

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
本文中の `#123` 形式の単なる言及や timeline の cross-reference は含まれません。

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

## セキュリティ考慮事項

### 1. 提供する操作の範囲

このサーバーは以下のreadonly操作を提供します：
- リポジトリ情報の取得
- Pull Requestの取得（一覧/詳細/コメント）
- Issueの取得（一覧/詳細/コメント）
- ラベル一覧の取得

加えて、以下の書き込み操作を提供します：
- ブランチのpush（git_push）: remote は origin 固定。force push・ブランチ削除・オプション指定は不可。
  push 先ブランチの制限はなく、デフォルトブランチへの push も許可します（1.3.x までは拒否していました）。
  デフォルトブランチの保護はリポジトリ側の branch protection / ruleset で行ってください。
  gh を利用しないため、origin が GitHub 以外の remote であっても push を実行します
- Pull Request作成（gh_pr_create）
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
- リポジトリパス: 絶対パス・ディレクトリ存在・.git 存在の確認
- PRタイトル: 空文字の拒否
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
