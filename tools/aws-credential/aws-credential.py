#!/usr/bin/env python3
"""
AWS Credential MCP Proxy Server

ホスト側の AWS 認証情報を `aws configure export-credentials` で取得し、
MCP ツールとして環境変数名をキーとする JSON で返す HTTP JSON-RPC サーバーです。

設定ファイル (aws-credential.yml) に記載された name ごとに、対応する
AWS プロファイルの認証情報を取得できます。
"""

import dataclasses
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from wsgiref.simple_server import make_server

import yaml

# サーバー設定
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "aws-credential"
SERVER_VERSION = "1.0.0"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "aws-credential.yml"
CONFIG_PATH_ENV = "AWS_CREDENTIAL_PROXY_CONFIG"
TIMEOUT_ENV = "AWS_CREDENTIAL_PROXY_TIMEOUT"
DEFAULT_TIMEOUT_SEC = 30
BIND_ENV = "AWS_CREDENTIAL_PROXY_BIND"
DEFAULT_BIND = "127.0.0.1"
PORT_ENV = "AWS_CREDENTIAL_PROXY_PORT"
DEFAULT_PORT = 30722
TOOL_NAME = "aws_get_credentials"

# aws configure export-credentials --format process の出力仕様バージョン
EXPECTED_PROCESS_FORMAT_VERSION = 1

# aws サブプロセスへ引き継ぐ環境変数。AWS_* を含む他の変数は渡さず、起動シェルの環境で解決結果が変わらないようにする
SUBPROCESS_ENV_KEYS = ("HOME", "PATH", "LANG", "LC_ALL")

# name と profile に共通。先頭の - を拒否して subprocess へのオプション注入を防ぐ
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# JSON-RPC エラーコード (gh-proxy と同じ)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# (stdout, stderr, returncode) を返すコマンド実行関数。副作用 (プロセス起動) を外部注入するための型
RunCommand = Callable[[List[str]], Tuple[str, str, int]]


@dataclasses.dataclass(frozen=True)
class CredentialEntry:
    """設定ファイルの credentials 配下の 1 エントリ"""

    name: str
    profile: str


class ConfigError(Exception):
    """設定ファイルの致命的な不備 (起動を継続できない)"""


@dataclasses.dataclass(frozen=True)
class AwsCredentials:
    """aws configure export-credentials --format process の出力を表す"""

    access_key_id: str
    secret_access_key: str
    session_token: Optional[str]
    expiration: Optional[str]


class CredentialFetchError(Exception):
    """認証情報の取得失敗。メッセージはクライアントに返すため秘密情報を含めてはならない"""


class ValidationError(Exception):
    """MCP リクエストの引数検証失敗 (JSON-RPC の INVALID_PARAMS に対応する)"""


def parse_credential_entry(name: Any, conf: Any) -> CredentialEntry:
    """1 エントリ分の name / conf を検証し CredentialEntry に変換する

    不正な場合は理由を含む ValueError を送出する。
    """
    if not isinstance(name, str) or not IDENTIFIER_PATTERN.match(name):
        raise ValueError(f"name '{name}' は英数字で始まる英数字と._-のみの文字列である必要があります")

    if not isinstance(conf, dict):
        raise ValueError(f"エントリの値は辞書である必要がありますが {type(conf).__name__} でした")

    unknown_keys = set(conf.keys()) - {"profile"}
    if unknown_keys:
        raise ValueError(f"未知のキーが含まれています: {sorted(unknown_keys)}")

    profile = conf.get("profile")
    if not isinstance(profile, str) or not IDENTIFIER_PATTERN.match(profile):
        raise ValueError("profile は英数字で始まる英数字と._-のみの文字列である必要があります")

    return CredentialEntry(name=name, profile=profile)


