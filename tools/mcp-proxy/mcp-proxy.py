#!/usr/bin/env python3
"""
MCP Proxy Server

インターネット上のMCPサーバーへのリクエストを中継する透過プロキシ。
YAML設定ファイルで定義された上流サーバーに対し、
認証ヘッダーを付与してJSON-RPCリクエストをそのまま転送する。
"""

import argparse
import dataclasses
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from wsgiref.simple_server import make_server

import yaml


PORT = 38247
TIMEOUT_SEC = 30
DEFAULT_CONFIG_PATH = Path.home() / ".mcp-proxy.d" / "mcp-servers.yml"

# Claude Codeのheaders helper仕様に合わせたタイムアウト
# https://code.claude.com/docs/en/mcp.md
HEADERS_HELPER_TIMEOUT_SEC = 10
HEADERS_HELPER_CACHE_TTL_SEC = 300


@dataclasses.dataclass(frozen=True)
class AuthBearer:
    token: str


@dataclasses.dataclass(frozen=True)
class AuthHeader:
    headers: Dict[str, str]


@dataclasses.dataclass(frozen=True)
class UpstreamServer:
    key: str
    endpoint: str
    transport_type: str
    auth: Optional[Union[AuthBearer, AuthHeader]] = None
    allow_tools: List[str] = dataclasses.field(default_factory=list)
    deny_tools: List[str] = dataclasses.field(default_factory=list)
    headers_helper: Optional[str] = None


def parse_auth(auth_config: Optional[Dict[str, Any]]) -> Optional[Union[AuthBearer, AuthHeader]]:
    """認証設定をパースする"""
    if auth_config is None:
        return None

    auth_type = auth_config.get("type")
    if auth_type == "bearer":
        token = auth_config.get("token")
        if not token:
            raise ValueError("bearer認証にはtokenが必要です")
        return AuthBearer(token=token)

    if auth_type == "header":
        headers = auth_config.get("headers")
        if not headers or not isinstance(headers, dict):
            raise ValueError("header認証にはheaders（辞書）が必要です")
        return AuthHeader(headers=headers)

    raise ValueError(f"未知の認証タイプ: {auth_type}")


def load_config(config_path: Path) -> List[UpstreamServer]:
    """YAML設定ファイルを読み込み、上流サーバーのリストを返す"""
    if not config_path.exists():
        print(f"設定ファイルが見つかりません: {config_path}", file=sys.stderr)
        return []

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw or "mcp-servers" not in raw:
        print("設定ファイルにmcp-serversキーがありません", file=sys.stderr)
        return []

    servers = []
    for key, conf in raw["mcp-servers"].items():
        endpoint = conf.get("endpoint")
        if not endpoint:
            print(f"サーバー '{key}' にendpointが指定されていません", file=sys.stderr)
            continue

        transport_type = conf.get("type", "http")
        if transport_type not in ("http", "sse"):
            print(
                f"サーバー '{key}' の未知のタイプ: {transport_type}",
                file=sys.stderr,
            )
            continue

        if transport_type == "sse":
            print(
                f"サーバー '{key}' はSSEタイプですが、現在はHTTPのみ対応しています。スキップします",
                file=sys.stderr,
            )
            continue

        headers_helper = conf.get("headers-helper")
        if headers_helper is not None and not isinstance(headers_helper, str):
            print(
                f"サーバー '{key}' のheaders-helperは文字列である必要があります。スキップします",
                file=sys.stderr,
            )
            continue

        auth = parse_auth(conf.get("auth"))
        allow_tools = conf.get("allow-tools", [])
        deny_tools = conf.get("deny-tools", [])
        servers.append(
            UpstreamServer(
                key=key,
                endpoint=endpoint,
                transport_type=transport_type,
                auth=auth,
                allow_tools=allow_tools,
                deny_tools=deny_tools,
                headers_helper=headers_helper,
            )
        )

    return servers


