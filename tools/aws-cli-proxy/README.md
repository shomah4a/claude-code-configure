# AWS CLI MCP Proxy Server

Model Context Protocol (MCP) サーバーとして動作し、ホスト側でプロファイルを固定した `aws` コマンドの実行を提供します。

## 概要

このサーバーは、Docker コンテナで動作する Claude Code に対して、ホスト側の `~/.aws/config` に定義されたプロファイルで `aws` コマンドを実行し、その結果を返します。
ホスト側でサーバーを起動し、コンテナ内の Claude Code から HTTP 経由でアクセスします。
認証情報はホストの `aws` プロセス内で解決され、コンテナにもクライアントにも渡しません (gh-proxy と同じ構成)。

利用できるプロファイルは設定ファイル `aws-cli-proxy.yml` で定義したものに限られます。設定ファイルは git 管理外です。

## 必要要件

- Python 3.10 以上と PyYAML (venv 経由で導入。`make serve` が自動で作成します)
- AWS CLI v2
  - 起動時に `aws --version` を確認し、v2 でなければ起動しません (v1 はパラメータの `http://` 参照など、本サーバーの引数検証が前提としない機能を持つため)
- 実行対象のプロファイルが `~/.aws/config` または `~/.aws/credentials` に定義されていること
  - SSO プロファイルは事前に `aws sso login --profile <profile>` でログインしておく必要があります
  - `credential_process` に aws-vault 等を使う場合、そのキーリング指定 (`AWS_VAULT_BACKEND` 等) がサーバーを起動したシェルの環境に必要です
  - MFA 等で対話入力を要求するプロファイルは、標準入力を閉じて実行するため失敗します

## セットアップ

### 1. 設定ファイルの作成

```bash
cd tools/aws-cli-proxy
cp aws-cli-proxy.yml.tmpl aws-cli-proxy.yml
```

`aws-cli-proxy.yml` を編集します。

```yaml
profiles:
  dev-readonly:                       # MCP ツールから指定する名前
    profile: my-dev-readonly-profile  # ~/.aws/config のプロファイル名
```

- `name` (profiles 配下のキー) と `profile` は、英数字で始まり英数字と `.` `_` `-` のみで構成してください
- **登録するプロファイルは読み取り専用の IAM ロールに限ってください。** コンテナ内の Claude は、登録したプロファイルの IAM 権限の範囲で任意の aws 操作 (書き込み・削除を含む) ができます。操作の制限はホスト側の IAM で行います
- 不正なエントリは起動時に標準エラー出力へ警告を出してスキップします
- 設定ファイルが無い、`profiles` キーが無い、有効なエントリが 0 件の場合は起動時にエラー終了します
- 設定はサーバー起動時に読み込みます。変更後はサーバーを再起動してください

旧 `aws-credential` から移行する場合は、`aws-credential.yml` をコピーしてトップレベルのキーを `credentials` から `profiles` に変更してください。

### 2. サーバーの起動

```bash
# 既定 (127.0.0.1:30722、タイムアウト 30 秒、出力上限 1 MiB)
make -C tools/aws-cli-proxy serve

# 環境変数で変更
AWS_CLI_PROXY_TIMEOUT=120 make -C tools/aws-cli-proxy serve
```

| 環境変数 | 既定値 | 内容 |
|---|---|---|
| `AWS_CLI_PROXY_BIND` | `127.0.0.1` | 待ち受けアドレス |
| `AWS_CLI_PROXY_PORT` | `30722` | 待ち受けポート (1〜65535) |
| `AWS_CLI_PROXY_TIMEOUT` | `30` | `aws` コマンドのタイムアウト (秒、正の整数) |
| `AWS_CLI_PROXY_MAX_OUTPUT_BYTES` | `1048576` | 返却する標準出力・標準エラー出力それぞれの上限 (バイト、正の整数) |
| `AWS_CLI_PROXY_CONFIG` | スクリプトと同じディレクトリの `aws-cli-proxy.yml` | 設定ファイルのパス |
| `AWS_CLI_PROXY_ALLOWED_HOSTS` | (なし) | `Host` / `Origin` ヘッダで許可するホスト名の追加分 (カンマ区切り、完全一致、ワイルドカード不可)。`localhost`, `127.0.0.1`, `[::1]` は常に許可され、環境変数で外すことはできません |

