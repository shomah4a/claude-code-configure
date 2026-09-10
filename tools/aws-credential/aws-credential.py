#!/usr/bin/env python3
"""
AWS Credential MCP Proxy Server

ホスト側の AWS 認証情報を `aws configure export-credentials` で取得し、
MCP ツールとして環境変数名をキーとする JSON で返す HTTP JSON-RPC サーバーです。

設定ファイル (aws-credential.yml) に記載された name ごとに、対応する
AWS プロファイルの認証情報を取得できます。
"""

import dataclasses
import re
from pathlib import Path
from typing import Any, Callable, List, Mapping

import yaml

# サーバー設定
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "aws-credential"
SERVER_VERSION = "1.0.0"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "aws-credential.yml"
CONFIG_PATH_ENV = "AWS_CREDENTIAL_PROXY_CONFIG"

# name と profile に共通。先頭の - を拒否して subprocess へのオプション注入を防ぐ
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclasses.dataclass(frozen=True)
class CredentialEntry:
    """設定ファイルの credentials 配下の 1 エントリ"""

    name: str
    profile: str


class ConfigError(Exception):
    """設定ファイルの致命的な不備 (起動を継続できない)"""


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