def is_tool_allowed(tool_name: str, server: UpstreamServer) -> bool:
    """ツール名がサーバーのallow/deny設定で許可されているか判定する"""
    allowed = True

    if server.allow_tools:
        allowed = any(
            fnmatch.fnmatch(tool_name, pattern)
            for pattern in server.allow_tools
        )

    if allowed and server.deny_tools:
        denied = any(
            fnmatch.fnmatch(tool_name, pattern)
            for pattern in server.deny_tools
        )
        if denied:
            allowed = False

    return allowed


def filter_tools_list_response(
    response_body: bytes, server: UpstreamServer
) -> bytes:
    """tools/listレスポンスからallow/denyに基づいてツールをフィルタリングする"""
    if not server.allow_tools and not server.deny_tools:
        return response_body

    data = json.loads(response_body)
    result = data.get("result")
    if result is None or "tools" not in result:
        return response_body

    result["tools"] = [
        tool for tool in result["tools"]
        if is_tool_allowed(tool.get("name", ""), server)
    ]
    return json.dumps(data).encode("utf-8")


_HOP_BY_HOP_HEADERS = frozenset({
    "Host",
    "Connection",
    "Keep-Alive",
    "Transfer-Encoding",
    "Te",
    "Trailer",
    "Upgrade",
    "Proxy-Authorization",
    "Proxy-Authenticate",
    "Content-Length",
})


def extract_client_headers(environ: Dict[str, Any]) -> Dict[str, str]:
    """WSGIのenvironからクライアントが送信したHTTPヘッダーを抽出する

    プロキシとして上流に転送すべきでないホップバイホップヘッダーは除外する。
    """
    headers = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            header_name = key[5:].replace("_", "-").title()
            if header_name not in _HOP_BY_HOP_HEADERS:
                headers[header_name] = value
    if "CONTENT_TYPE" in environ:
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    return headers


# RFC 7230のtoken文字。上流へのヘッダーインジェクションを防ぐため
# helper出力のヘッダー名をこの文字集合に限定する
_HEADER_NAME_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HEADER_VALUE_FORBIDDEN_RE = re.compile(r"[\r\n\0]")


class HeadersHelperError(Exception):
    """headers-helperコマンドの実行または出力検証の失敗"""


def parse_headers_helper_output(output: str) -> Dict[str, str]:
    """headers-helperコマンドのstdoutを検証し、ヘッダー辞書に変換する

    契約: 文字列key-valueのJSONオブジェクト。
    ヘッダー名はRFC 7230 token文字のみ、値に制御文字を含む場合は拒否する。
    """
    try:
        data = json.loads(output)
    except json.JSONDecodeError as e:
        raise HeadersHelperError(
            f"headers-helperの出力がJSONではありません: {e}"
        ) from e

    if not isinstance(data, dict):
        raise HeadersHelperError(
            "headers-helperの出力はJSONオブジェクトである必要があります"
        )

    headers = {}
    for name, value in data.items():
        if not isinstance(value, str):
            raise HeadersHelperError(
                f"ヘッダー値が文字列ではありません: {name}"
            )
        if not _HEADER_NAME_TOKEN_RE.match(name):
            raise HeadersHelperError(f"不正なヘッダー名です: {name!r}")
        if _HEADER_VALUE_FORBIDDEN_RE.search(value):
            raise HeadersHelperError(
                f"ヘッダー値に制御文字が含まれています: {name}"
            )
        headers[name] = value
    return headers


