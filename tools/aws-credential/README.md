# AWS Credential MCP Proxy Server

Model Context Protocol (MCP) サーバーとして動作し、ホスト側の AWS 認証情報を取得する操作を提供します。

## 概要

このサーバーは、Docker コンテナで動作する Claude Code に対して、ホスト側の `~/.aws/config` または `~/.aws/credentials` に定義されたプロファイルの認証情報を返します。
ホスト側でサーバーを起動し、コンテナ内の Claude Code から HTTP 経由でアクセスします。
認証情報の解決は `aws configure export-credentials` に委譲するため、SSO、assume-role、credential_process、MFA を含むプロファイルに対応します。

どのプロファイルを取得可能にするかは設定ファイル `aws-credential.yml` で定義します。設定ファイルは git 管理外です。

## 必要要件

- Python 3.8 以上と PyYAML (venv 経由で導入。`make serve` が自動で作成します)
- AWS CLI v2 (2.9 以降)
  - `aws configure export-credentials` は AWS CLI v2 の 2.9 系で追加されました。v1 にはありません
  - 出典: https://github.com/aws/aws-cli/issues/7388
- 取得対象のプロファイルが `~/.aws/config` または `~/.aws/credentials` に定義されていること
  - SSO プロファイルは事前に `aws sso login --profile <profile>` でログインしておく必要があります
  - MFA 等で対話入力を要求するプロファイルは、標準入力を閉じて実行するため取得に失敗します

## セットアップ

### 1. 設定ファイルの作成

```bash
cd tools/aws-credential
cp aws-credential.yml.tmpl aws-credential.yml
```

`aws-credential.yml` を編集します。

```yaml
credentials:
  dev:                       # MCP ツールから指定する名前
    profile: my-dev-profile  # ~/.aws/config のプロファイル名
  staging:
    profile: my-staging-profile
```

- `name` (credentials 配下のキー) と `profile` は、英数字で始まり英数字と `.` `_` `-` のみで構成してください
- 不正なエントリは起動時に標準エラー出力へ警告を出してスキップします
- 設定ファイルが無い、`credentials` キーが無い、有効なエントリが 0 件の場合は起動時にエラー終了します
- 設定はサーバー起動時に読み込みます。変更後はサーバーを再起動してください

### 2. サーバーの起動

```bash
# 既定 (127.0.0.1:30722、タイムアウト 30 秒)
make -C tools/aws-credential serve

# 環境変数で変更
AWS_CREDENTIAL_PROXY_PORT=30800 make -C tools/aws-credential serve
```

| 環境変数 | 既定値 | 内容 |
|---|---|---|
| `AWS_CREDENTIAL_PROXY_BIND` | `127.0.0.1` | 待ち受けアドレス |
| `AWS_CREDENTIAL_PROXY_PORT` | `30722` | 待ち受けポート |
| `AWS_CREDENTIAL_PROXY_TIMEOUT` | `30` | `aws` コマンドのタイムアウト (秒) |
| `AWS_CREDENTIAL_PROXY_CONFIG` | スクリプトと同じディレクトリの `aws-credential.yml` | 設定ファイルのパス |
| `AWS_CREDENTIAL_PROXY_ALLOWED_HOSTS` | (なし) | `Host` / `Origin` ヘッダで許可するホスト名の追加分 (カンマ区切り)。`localhost`, `127.0.0.1`, `::1` は常に許可 |

`tools/tool-launcher/launcher.py` (`make launch-servers`) にも登録されているため、他のツールと一括起動できます。

サーバーは起動時のコードと設定で動作し続けるため、`aws-credential.py` や `aws-credential.yml` を更新した場合はサーバーを再起動してください。

### 3. Claude Code への登録

`mcp-servers.template.json` に登録済みです。`make update` で `mcp-servers.json` に反映され、コンテナ内の Claude Code から `aws-credential` サーバーとして利用できます。

`docker/docker-compose.yml` は `network_mode: host` のため、コンテナ内の `localhost:30722` はホストの loopback と同一です。
bridge network 構成に変更する場合は `AWS_CREDENTIAL_PROXY_BIND` で待ち受けアドレスを明示し、`AWS_CREDENTIAL_PROXY_ALLOWED_HOSTS` に接続時のホスト名 (例: `host.docker.internal`) を追加し、`mcp-servers.template.json` の URL も合わせて変更してください。

## 提供するツール

### aws_get_credentials

設定ファイルで定義した `name` を指定して、対応するプロファイルの認証情報を取得します。

| 引数 | 型 | 必須 | 内容 |
|---|---|---|---|
| `name` | string (設定ファイルの name の enum) | はい | 取得する認証情報の名前 |

返却値は、環境変数名をキーとする JSON オブジェクトです。

