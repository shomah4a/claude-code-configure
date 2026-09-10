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
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import yaml

# サーバー設定
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "aws-credential"
SERVER_VERSION = "1.0.0"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "aws-credential.yml"
CONFIG_PATH_ENV = "AWS_CREDENTIAL_PROXY_CONFIG"
TIMEOUT_ENV = "AWS_CREDENTIAL_PROXY_TIMEOUT"
DEFAULT_TIMEOUT_SEC = 30

# aws configure export-credentials --format process の出力仕様バージョン
EXPECTED_PROCESS_FORMAT_VERSION = 1

# aws サブプロセスへ引き継ぐ環境変数。AWS_* を含む他の変数は渡さず、起動シェルの環境で解決結果が変わらないようにする
SUBPROCESS_ENV_KEYS = ("HOME", "PATH", "LANG", "LC_ALL")

# name と profile に共通。先頭の - を拒否して subprocess へのオプション注入を防ぐ
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

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