def run_headers_helper(command: str, timeout_sec: int) -> Dict[str, str]:
    """headers-helperコマンドをシェルで実行し、動的ヘッダーの辞書を返す

    コマンド文字列は設定ファイル由来の静的な値のみを渡すこと。
    リクエスト由来のデータを補間してはならない (シェルインジェクション防止)。
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        raise HeadersHelperError(
            f"headers-helperがタイムアウトしました ({timeout_sec}秒)"
        ) from e

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise HeadersHelperError(
            f"headers-helperが失敗しました (exit {result.returncode}): {stderr}"
        )

    stdout = result.stdout.decode("utf-8", errors="replace")
    return parse_headers_helper_output(stdout)


HelperRunner = Callable[[str, int], Dict[str, str]]


class HeadersHelperCache:
    """headers-helperの実行結果をサーバー単位でTTLキャッシュする

    副作用の外部化のため、helper実行関数と現在時刻取得関数
    (monotonic clock想定、秒単位) はコンストラクタで注入する。
    """

    def __init__(
        self,
        runner: HelperRunner,
        ttl_sec: float,
        now_func: Callable[[], float],
    ):
        self._runner = runner
        self._ttl_sec = ttl_sec
        self._now = now_func
        self._entries: Dict[str, Tuple[float, Dict[str, str]]] = {}

    def get(self, server: UpstreamServer) -> Dict[str, str]:
        """サーバーの動的ヘッダーを返す。TTL内はキャッシュを利用する"""
        if server.headers_helper is None:
            return {}

        now = self._now()
        entry = self._entries.get(server.key)
        if entry is not None and now < entry[0]:
            return entry[1]

        headers = self._runner(
            server.headers_helper, HEADERS_HELPER_TIMEOUT_SEC
        )
        self._entries[server.key] = (now + self._ttl_sec, headers)
        return headers


def build_upstream_headers(
    server: UpstreamServer,
    client_headers: Dict[str, str],
    helper_headers: Dict[str, str],
) -> Dict[str, str]:
    """上流サーバーへのリクエストに付与するヘッダーを構築する

    優先順位（後勝ち）:
    1. プロキシのデフォルト (Content-Type, Accept)
    2. クライアントからのパススルーヘッダー
    3. YAML認証ヘッダー
    4. headers-helperによる動的ヘッダー
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    headers.update(client_headers)

    if isinstance(server.auth, AuthBearer):
        headers["Authorization"] = f"Bearer {server.auth.token}"
    elif isinstance(server.auth, AuthHeader):
        headers.update(server.auth.headers)

    headers.update(helper_headers)

    return headers