`tools/tool-launcher/launcher.py` (`make launch-servers`) にも登録されているため、他のツールと一括起動できます。

サーバーは起動時のコードと設定で動作し続けるため、`aws-cli-proxy.py` や `aws-cli-proxy.yml` を更新した場合はサーバーを再起動してください。

### 3. Claude Code への登録

`mcp-servers.template.json` に `aws-cli-proxy` として登録済みです。`make update` で `mcp-servers.json` に反映され、コンテナ内の Claude Code から利用できます。

`make update` は `mcp-servers.template.json` を `mcp-servers.json` に上書きコピーします。`mcp-servers.json` にローカルで追加したエントリがある場合は、実行前に退避するか、`aws-cli-proxy` のエントリを手で追記してください。

`docker/docker-compose.yml` は `network_mode: host` のため、コンテナ内の `localhost:30722` はホストの loopback と同一です。
bridge network 構成に変更する場合は `AWS_CLI_PROXY_BIND` で待ち受けアドレスを明示し、`AWS_CLI_PROXY_ALLOWED_HOSTS` に接続時のホスト名 (例: `host.docker.internal`) を追加し、`mcp-servers.template.json` の URL も合わせて変更してください。

## 提供するツール

### aws_run

設定ファイルで定義した `name` のプロファイルで `aws` コマンドを実行します。

| 引数 | 型 | 必須 | 内容 |
|---|---|---|---|
| `name` | string (設定ファイルの name の enum) | はい | 使用するプロファイルの名前 |
| `args` | string の配列 (1 要素以上) | はい | `aws` に続く引数。例: `["s3", "ls"]`, `["sts", "get-caller-identity", "--output", "json"]` |

サーバーは `aws --profile <profile> <args...>` を実行し、終了コード 0 なら標準出力をそのまま返します。
終了コードが非 0 の場合は `isError: true` で、終了コードと標準出力・標準エラー出力を返します。

### 指定できない引数

以下は引数検証で拒否され、`aws` は実行されません。

| 種別 | 内容 | 理由 |
|---|---|---|
| グローバルオプション | `--profile`, `--debug`, `--endpoint-url`, `--no-verify-ssl`, `--ca-bundle`, `--help` (省略形 `--prof` や `--profile=x` 形式を含む) | プロファイルの上書き、デバッグログへのセッショントークンの出力、署名付きリクエストの送信先の差し替え、ヘルプ描画による groff / pager の起動を防ぐ |
| セパレータ | `--` | 以降が検証の対象外になるのを防ぐ (aws 側でも受理されない) |
| サブコマンド | `configure`, `sso`, `help`, `history` (引数のどの位置にあっても) | ホストの `~/.aws/config` の書き換え (`configure set credential_process ...` はホスト上の任意コマンド実行につながる)、認証情報の出力 (`configure export-credentials`)、SSO トークンの削除、ブラウザ・pager の起動、コマンド履歴 (`~/.aws/cli/history`、過去の API レスポンスを含む) の出力を防ぐ。`aws configure list-profiles` も使えません |
| ホストのファイル参照 | `file://` / `fileb://` を含む値、`/` `~` `./` `../` で始まる値、`/../` を含む値、`..`。`--opt=値` 形式では `=` 以降の値部分にも同じ規則を適用 | ホストのファイルの読み書きを防ぐ |

先頭が `/` の値は一律に拒否するため、CloudWatch Logs のロググループ名 (`/aws/lambda/...`) や SSM パラメータ名 (`/prod/db/host`) のようなパス型の AWS 識別子は指定できません。これは誤検知を受容した仕様です。

相対パス (例: `out.json`) は拒否しません。`aws` は呼び出しごとに作られる空の一時ディレクトリを作業ディレクトリとして実行され、終了後に削除されるため、相対パスでの読み書きはその中に閉じます。一時ディレクトリへの書き込み量に上限はありません。

### 出力の上限

標準出力・標準エラー出力はそれぞれ `AWS_CLI_PROXY_MAX_OUTPUT_BYTES` (既定 1 MiB) で切り詰め、切り詰めた場合はその旨を付記します。
大きな結果は `--query`、`--max-items`、`--page-size` 等で絞ってください。切り詰めは応答サイズを抑えるためのもので、`aws` プロセスの出力全体はサーバーのメモリに一度保持されます。

