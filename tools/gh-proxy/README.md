# GitHub CLI MCP Proxy Server

Model Context Protocol (MCP) サーバーとして動作し、GitHub CLI (gh) / git コマンドへの操作を提供します。

## 概要

このサーバーは、Claude CodeなどのMCPクライアントに対して、GitHubのreadonly操作と、
一部の書き込み操作（git push、Pull Request作成）を提供します。
ホスト側でサーバーを起動し、Dockerコンテナで動作するClaude Codeから HTTP経由でアクセスすることで、
認証情報を渡すことなくGitHubを操作できます。

## 必要要件

- Python 3.8 以上（標準ライブラリのみ使用）
- GitHub CLI (gh) 2.0.0 以上
- git（git_push を利用する場合）
- GitHub認証済みの環境（`gh auth status` で確認可能）

## セットアップ

### 1. GitHub CLIのインストール

```bash
# macOS
brew install gh

# Ubuntu/Debian
sudo apt install gh

# その他のOSについては https://cli.github.com/ を参照
```

### 2. GitHub認証

```bash
gh auth login
```

readonly ツールのみ利用する場合、認証トークンのスコープは `repo:read` または `public_repo` のみに制限することを推奨します。
git_push / gh_pr_create を利用する場合は、対象リポジトリへの書き込みが可能なスコープ（`repo` 等）が必要です。

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

push 先ブランチがデフォルトブランチと一致する場合、push は拒否されます。
デフォルトブランチは対象リポジトリを作業ディレクトリとして `gh repo view` で判定します。
判定できない場合（GitHub 以外の remote、gh 未認証等）も push を拒否します（fail-closed）。
このため git_push の利用には gh と GitHub 認証が必要です。

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

## セキュリティ考慮事項

### 1. 提供する操作の範囲

このサーバーは以下のreadonly操作を提供します：
- リポジトリ情報の取得
- Pull Requestの取得（一覧/詳細/コメント）
- Issueの取得（一覧/詳細/コメント）

加えて、以下の書き込み操作を提供します：
- ブランチのpush（git_push）: remote は origin 固定。force push・ブランチ削除・オプション指定は不可。
  デフォルトブランチへの push は拒否（デフォルトブランチを判定できない場合も拒否）
- Pull Request作成（gh_pr_create）

git_push の `path` には「絶対パス・ディレクトリ存在・.git 存在」以外の制限を設けていません。
サーバープロセスから到達可能な任意の git リポジトリを push 対象に指定できるため、
信頼できないクライアントからアクセス可能な環境で運用する場合はこの点を考慮してください。

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
- git_push / gh_pr_create を利用する場合: 対象リポジトリへの書き込みが可能なスコープ（`repo` 等）

なお、git_push はホスト側の git の認証設定（credential helper 等）を利用します。

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