def parse_entries(raw: Any, report: Callable[[str], None]) -> List[CredentialEntry]:
    """設定全体 (yaml.safe_load の戻り値) から有効な CredentialEntry のリストを作る

    個々のエントリが不正な場合はスキップして report で通知し、
    設定ファイル自体の構造が不正、または有効なエントリが 0 件の場合は ConfigError を送出する。
    """
    if not isinstance(raw, dict) or "credentials" not in raw:
        raise ConfigError("設定ファイルにcredentialsキーがありません")

    credentials = raw["credentials"]
    if not isinstance(credentials, dict):
        raise ConfigError(f"credentialsは辞書である必要がありますが{type(credentials).__name__}でした")

    entries: List[CredentialEntry] = []
    for name, conf in credentials.items():
        try:
            entries.append(parse_credential_entry(name, conf))
        except ValueError as e:
            report(f"エントリ '{name}' をスキップします: {e}")

    if not entries:
        raise ConfigError("有効なcredentialsエントリがありません")

    return entries


def load_config(config_path: Path, report: Callable[[str], None]) -> List[CredentialEntry]:
    """設定ファイルを読み込み、有効な CredentialEntry のリストを返す"""
    if not config_path.exists():
        raise ConfigError(f"設定ファイルが見つかりません: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return parse_entries(raw, report)


def resolve_config_path(environ: Mapping[str, str]) -> Path:
    """環境変数から設定ファイルのパスを決定する

    環境変数が設定されていればそのパスを、無ければ既定パスを返す。
    """
    configured = environ.get(CONFIG_PATH_ENV)
    if configured is not None:
        return Path(configured)
    return DEFAULT_CONFIG_PATH


def build_export_credentials_args(profile: str) -> List[str]:
    """指定プロファイルの認証情報を process 形式で取得する aws コマンド引数を組み立てる"""
    return ["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"]


def build_get_region_args(profile: str) -> List[str]:
    """指定プロファイルの region を取得する aws コマンド引数を組み立てる"""
    return ["aws", "configure", "get", "region", "--profile", profile]


def _require_str_field(data: Dict[str, Any], field: str) -> str:
    """data[field] が存在する str であることを検証して返す

    export-credentials の出力 (data) は秘密情報を含みうるため、
    フィールド名以外の値をエラーメッセージに含めてはならない。
    """
    value = data.get(field)
    if not isinstance(value, str):
        raise CredentialFetchError(f"export-credentials の出力に {field} がありません")
    return value


def _optional_str_field(data: Dict[str, Any], field: str) -> Optional[str]:
    """data[field] が省略可能な str であることを検証して返す"""
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CredentialFetchError(f"export-credentials の出力の {field} の形式が不正です")
    return value


def parse_export_credentials_output(stdout: str) -> AwsCredentials:
    """aws configure export-credentials --format process の標準出力を解析する

    stdout は認証情報そのものを含むため、失敗時の例外メッセージに含めてはならない。
    json.JSONDecodeError からは e.pos のみを参照し、入力文字列を保持する e.doc は参照しない。
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise CredentialFetchError(f"export-credentials の出力を JSON として解釈できません (位置 {e.pos})")

    if not isinstance(data, dict):
        raise CredentialFetchError("export-credentials の出力が JSON オブジェクトではありません")

    if data.get("Version") != EXPECTED_PROCESS_FORMAT_VERSION:
        raise CredentialFetchError(
            f"export-credentials の出力の Version が想定 ({EXPECTED_PROCESS_FORMAT_VERSION}) と異なります"
        )

    return AwsCredentials(
        access_key_id=_require_str_field(data, "AccessKeyId"),
        secret_access_key=_require_str_field(data, "SecretAccessKey"),
        session_token=_optional_str_field(data, "SessionToken"),
        expiration=_optional_str_field(data, "Expiration"),
    )


def build_env_mapping(credentials: AwsCredentials, region: Optional[str]) -> Dict[str, str]:
    """認証情報と region から MCP ツールの戻り値となる環境変数マッピングを作る"""
    env: Dict[str, str] = {
        "AWS_ACCESS_KEY_ID": credentials.access_key_id,
        "AWS_SECRET_ACCESS_KEY": credentials.secret_access_key,
    }
    if credentials.session_token is not None:
        env["AWS_SESSION_TOKEN"] = credentials.session_token
    if credentials.expiration is not None:
        env["AWS_CREDENTIAL_EXPIRATION"] = credentials.expiration
    if region is not None:
        env["AWS_REGION"] = region
        env["AWS_DEFAULT_REGION"] = region
    return env


def fetch_region(profile: str, run_command: RunCommand, report: Callable[[str], None]) -> Optional[str]:
    """指定プロファイルの region を取得する

    取得できなくても認証情報自体の返却は妨げないため、失敗時は report で通知して None を返す。
    """
    stdout, stderr, code = run_command(build_get_region_args(profile))
    region = stdout.strip()
    if code == 0 and region:
        return region

    report(
        f"プロファイル '{profile}' の region を取得できなかったため返却値に含めません"
        f" (終了コード {code}): {stderr.strip()}"
    )
    return None


def is_export_credentials_unavailable(stderr: str) -> bool:
    """stderr が export-credentials サブコマンド未実装 (AWS CLI v1 等) によるものかを判定する"""
    return "Invalid choice" in stderr and "export-credentials" in stderr


def fetch_credentials(profile: str, run_command: RunCommand, report: Callable[[str], None]) -> Dict[str, str]:
    """指定プロファイルの認証情報と region を取得し、環境変数マッピングとして返す

    aws コマンドの標準エラー出力は report にのみ渡し、クライアント向け例外メッセージには含めない。
    """
    stdout, stderr, code = run_command(build_export_credentials_args(profile))

    if code != 0:
        if is_export_credentials_unavailable(stderr):
            raise CredentialFetchError(
                "aws configure export-credentials が利用できません。AWS CLI v2 (2.9 以降) が必要です"
            )
        report(f"export-credentials が終了コード {code} で失敗しました: {stderr.strip()}")
        raise CredentialFetchError(
            f"認証情報の取得に失敗しました (終了コード {code})。詳細はサーバーログを参照してください"
        )

    credentials = parse_export_credentials_output(stdout)
    region = fetch_region(profile, run_command, report)
    return build_env_mapping(credentials, region)


def build_subprocess_env(environ: Mapping[str, str]) -> Dict[str, str]:
    """aws サブプロセスへ引き継ぐ環境変数だけを抽出する"""
    return {key: environ[key] for key in SUBPROCESS_ENV_KEYS if key in environ}


def resolve_timeout_sec(environ: Mapping[str, str]) -> int:
    """環境変数からサブプロセスのタイムアウト秒数を決定する"""
    configured = environ.get(TIMEOUT_ENV)
    if configured is None:
        return DEFAULT_TIMEOUT_SEC

    try:
        return int(configured)
    except ValueError:
        raise ConfigError(f"{TIMEOUT_ENV} は整数である必要があります: {configured}")


def create_run_command(timeout_sec: int, env: Mapping[str, str]) -> RunCommand:
    """subprocess.run をラップした RunCommand を作る

    プロセス起動という副作用をここに閉じ込め、呼び出し側には RunCommand として注入する。
    """
    def run_command(command: List[str]) -> Tuple[str, str, int]:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                shell=False,
                stdin=subprocess.DEVNULL,
                env=dict(env),
            )
        except subprocess.TimeoutExpired:
            raise CredentialFetchError(f"コマンド実行がタイムアウトしました ({timeout_sec}秒)")
        except FileNotFoundError:
            raise CredentialFetchError(
                "aws コマンドが見つかりません。AWS CLI v2 (2.9 以降) をインストールしてください"
            )

        return result.stdout, result.stderr, result.returncode

    return run_command


def build_tools(entries: List[CredentialEntry]) -> List[Dict[str, Any]]:
    """MCP tools/list で返すツール定義を組み立てる"""
    return [
        {
            "name": TOOL_NAME,
            "description": (
                "設定ファイルで定義した name に対応する AWS プロファイルの認証情報を取得し、"
                "環境変数名をキーとする JSON オブジェクトで返します。"
                "一時認証情報は AWS_CREDENTIAL_EXPIRATION の時刻で失効するため、"
                "失効後は再取得してください"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "取得する認証情報の名前 (aws-credential.yml の credentials 配下のキー)",
                        "enum": [entry.name for entry in entries],
                    }
                },
                "required": ["name"],
            },
        }
    ]


def validate_arguments(arguments: Any, entries: List[CredentialEntry]) -> CredentialEntry:
    """tools/call の arguments を検証し、対応する CredentialEntry を返す"""
    if not isinstance(arguments, dict):
        raise ValidationError("arguments は辞書である必要があります")

    if "name" not in arguments:
        raise ValidationError("必須フィールドが不足しています: name")

    for field in arguments:
        if field != "name":
            raise ValidationError(f"未知のフィールド: {field}")

    name = arguments["name"]
    if not isinstance(name, str):
        raise ValidationError("name は文字列である必要があります")

    for entry in entries:
        if entry.name == name:
            return entry

    raise ValidationError(f"未知の name: {name}")


def handle_initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    """initialize メソッドの処理"""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {}
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION
        }
    }


def handle_tools_list(tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    """tools/list メソッドの処理"""
    return {
        "tools": tools
    }


def handle_tools_call(
    params: Dict[str, Any],
    entries: List[CredentialEntry],
    run_command: RunCommand,
    report: Callable[[str], None],
) -> Dict[str, Any]:
    """tools/call メソッドの処理"""
    if "name" not in params:
        raise ValidationError("ツール名が指定されていません")

    tool_name = params["name"]
    if tool_name != TOOL_NAME:
        raise ValidationError(f"未知のツール: {tool_name}")

    entry = validate_arguments(params.get("arguments", {}), entries)

    try:
        mapping = fetch_credentials(entry.profile, run_command, report)
    except CredentialFetchError as e:
        return {
            "content": [{"type": "text", "text": f"エラー: {e}"}],
            "isError": True
        }

    return {
        "content": [{"type": "text", "text": json.dumps(mapping, indent=2)}]
    }


def create_error_response(request_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    """JSON-RPCエラーレスポンスを生成"""
    error = {
        "code": code,
        "message": message
    }
    if data is not None:
        error["data"] = data

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error
    }


def create_success_response(request_id: Any, result: Any) -> Dict[str, Any]:
    """JSON-RPC成功レスポンスを生成"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    }


def handle_jsonrpc_request(
    request: Dict[str, Any],
    entries: List[CredentialEntry],
    tools: List[Dict[str, Any]],
    run_command: RunCommand,
    report: Callable[[str], None],
) -> Dict[str, Any]:
    """JSON-RPCリクエストを処理"""
    if request.get("jsonrpc") != "2.0":
        return create_error_response(
            request.get("id"),
            INVALID_REQUEST,
            "jsonrpc フィールドは '2.0' である必要があります"
        )

    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    report(f"method={method}, params={json.dumps(params)}")

    if not method:
        return create_error_response(
            request_id,
            INVALID_REQUEST,
            "method フィールドが必要です"
        )

    try:
        if method == "initialize":
            result = handle_initialize(params)
        elif method == "tools/list":
            result = handle_tools_list(tools)
        elif method == "tools/call":
            result = handle_tools_call(params, entries, run_command, report)
        else:
            return create_error_response(
                request_id,
                METHOD_NOT_FOUND,
                f"未知のメソッド: {method}"
            )

        return create_success_response(request_id, result)

    except ValidationError as e:
        return create_error_response(
            request_id,
            INVALID_PARAMS,
            str(e)
        )
    except Exception as e:
        # 予期しない例外の内容 (秘密情報を含みうる) はクライアントに返さず、report にのみ渡す
        report(f"内部エラー: {type(e).__name__}: {e}")
        return create_error_response(
            request_id,
            INTERNAL_ERROR,
            "内部エラーが発生しました。詳細はサーバーログを参照してください"
        )


def create_application(
    entries: List[CredentialEntry],
    run_command: RunCommand,
    report: Callable[[str], None],
) -> Callable[[Dict[str, Any], Callable[..., None]], List[bytes]]:
    """WSGI アプリケーションを組み立てる

    ツール定義 (tools) はクロージャ生成時に 1 回だけ構築し、リクエストのたびに作り直さない。
    """
    tools = build_tools(entries)

    def application(environ: Dict[str, Any], start_response: Callable[..., None]) -> List[bytes]:
        """WSGI アプリケーション"""
        if environ["REQUEST_METHOD"] != "POST":
            start_response("405 Method Not Allowed", [("Content-Type", "text/plain")])
            return [b"Method Not Allowed"]

        content_type = environ.get("CONTENT_TYPE", "")
        if not content_type.startswith("application/json"):
            start_response("415 Unsupported Media Type", [("Content-Type", "text/plain")])
            return [b"Content-Type must be application/json"]

        content_length = int(environ.get("CONTENT_LENGTH", 0))
        request_body = environ["wsgi.input"].read(content_length)

        try:
            request = json.loads(request_body.decode("utf-8"))
        except json.JSONDecodeError as e:
            response = create_error_response(
                None,
                PARSE_ERROR,
                f"リクエストボディを JSON として解釈できません (位置 {e.pos})"
            )
        else:
            response = handle_jsonrpc_request(request, entries, tools, run_command, report)

        response_body = json.dumps(response).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(response_body)))
        ])
        return [response_body]

    return application


