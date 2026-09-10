#!/usr/bin/env python3
"""
AWS CLI MCP Proxy Server

設定ファイル (aws-cli-proxy.yml) に記載されたプロファイルを固定して、
ホスト側で aws コマンドを実行し結果を返す HTTP JSON-RPC (MCP) サーバーです。
認証情報はホストの aws プロセス内で解決され、クライアントには渡しません。
"""

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server

import yaml

# サーバー設定
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "aws-cli-proxy"
SERVER_VERSION = "1.0.0"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "aws-cli-proxy.yml"
CONFIG_PATH_ENV = "AWS_CLI_PROXY_CONFIG"
TIMEOUT_ENV = "AWS_CLI_PROXY_TIMEOUT"
DEFAULT_TIMEOUT_SEC = 30
MAX_OUTPUT_BYTES_ENV = "AWS_CLI_PROXY_MAX_OUTPUT_BYTES"
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
# 出力を切り詰めたときに付記する文言
OUTPUT_TRUNCATED_NOTICE = "\n... (出力が上限 {limit} バイトを超えたため切り詰めました。--query や --max-items で絞ってください)"
# AWS CLI v2 の --version 出力の接頭辞
AWS_CLI_V2_VERSION_PREFIX = "aws-cli/2."
BIND_ENV = "AWS_CLI_PROXY_BIND"
DEFAULT_BIND = "127.0.0.1"
PORT_ENV = "AWS_CLI_PROXY_PORT"
DEFAULT_PORT = 30722
TOOL_NAME = "aws_get_credentials"
ALLOWED_HOSTS_ENV = "AWS_CLI_PROXY_ALLOWED_HOSTS"
# loopback を指すホスト名。Host / Origin ヘッダのホスト部がこれ以外なら 403 (DNS リバインディング対策)
DEFAULT_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

# aws configure export-credentials --format process の出力仕様バージョン
EXPECTED_PROCESS_FORMAT_VERSION = 1

# name と profile に共通。先頭の - を拒否して subprocess へのオプション注入を防ぐ
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# aws_run ツールの args に指定させないグローバルオプション。AWS CLI は argparse の allow_abbrev により
# 長オプションの省略形 (--prof, --p 等) を受理し、同名オプションは後勝ちになるため、
# 同じ argparse に拒否対象だけを登録して省略形ごと検出する
DENIED_VALUE_OPTIONS = ("--profile", "--endpoint-url", "--ca-bundle")
DENIED_FLAG_OPTIONS = ("--debug", "--no-verify-ssl")
# ホストの ~/.aws を書き換える・認証情報を出力する・ブラウザや pager を起動するサブコマンド
DENIED_SUBCOMMANDS = frozenset({"configure", "sso", "help"})
# ホストのファイルを参照する引数
DENIED_URI_SCHEMES = ("file://", "fileb://")
DENIED_PATH_PREFIXES = ("/", "~", "./", "../")

# JSON-RPC エラーコード (gh-proxy と同じ)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# (stdout, stderr, returncode) を返すコマンド実行関数。副作用 (プロセス起動) を外部注入するための型
RunCommand = Callable[[List[str]], Tuple[str, str, int]]


@dataclasses.dataclass(frozen=True)
class CommandResult:
    """aws コマンド (argv 全体) の実行結果"""

    stdout: str
    stderr: str
    returncode: int
    truncated: bool


# aws コマンド (argv 全体) を実行して結果を返す関数。副作用 (プロセス起動) を外部注入するための型
AwsCommandRunner = Callable[[Sequence[str]], CommandResult]


@dataclasses.dataclass(frozen=True)
class CredentialEntry:
    """設定ファイルの profiles 配下の 1 エントリ"""

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


class CommandExecutionError(Exception):
    """aws コマンド実行そのものの失敗 (タイムアウト、コマンド不在)。メッセージはクライアントに返す"""


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
    if not isinstance(raw, dict) or "profiles" not in raw:
        raise ConfigError("設定ファイルにprofilesキーがありません")

    profiles = raw["profiles"]
    if not isinstance(profiles, dict):
        raise ConfigError(f"profilesは辞書である必要がありますが{type(profiles).__name__}でした")

    entries: List[CredentialEntry] = []
    for name, conf in profiles.items():
        try:
            entries.append(parse_credential_entry(name, conf))
        except ValueError as e:
            report(f"エントリ '{name}' をスキップします: {e}")

    if not entries:
        raise ConfigError("有効なprofilesエントリがありません")

    return entries


