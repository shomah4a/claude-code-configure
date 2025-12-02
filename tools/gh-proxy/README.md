# GitHub CLI MCP Proxy Server

Model Context Protocol (MCP) サーバーとして動作し、GitHub CLI (gh) コマンドへのreadonly操作を提供します。

## 概要

このサーバーは、Claude CodeなどのMCPクライアントに対して、GitHubのreadonly操作を安全に提供します。
ホスト側でサーバーを起動し、Dockerコンテナで動作するClaude Codeから HTTP経由でアクセスすることで、
認証情報を渡すことなくGitHubデータを取得できます。

## 必要要件

- Python 3.8 以上（標準ライブラリのみ使用）
- GitHub CLI (gh) 2.0.0 以上
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

認証トークンのスコープは `repo:read` または `public_repo` のみに制限することを推奨します。

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

サーバーは `127.0.0.1` でリッスンし、ローカルホストからの接続のみを受け付けます。
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

## セキュリティ考慮事項

### 1. readonly操作のみ提供

このサーバーは以下のreadonly操作のみを提供します：
- リポジトリ情報の取得
- Pull Requestの取得（一覧/詳細）
- Issueの取得（一覧/詳細）

書き込み操作（PR作成、Issue作成、コメント投稿など）は一切提供していません。

### 2. 引数バリデーション

すべてのツール引数は厳密にバリデーションされます：
- オーナー名・リポジトリ名: 正規表現パターンマッチング
- 数値: 範囲チェック
- 状態: 列挙値チェック

### 3. コマンドインジェクション対策

- `subprocess.run()` を `shell=False` で実行
- 引数をリスト形式で個別に指定
- タイムアウト設定（デフォルト30秒、環境変数`GH_PROXY_TIMEOUT`で変更可能）

### 4. 認証トークンのスコープ制限

GitHub認証トークンは以下のスコープに制限することを推奨します：
- `repo:read`: プライベートリポジトリへの読み取りアクセス
- `public_repo`: パブリックリポジトリへの読み取りアクセス

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