```json
{
  "AWS_ACCESS_KEY_ID": "...",
  "AWS_SECRET_ACCESS_KEY": "...",
  "AWS_SESSION_TOKEN": "...",
  "AWS_CREDENTIAL_EXPIRATION": "...",
  "AWS_REGION": "...",
  "AWS_DEFAULT_REGION": "..."
}
```

- `AWS_SESSION_TOKEN`、`AWS_CREDENTIAL_EXPIRATION` は一時認証情報の場合のみ含まれます。静的なアクセスキーのプロファイルでは含まれません
- `AWS_REGION`、`AWS_DEFAULT_REGION` は `aws configure get region --profile <profile>` で region を取得できた場合のみ含まれます。両方に同じ値が入ります。コンテナには `~/.aws/config` が無いため、含まれない場合はコンテナ内の `aws` に `--region` を付けるか `AWS_REGION` を手動で設定してください
- 一時認証情報は `AWS_CREDENTIAL_EXPIRATION` の時刻で失効します。失効後は再取得してください

取得に失敗した場合は `isError: true` で、終了コードを含む固定の文言を返します。`aws` コマンドの標準エラー出力はクライアントには返さず、サーバーの標準エラー出力にのみ記録します。

## 動作の詳細

1. `aws configure export-credentials --profile <profile> --format process` を実行し、JSON (credential_process 互換形式) を解釈します
   - 出典: https://docs.aws.amazon.com/cli/latest/reference/configure/export-credentials.html
   - `Version` が 1 でない場合、`AccessKeyId` / `SecretAccessKey` が欠けている場合はエラーにします
2. `aws configure get region --profile <profile>` を実行し、取得できた場合のみ region を付与します
3. 環境変数名をキーとする JSON に整形して返します

`aws` コマンドは以下の条件で実行します。

- 標準入力は閉じて実行します (対話入力でハングしないようにするため)
- サーバーを起動したシェルの環境変数をそのまま引き継ぎます。`credential_process` に aws-vault 等を使うプロファイルでは、キーリングのバックエンド指定 (`AWS_VAULT_BACKEND` 等) がサーバーの環境に必要です。`--profile` を明示して実行するため、シェルの `AWS_ACCESS_KEY_ID` 等が取得結果に混ざることはありませんが、`AWS_CONFIG_FILE` や `AWS_REGION` 等の設定系変数は解決結果に影響します
- サーバーは単一スレッドで動作します。`aws` コマンドの実行中は他のリクエストを処理しません

## セキュリティ考慮事項

- **認証情報は Claude の会話に平文で残ります。** ツールの返却値として会話コンテキストに入り、セッションログ (`~/.claude/projects` 配下) にも記録されます
- **全プロジェクト・全セッションで有効になります。** `mcp-servers.json` は managed MCP 設定 (`/etc/claude-code/managed-mcp.json`) としてコンテナにマウントされるため、AWS を使わないプロジェクトのセッションからも呼び出せます。`--permission-mode auto` と組み合わせた場合、外部コンテンツ (Web ページ、Issue 本文、PR コメント等) に埋め込まれた指示によって呼び出され、認証情報が外部へ送信されるリスクがあります。コンテナに AWS CLI が入っている構成では、取得した認証情報でコンテナ内から AWS API (読み出し・破壊的操作の双方) を実行できます
- **認証は行いません。** 既定で loopback (`127.0.0.1`) にのみ bind するため、同一ホスト上のプロセスからのみ到達できます。`AWS_CREDENTIAL_PROXY_BIND` を `0.0.0.0` 等に変更すると、ネットワーク上の任意のホストから無認証で認証情報を取得できる状態になります
- `name` と `profile` は先頭にハイフンを許可しないため、`aws` コマンドへのオプション注入はできません
- `aws` の標準出力・標準エラー出力の内容はクライアントへのエラー応答に含めません。`aws` の標準エラー出力はサーバーの標準エラー出力 (`make launch-servers` 経由ではその端末) に記録されます。`credential_process` を使うプロファイルでは、そのスクリプトが標準エラー出力に書いた内容がここに含まれます
- `Host` ヘッダと (存在する場合) `Origin` ヘッダのホスト名が loopback (`localhost`, `127.0.0.1`, `::1`) または `AWS_CREDENTIAL_PROXY_ALLOWED_HOSTS` に無いリクエストは 403 で拒否します。ホスト上のブラウザで開いたページから DNS リバインディングで `127.0.0.1:30722` に到達する経路への対策です。同一ホスト上の非ブラウザプロセスからの直接アクセスは防ぎません

## テスト

```bash
cd tools/aws-credential
make venv/.initialized
venv/bin/python3 -m unittest test_aws_credential -v
```

## ロールバック

本ツールを削除する場合は、`tools/aws-credential/` のほか、`.gitignore`、`mcp-servers.template.json`、`tools/tool-launcher/launcher.py` の追記を戻した上で、`make update` を再実行して `mcp-servers.json` と `~/.claude/` を再生成してください。