def load_config(config_path: Path, report: Callable[[str], None]) -> List[CredentialEntry]:
    """設定ファイルを読み込み、有効な CredentialEntry のリストを返す"""
    if not config_path.exists():
        raise ConfigError(f"設定ファイルが見つかりません: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError as e:
        raise ConfigError(f"設定ファイルを読み込めません: {config_path} ({e.strerror})")
    except yaml.YAMLError:
        # YAMLError のメッセージは設定ファイルの断片を含むため、パスのみを伝える
        raise ConfigError(f"設定ファイルの YAML 解析に失敗しました: {config_path}")

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


def build_aws_command(profile: str, args: Sequence[str]) -> List[str]:
    """指定プロファイルで aws コマンドを実行する argv 全体を組み立てる"""
    return ["aws", "--profile", profile, *args]


def truncate_output(data: bytes, limit: int) -> Tuple[str, bool]:
    """バイト列を上限バイト数で切り詰めて文字列に変換する

    上限以下ならそのまま decode し、超える場合は先頭 limit バイトを decode したうえで
    切り詰めた旨の注記を付ける。切り詰めにより UTF-8 のマルチバイト文字が境界で
    途切れても errors="replace" で置換文字に変換し、例外にはしない。
    """
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace"), False

    text = data[:limit].decode("utf-8", errors="replace")
    return text + OUTPUT_TRUNCATED_NOTICE.format(limit=limit), True


def resolve_max_output_bytes(environ: Mapping[str, str]) -> int:
    """環境変数から aws コマンド出力の上限バイト数を決定する

    環境変数が設定されていれば整数に変換して返し、無ければ既定値を返す。
    整数に変換できない、または 0 以下の場合は ConfigError を送出する。
    """
    configured = environ.get(MAX_OUTPUT_BYTES_ENV)
    if configured is None:
        return DEFAULT_MAX_OUTPUT_BYTES

    try:
        max_output_bytes = int(configured)
    except ValueError:
        raise ConfigError(f"{MAX_OUTPUT_BYTES_ENV} は整数である必要があります: {configured}")

    if max_output_bytes <= 0:
        raise ConfigError(f"{MAX_OUTPUT_BYTES_ENV} は正の整数である必要があります: {configured}")

    return max_output_bytes


def build_aws_subprocess_env(environ: Mapping[str, str]) -> Dict[str, str]:
    """aws サブプロセスに渡す環境変数を組み立てる

    pager の起動を防ぐため AWS_PAGER を空文字列で上書きする。
    """
    env = dict(environ)
    env["AWS_PAGER"] = ""
    return env


def create_aws_command_runner(
    timeout_sec: int, max_output_bytes: int, env: Mapping[str, str]
) -> AwsCommandRunner:
    """aws コマンドの argv 全体を実行する AwsCommandRunner を作る

    呼び出しごとに一時ディレクトリを作り、その中を cwd として実行する。
    一時ディレクトリの生成と破棄という副作用はこの関数の内側に閉じ、
    呼び出し側は結果 (CommandResult) のみを受け取る。
    """
    def run(command: Sequence[str]) -> CommandResult:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            try:
                result = subprocess.run(
                    list(command),
                    capture_output=True,
                    text=False,
                    timeout=timeout_sec,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    cwd=tmp_dir,
                    env=dict(env),
                )
            except subprocess.TimeoutExpired:
                raise CommandExecutionError(f"コマンド実行がタイムアウトしました ({timeout_sec}秒)")
            except FileNotFoundError:
                raise CommandExecutionError(
                    "aws コマンドが見つかりません。AWS CLI v2 をインストールしてください"
                )

        stdout, stdout_truncated = truncate_output(result.stdout, max_output_bytes)
        stderr, stderr_truncated = truncate_output(result.stderr, max_output_bytes)
        return CommandResult(
            stdout=stdout,
            stderr=stderr,
            returncode=result.returncode,
            truncated=stdout_truncated or stderr_truncated,
        )

    return run


def run_aws(profile: str, args: Sequence[str], runner: AwsCommandRunner) -> CommandResult:
    """指定プロファイルで aws コマンドの argv を組み立てて runner で実行する"""
    return runner(build_aws_command(profile, args))


def verify_aws_cli_v2(runner: AwsCommandRunner) -> str:
    """aws --version を実行し AWS CLI v2 であることを確認してバージョン文字列の先頭行を返す

    AWS CLI v1 はバージョン文字列を標準エラー出力に出すため、stdout / stderr の両方を確認する。
    実行そのものの失敗 (CommandExecutionError) はメッセージを引き継いで ConfigError に変換する。
    """
    try:
        result = runner(["aws", "--version"])
    except CommandExecutionError as e:
        raise ConfigError(str(e))

    output = result.stdout or result.stderr
    first_line = output.splitlines()[0].strip() if output else ""

    is_v2 = result.returncode == 0 and (
        result.stdout.startswith(AWS_CLI_V2_VERSION_PREFIX)
        or result.stderr.startswith(AWS_CLI_V2_VERSION_PREFIX)
    )
    if not is_v2:
        raise ConfigError(f"AWS CLI v2 が必要です (aws --version の出力: {first_line})")

    return first_line


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
                        "description": "取得する認証情報の名前 (aws-cli-proxy.yml の profiles 配下のキー)",
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


def build_denied_option_parser() -> argparse.ArgumentParser:
    """拒否対象のグローバルオプションのみを認識する argparse.ArgumentParser を作る

    add_help=False で -h/--help を無効化し、allow_abbrev=True (既定) で
    AWS CLI と同じ長オプションの省略形解釈を再現する。exit_on_error=False により、
    解析エラー時は原則 SystemExit ではなく argparse.ArgumentError を送出させる。
    """
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=True, exit_on_error=False)
    for option in DENIED_VALUE_OPTIONS:
        parser.add_argument(option, dest=option, default=None)
    for option in DENIED_FLAG_OPTIONS:
        parser.add_argument(option, dest=option, action="store_true", default=False)
    return parser