UTF-8 として解釈できないバイト列は置換文字に変換して返します。

## 動作の詳細

- 起動時に `aws --version` を実行し、`aws-cli/2.` で始まる出力でなければ終了コード 1 で停止します
- `aws` は標準入力を閉じ、`AWS_PAGER` を空にして実行します。環境変数はサーバーを起動したシェルのものをそのまま引き継ぎます (aws-vault 等の `credential_process` がキーリング指定を必要とするため)
- サーバーは単一スレッドで動作します。`aws` コマンドの実行中は他のリクエストを処理しません
- リクエストごとに `method` と `params` (name と args) をサーバーの標準エラー出力に記録します。`args` にシークレット (例: `secretsmanager put-secret-value --secret-string ...`) を書くと、ホストの端末ログに残ります。書かないことは呼び出し側の責任です

## セキュリティ考慮事項

- **認証情報はコンテナに渡りません。** 認証情報の解決はホストの `aws` プロセス内で完結します。`configure export-credentials`、`history`、`--debug` を拒否しているため、ツール経由でホスト側の認証情報を取り出す既知の経路は塞いでいます。ただし `sts get-session-token` や `sts assume-role`、`ecr get-login-password` のように、IAM 権限の範囲内で AWS API から取得できる資格情報は防ぎません
- **プロファイルは設定ファイルのものに固定されます。** `--profile` とその省略形は argparse による事前パースで検出して拒否します。`configure set` によるホスト設定の書き換えも拒否します
- **全プロジェクト・全セッションで有効になります。** `mcp-servers.json` は managed MCP 設定 (`/etc/claude-code/managed-mcp.json`) としてコンテナにマウントされるため、AWS を使わないプロジェクトのセッションからも呼び出せます。`--permission-mode auto` と組み合わせた場合、外部コンテンツ (Web ページ、Issue 本文、PR コメント等) に埋め込まれた指示によって `aws_run` が呼ばれ、登録プロファイルの IAM 権限の範囲で操作されるリスクがあります。登録するプロファイルを読み取り専用に限ることが前提です
- **ホスト側の `~/.aws/cli/alias` は検出できません。** AWS CLI の alias は任意の名前を第一引数として受け付け、`!` で始まる alias はシェルコマンドとして実行されます。サーバーは alias 名を知り得ないため拒否できません。ホストで alias を定義している場合は、本サーバーを使わないか alias ファイルを退避してください
- **拒否リスト方式の限界。** 引数検証は拒否対象を列挙する方式のため、将来の AWS CLI で追加されるグローバルオプションやサブコマンドは自動では拒否されません。AWS CLI を更新した際は拒否対象の見直しが必要です
- **認証は行いません。** 既定で loopback (`127.0.0.1`) にのみ bind するため、同一ホスト上のプロセスからのみ到達できます。`AWS_CLI_PROXY_BIND` を `0.0.0.0` 等に変更すると、ネットワーク上の任意のホストから無認証で `aws` を実行できる状態になります
- `Host` ヘッダと (存在する場合) `Origin` ヘッダのホスト名が loopback または `AWS_CLI_PROXY_ALLOWED_HOSTS` に無いリクエスト、および `Host` が重複してカンマ結合されたリクエストは 403 で拒否します。ホスト上のブラウザで開いたページから DNS リバインディングで到達する経路への対策で、同一ホスト上の非ブラウザプロセスからの直接アクセスは防ぎません
- `name` と `profile` は先頭にハイフンを許可しないため、`aws` コマンドへのオプション注入はできません
- `aws` の標準エラー出力は失敗時にクライアントへ返します。`credential_process` を使うプロファイルでは、そのスクリプトが標準エラー出力に書いた内容が含まれます

## テスト

```bash
cd tools/aws-cli-proxy
make venv/.initialized
venv/bin/python3 -m unittest test_aws_cli_proxy -v
```

## ロールバック

本ツールを削除する場合は、`tools/aws-cli-proxy/` のほか、`.gitignore`、`mcp-servers.template.json`、`tools/tool-launcher/launcher.py` の追記を戻した上で、`mcp-servers.json` と `~/.claude/` を更新してください。