def forward_request(
    server: UpstreamServer,
    request_body: bytes,
    timeout_sec: int,
    client_headers: Dict[str, str],
    helper_headers: Dict[str, str],
) -> bytes:
    """リクエストを上流サーバーに転送し、レスポンスを返す"""
    headers = build_upstream_headers(server, client_headers, helper_headers)
    req = urllib.request.Request(
        server.endpoint,
        data=request_body,
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return resp.read()


def resolve_server(
    servers: Dict[str, UpstreamServer], path: str
) -> Optional[UpstreamServer]:
    """リクエストパスから対応する上流サーバーを解決する"""
    key = path.strip("/")
    return servers.get(key)


def generate_mcp_json(servers: List[UpstreamServer]) -> str:
    """設定からmcp-servers.json相当の内容を生成する"""
    mcp_servers = {}
    for server in servers:
        mcp_servers[server.key] = {
            "type": "http",
            "url": f"http://localhost:{PORT}/{server.key}",
        }
    return json.dumps({"mcpServers": mcp_servers}, indent=2, ensure_ascii=False)


ForwardFunc = Callable[
    [UpstreamServer, bytes, int, Dict[str, str], Dict[str, str]], bytes
]
HelperHeadersFunc = Callable[[UpstreamServer], Dict[str, str]]


class McpProxyApp:
    """WSGI MCPプロキシアプリケーション"""

    def __init__(
        self,
        servers: List[UpstreamServer],
        timeout_sec: int,
        forward_func: ForwardFunc = forward_request,
        *,
        helper_headers_func: HelperHeadersFunc,
    ):
        self._servers = {s.key: s for s in servers}
        self._timeout_sec = timeout_sec
        self._forward = forward_func
        self._helper_headers = helper_headers_func

    def __call__(
        self, environ: Dict[str, Any], start_response
    ) -> List[bytes]:
        if environ["REQUEST_METHOD"] != "POST":
            start_response(
                "405 Method Not Allowed", [("Content-Type", "text/plain")]
            )
            return [b"Method Not Allowed"]

        content_type = environ.get("CONTENT_TYPE", "")
        if not content_type.startswith("application/json"):
            start_response(
                "415 Unsupported Media Type",
                [("Content-Type", "text/plain")],
            )
            return [b"Content-Type must be application/json"]

        path = environ.get("PATH_INFO", "/")
        server = resolve_server(self._servers, path)
        if server is None:
            start_response(
                "404 Not Found", [("Content-Type", "text/plain")]
            )
            return [f"未知のサーバー: {path}".encode("utf-8")]

        content_length = int(environ.get("CONTENT_LENGTH", 0))
        request_body = environ["wsgi.input"].read(content_length)

        # tools/call時のアクセス制御
        request_data = json.loads(request_body)
        method = request_data.get("method", "")
        request_id = request_data.get("id")

        if method == "tools/call":
            tool_name = request_data.get("params", {}).get("name", "")
            if not is_tool_allowed(tool_name, server):
                print(
                    f"[{server.key}] ツール拒否: {tool_name}",
                    file=sys.stderr,
                )
                start_response(
                    "200 OK",
                    [("Content-Type", "application/json")],
                )
                error_response = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"ツール '{tool_name}' は許可されていません",
                        },
                    }
                )
                return [error_response.encode("utf-8")]

        client_headers = extract_client_headers(environ)

        try:
            helper_headers = self._helper_headers(server)
        except HeadersHelperError as e:
            # 詳細 (stderrや出力断片) は秘匿情報を含み得るため
            # サーバー側ログのみに出力し、クライアントには固定文言を返す
            print(
                f"[{server.key}] headers-helperエラー: {e}",
                file=sys.stderr,
            )
            start_response(
                "502 Bad Gateway", [("Content-Type", "application/json")]
            )
            error_response = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32603,
                        "message": "headers-helper実行エラー",
                    },
                }
            )
            return [error_response.encode("utf-8")]

        print(
            f"[{server.key}] 転送: {server.endpoint}",
            file=sys.stderr,
        )

        try:
            response_body = self._forward(
                server,
                request_body,
                self._timeout_sec,
                client_headers,
                helper_headers,
            )
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            print(
                f"[{server.key}] 上流エラー: {e.code} {error_body}",
                file=sys.stderr,
            )
            start_response(
                "502 Bad Gateway", [("Content-Type", "application/json")]
            )
            error_response = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32603,
                        "message": f"上流サーバーエラー: {e.code}",
                    },
                }
            )
            return [error_response.encode("utf-8")]
        except urllib.error.URLError as e:
            print(
                f"[{server.key}] 接続エラー: {e.reason}",
                file=sys.stderr,
            )
            start_response(
                "502 Bad Gateway", [("Content-Type", "application/json")]
            )
            error_response = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32603,
                        "message": f"上流サーバー接続エラー: {e.reason}",
                    },
                }
            )
            return [error_response.encode("utf-8")]

        # tools/listレスポンスのフィルタリング
        if method == "tools/list":
            response_body = filter_tools_list_response(
                response_body, server
            )

        start_response(
            "200 OK",
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(response_body))),
            ],
        )
        return [response_body]


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Proxy Server")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"設定ファイルのパス (デフォルト: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--generate-mcp-json",
        action="store_true",
        help="mcp-servers.json相当の内容をstdoutに出力して終了",
    )
    args = parser.parse_args()

    servers = load_config(args.config)
    if not servers:
        print("有効な上流サーバーがありません", file=sys.stderr)
        return 1

    if args.generate_mcp_json:
        print(generate_mcp_json(servers))
        return 0

    helper_cache = HeadersHelperCache(
        runner=run_headers_helper,
        ttl_sec=HEADERS_HELPER_CACHE_TTL_SEC,
        now_func=time.monotonic,
    )
    app = McpProxyApp(
        servers, TIMEOUT_SEC, helper_headers_func=helper_cache.get
    )

    print(f"MCP Proxy Server", file=sys.stderr)
    print(f"Port: {PORT}", file=sys.stderr)
    print(f"上流サーバー:", file=sys.stderr)
    for server in servers:
        print(f"  /{server.key} -> {server.endpoint}", file=sys.stderr)
    print(file=sys.stderr)

    with make_server("", PORT, app) as httpd:
        print(
            f"サーバーが起動しました: http://127.0.0.1:{PORT}",
            file=sys.stderr,
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nサーバーを停止しています...", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