def find_denied_option(args: Sequence[str]) -> Optional[str]:
    """args に拒否対象のグローバルオプション (値付き・フラグ) が含まれていればオプション名を返す

    省略形 (--prof, --p 等) で指定された場合も allow_abbrev により検出できる。
    値の無い --profile 単独指定など argparse が解析エラーとする入力は、
    どのオプションに該当したかを例外メッセージから特定せず「拒否対象オプションに該当した」
    ものとして固定の文字列を返す。exit_on_error=False でも Python のバージョンによっては
    SystemExit が送出される経路があるため、これも同様に扱う。
    """
    parser = build_denied_option_parser()
    try:
        namespace, _unknown = parser.parse_known_args(list(args))
    except (argparse.ArgumentError, SystemExit):
        return "(拒否対象オプション)"

    for option in DENIED_VALUE_OPTIONS:
        if getattr(namespace, option) is not None:
            return option

    for option in DENIED_FLAG_OPTIONS:
        if getattr(namespace, option):
            return option

    return None


def find_denied_subcommand(args: Sequence[str]) -> Optional[str]:
    """args に DENIED_SUBCOMMANDS のいずれかが含まれていれば、その値を返す (位置を問わない)"""
    for arg in args:
        if arg in DENIED_SUBCOMMANDS:
            return arg
    return None


def find_host_path_reference(args: Sequence[str]) -> Optional[str]:
    """args にホストのファイルを参照する引数が含まれていれば、その値を返す

    小文字化した値が DENIED_URI_SCHEMES のいずれかを含む (--body=fileb://x のような
    オプション連結形式も対象)、DENIED_PATH_PREFIXES のいずれかで始まる、パス途中に /../ を含む、
    ".." と完全一致する、のいずれかを検出する。
    """
    for arg in args:
        lowered = arg.lower()
        if any(scheme in lowered for scheme in DENIED_URI_SCHEMES):
            return arg
        if arg.startswith(DENIED_PATH_PREFIXES):
            return arg
        if "/../" in arg:
            return arg
        if arg == "..":
            return arg
    return None


def validate_aws_args(args: Any) -> None:
    """aws_run ツールが受け取る args (aws --profile <profile> <args...> として実行される) を検証する

    profile やエンドポイントの上書き、ホストの設定変更・認証情報出力を伴うサブコマンド、
    ホストのファイルを参照する引数を拒否する。
    """
    if not isinstance(args, list):
        raise ValidationError("args は文字列の配列である必要があります")

    if not args:
        raise ValidationError("args には 1 つ以上の引数が必要です")

    for i, value in enumerate(args):
        if not isinstance(value, str):
            raise ValidationError(f"args の要素は文字列である必要があります (位置 {i})")
        if value == "" or "\n" in value or "\r" in value or "\0" in value:
            raise ValidationError(f"args に空文字列または改行を含む要素があります (位置 {i})")

    denied_option = find_denied_option(args)
    if denied_option is not None:
        raise ValidationError(f"指定できないオプションです: {denied_option}")

    denied_subcommand = find_denied_subcommand(args)
    if denied_subcommand is not None:
        raise ValidationError(f"指定できないサブコマンドです: {denied_subcommand}")

    host_path_reference = find_host_path_reference(args)
    if host_path_reference is not None:
        raise ValidationError(f"ホストのファイルを参照する引数は指定できません: {host_path_reference}")


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