def report_to_stderr(message: str) -> None:
    """report コールバックの既定実装。サーバー側の標準エラー出力に書き出す"""
    print(message, file=sys.stderr, flush=True)


def resolve_bind(environ: Mapping[str, str]) -> str:
    """環境変数から bind アドレスを決定する

    環境変数が設定されていればその値を、無ければ既定値を返す。
    """
    return environ.get(BIND_ENV, DEFAULT_BIND)


def resolve_port(environ: Mapping[str, str]) -> int:
    """環境変数から listen ポート番号を決定する

    環境変数が設定されていればその値を整数に変換して返し、無ければ既定値を返す。
    """
    configured = environ.get(PORT_ENV)
    if configured is None:
        return DEFAULT_PORT

    try:
        return int(configured)
    except ValueError:
        raise ConfigError(f"{PORT_ENV} は整数である必要があります: {configured}")


def main() -> int:
    """メイン関数"""
    environ = os.environ

    config_path = resolve_config_path(environ)
    try:
        entries = load_config(config_path, report_to_stderr)
        timeout_sec = resolve_timeout_sec(environ)
        port = resolve_port(environ)
    except ConfigError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 1

    run_command = create_run_command(timeout_sec, build_subprocess_env(environ))
    bind = resolve_bind(environ)

    print("AWS Credential MCP Proxy Server")
    print(f"Protocol Version: {PROTOCOL_VERSION}")
    print(f"Server: {SERVER_NAME} v{SERVER_VERSION}")
    print(f"設定ファイル: {config_path}")
    print(f"登録された name: {', '.join(entry.name for entry in entries)}")
    print(f"Bind: {bind}:{port}")
    print()
    print("サーバーを起動しています...")

    with make_server(bind, port, create_application(entries, run_command, report_to_stderr)) as httpd:
        print(f"サーバーが起動しました: http://{bind}:{port}")
        print("Ctrl+C で停止します")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nサーバーを停止しています...")

    return 0


if __name__ == "__main__":
    sys.exit(main())