def extract_host_name(host_header: str) -> str:
    """Host ヘッダ値、または Origin の host[:port] 部分からポートを除いたホスト名を返す

    IPv6 表記 ([::1]:30722) は ] の後ろのポートだけを除き [::1] を返す。
    """
    value = host_header.strip()

    if value.startswith("["):
        closing = value.find("]")
        if closing == -1:
            return value
        return value[:closing + 1]

    if ":" in value:
        return value.rsplit(":", 1)[0]
    return value


def extract_origin_host(origin_header: str) -> str:
    """Origin ヘッダ (scheme://host[:port]) からホスト名を取り出す

    パースできない、または netloc が空の場合は空文字列を返す。
    """
    try:
        netloc = urlsplit(origin_header).netloc
    except ValueError:
        return ""

    if not netloc:
        return ""
    return extract_host_name(netloc)


def is_request_from_allowed_host(environ: Dict[str, Any], allowed_hosts: FrozenSet[str]) -> bool:
    """DNS リバインディング対策として Host / Origin ヘッダを検証する

    Origin ヘッダが無い場合は Host のみで判定する (Claude Code の MCP クライアントは Origin を送らないと想定)。
    """
    host_header = environ.get("HTTP_HOST")
    if not host_header or extract_host_name(host_header) not in allowed_hosts:
        return False

    origin_header = environ.get("HTTP_ORIGIN")
    if not origin_header:
        return True

    if origin_header == "null":
        return False

    return extract_origin_host(origin_header) in allowed_hosts


def create_application(
    entries: List[CredentialEntry],
    run_command: RunCommand,
    report: Callable[[str], None],
    allowed_hosts: FrozenSet[str],
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

        if not is_request_from_allowed_host(environ, allowed_hosts):
            report(
                "許可されていない Host/Origin からのリクエストを拒否しました: "
                f"host={environ.get('HTTP_HOST', '')!r}, origin={environ.get('HTTP_ORIGIN', '')!r}"
            )
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"Forbidden"]

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
            if isinstance(request, dict):
                response = handle_jsonrpc_request(request, entries, tools, run_command, report)
            else:
                response = create_error_response(
                    None,
                    INVALID_REQUEST,
                    "リクエストは JSON オブジェクトである必要があります"
                )

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


def resolve_allowed_hosts(environ: Mapping[str, str]) -> FrozenSet[str]:
    """環境変数から許可する Host/Origin のホスト名集合を決定する

    環境変数が設定されていれば DEFAULT_ALLOWED_HOSTS に追加する。設定されていなければ DEFAULT_ALLOWED_HOSTS のみ。
    """
    configured = environ.get(ALLOWED_HOSTS_ENV)
    if configured is None:
        return DEFAULT_ALLOWED_HOSTS

    additional = {host.strip() for host in configured.split(",") if host.strip()}
    return frozenset(DEFAULT_ALLOWED_HOSTS | additional)


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

    # credential_process (aws-vault 等) がキーリングやエージェントの環境変数を必要とするため、
    # サーバーの環境をそのまま aws に引き継ぐ。--profile を明示しているため AWS_ACCESS_KEY_ID 等による取り違えは起きない
    run_command = create_run_command(timeout_sec, environ)
    bind = resolve_bind(environ)
    allowed_hosts = resolve_allowed_hosts(environ)

    # tool-launcher が stdout を pipe で受けるため、ブロックバッファリングで起動メッセージが滞留しないよう flush する
    print("AWS CLI MCP Proxy Server", flush=True)
    print(f"Protocol Version: {PROTOCOL_VERSION}", flush=True)
    print(f"Server: {SERVER_NAME} v{SERVER_VERSION}", flush=True)
    print(f"設定ファイル: {config_path}", flush=True)
    print(f"登録された name: {', '.join(entry.name for entry in entries)}", flush=True)
    print(f"許可する Host: {', '.join(sorted(allowed_hosts))}", flush=True)
    print(f"Bind: {bind}:{port}", flush=True)
    print(flush=True)
    print("サーバーを起動しています...", flush=True)

    application = create_application(entries, run_command, report_to_stderr, allowed_hosts)
    with make_server(bind, port, application) as httpd:
        print(f"サーバーが起動しました: http://{bind}:{port}", flush=True)
        print("Ctrl+C で停止します", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nサーバーを停止しています...", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
