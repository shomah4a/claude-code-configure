#!/usr/bin/env python3
"""mcp-proxy のユニットテスト"""

import io
import json
import textwrap
import unittest
import urllib.error
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# テスト対象のインポートのためにパスを追加
import sys
sys.path.insert(0, str(Path(__file__).parent))

from importlib import import_module
# ハイフン入りのモジュール名のためimportlibを使用
mcp_proxy = import_module("mcp-proxy")


class 設定ファイル読み込みテスト(unittest.TestCase):

    def _write_yaml(self, tmp_path: Path, content: str) -> Path:
        config_path = tmp_path / "test-config.yaml"
        config_path.write_text(textwrap.dedent(content), encoding="utf-8")
        return config_path

    def test_bearer認証を含む設定を読み込める(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_yaml(Path(tmp), """\
                mcp-servers:
                  newrelic:
                    endpoint: https://mcp.newrelic.com/mcp/
                    type: http
                    auth:
                      type: bearer
                      token: test-token-123
            """)
            servers = mcp_proxy.load_config(config_path)

            self.assertEqual(len(servers), 1)
            self.assertEqual(servers[0].key, "newrelic")
            self.assertEqual(servers[0].endpoint, "https://mcp.newrelic.com/mcp/")
            self.assertEqual(servers[0].transport_type, "http")
            self.assertIsInstance(servers[0].auth, mcp_proxy.AuthBearer)
            self.assertEqual(servers[0].auth.token, "test-token-123")

    def test_header認証を含む設定を読み込める(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_yaml(Path(tmp), """\
                mcp-servers:
                  custom:
                    endpoint: https://example.com/mcp/
                    type: http
                    auth:
                      type: header
                      name: X-Api-Key
                      value: my-api-key
            """)
            servers = mcp_proxy.load_config(config_path)

            self.assertEqual(len(servers), 1)
            self.assertIsInstance(servers[0].auth, mcp_proxy.AuthHeader)
            self.assertEqual(servers[0].auth.name, "X-Api-Key")
            self.assertEqual(servers[0].auth.value, "my-api-key")

    def test_認証なしの設定を読み込める(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_yaml(Path(tmp), """\
                mcp-servers:
                  public:
                    endpoint: https://public.example.com/mcp/
                    type: http
            """)
            servers = mcp_proxy.load_config(config_path)

            self.assertEqual(len(servers), 1)
            self.assertIsNone(servers[0].auth)

    def test_複数サーバーの設定を読み込める(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_yaml(Path(tmp), """\
                mcp-servers:
                  server1:
                    endpoint: https://one.example.com/mcp/
                    type: http
                  server2:
                    endpoint: https://two.example.com/mcp/
                    type: http
                    auth:
                      type: bearer
                      token: token2
            """)
            servers = mcp_proxy.load_config(config_path)

            self.assertEqual(len(servers), 2)
            keys = {s.key for s in servers}
            self.assertEqual(keys, {"server1", "server2"})

    def test_sseタイプのサーバーはスキップされる(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_yaml(Path(tmp), """\
                mcp-servers:
                  sse-server:
                    endpoint: https://sse.example.com/mcp/
                    type: sse
                  http-server:
                    endpoint: https://http.example.com/mcp/
                    type: http
            """)
            servers = mcp_proxy.load_config(config_path)

            self.assertEqual(len(servers), 1)
            self.assertEqual(servers[0].key, "http-server")

    def test_存在しない設定ファイルは空リストを返す(self):
        servers = mcp_proxy.load_config(Path("/nonexistent/config.yaml"))
        self.assertEqual(servers, [])

    def test_endpointが未指定のサーバーはスキップされる(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_yaml(Path(tmp), """\
                mcp-servers:
                  no-endpoint:
                    type: http
                  valid:
                    endpoint: https://valid.example.com/mcp/
                    type: http
            """)
            servers = mcp_proxy.load_config(config_path)

            self.assertEqual(len(servers), 1)
            self.assertEqual(servers[0].key, "valid")


class 認証ヘッダー構築テスト(unittest.TestCase):

    def test_bearer認証のヘッダーが構築される(self):
        server = mcp_proxy.UpstreamServer(
            key="test",
            endpoint="https://example.com/mcp/",
            transport_type="http",
            auth=mcp_proxy.AuthBearer(token="my-token"),
        )
        headers = mcp_proxy.build_upstream_headers(server)

        self.assertEqual(headers["Authorization"], "Bearer my-token")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_header認証のヘッダーが構築される(self):
        server = mcp_proxy.UpstreamServer(
            key="test",
            endpoint="https://example.com/mcp/",
            transport_type="http",
            auth=mcp_proxy.AuthHeader(name="X-Api-Key", value="key-123"),
        )
        headers = mcp_proxy.build_upstream_headers(server)

        self.assertEqual(headers["X-Api-Key"], "key-123")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_認証なしの場合はContentTypeのみ(self):
        server = mcp_proxy.UpstreamServer(
            key="test",
            endpoint="https://example.com/mcp/",
            transport_type="http",
            auth=None,
        )
        headers = mcp_proxy.build_upstream_headers(server)

        self.assertEqual(headers, {"Content-Type": "application/json"})


class パスルーティングテスト(unittest.TestCase):

    def _make_servers(self) -> Dict[str, mcp_proxy.UpstreamServer]:
        return {
            "newrelic": mcp_proxy.UpstreamServer(
                key="newrelic",
                endpoint="https://mcp.newrelic.com/mcp/",
                transport_type="http",
            ),
            "other": mcp_proxy.UpstreamServer(
                key="other",
                endpoint="https://other.example.com/mcp/",
                transport_type="http",
            ),
        }

    def test_パスからサーバーを解決できる(self):
        servers = self._make_servers()
        result = mcp_proxy.resolve_server(servers, "/newrelic")
        self.assertEqual(result.key, "newrelic")

    def test_存在しないパスはNoneを返す(self):
        servers = self._make_servers()
        result = mcp_proxy.resolve_server(servers, "/unknown")
        self.assertIsNone(result)

    def test_ルートパスはNoneを返す(self):
        servers = self._make_servers()
        result = mcp_proxy.resolve_server(servers, "/")
        self.assertIsNone(result)


class MCP_JSON生成テスト(unittest.TestCase):

    def test_サーバーリストからJSON文字列を生成できる(self):
        servers = [
            mcp_proxy.UpstreamServer(
                key="newrelic",
                endpoint="https://mcp.newrelic.com/mcp/",
                transport_type="http",
            ),
            mcp_proxy.UpstreamServer(
                key="other",
                endpoint="https://other.example.com/mcp/",
                transport_type="http",
            ),
        ]
        result = json.loads(mcp_proxy.generate_mcp_json(servers))

        self.assertIn("mcpServers", result)
        self.assertEqual(
            result["mcpServers"]["newrelic"]["url"],
            f"http://localhost:{mcp_proxy.PORT}/newrelic",
        )
        self.assertEqual(
            result["mcpServers"]["other"]["url"],
            f"http://localhost:{mcp_proxy.PORT}/other",
        )


def _build_environ(
    method: str = "POST",
    path: str = "/",
    content_type: str = "application/json",
    body: bytes = b"",
) -> Dict[str, Any]:
    """WSGIテスト用のenviron辞書を構築する"""
    return {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }


class _ResponseCapture:
    """start_responseの呼び出しをキャプチャする"""

    def __init__(self):
        self.status: Optional[str] = None
        self.headers: List[Tuple[str, str]] = []

    def __call__(self, status: str, headers: List[Tuple[str, str]]) -> None:
        self.status = status
        self.headers = headers


def _make_app(
    forward_func: Callable = None,
) -> mcp_proxy.McpProxyApp:
    """テスト用のMcpProxyAppを構築する"""
    servers = [
        mcp_proxy.UpstreamServer(
            key="test-server",
            endpoint="https://test.example.com/mcp/",
            transport_type="http",
        ),
    ]
    if forward_func is None:
        forward_func = lambda server, body, timeout: b'{"jsonrpc":"2.0","id":1,"result":{}}'
    return mcp_proxy.McpProxyApp(servers, 30, forward_func=forward_func)


class WSGIハンドラテスト(unittest.TestCase):

    def test_GETリクエストは405を返す(self):
        app = _make_app()
        environ = _build_environ(method="GET", path="/test-server")
        resp = _ResponseCapture()

        body = app(environ, resp)

        self.assertEqual(resp.status, "405 Method Not Allowed")

    def test_JSONでないContentTypeは415を返す(self):
        app = _make_app()
        environ = _build_environ(
            path="/test-server", content_type="text/plain"
        )
        resp = _ResponseCapture()

        body = app(environ, resp)

        self.assertEqual(resp.status, "415 Unsupported Media Type")

    def test_存在しないパスは404を返す(self):
        app = _make_app()
        environ = _build_environ(path="/unknown")
        resp = _ResponseCapture()

        body = app(environ, resp)

        self.assertEqual(resp.status, "404 Not Found")

    def test_正常なリクエストは上流のレスポンスをそのまま返す(self):
        upstream_response = b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}'

        def fake_forward(server, body, timeout):
            return upstream_response

        app = _make_app(forward_func=fake_forward)
        request_body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        environ = _build_environ(path="/test-server", body=request_body)
        resp = _ResponseCapture()

        body = app(environ, resp)

        self.assertEqual(resp.status, "200 OK")
        self.assertEqual(body, [upstream_response])

    def test_転送関数にサーバー情報とリクエストボディが渡される(self):
        received = {}

        def capturing_forward(server, body, timeout):
            received["server_key"] = server.key
            received["body"] = body
            received["timeout"] = timeout
            return b'{"jsonrpc":"2.0","id":1,"result":{}}'

        app = _make_app(forward_func=capturing_forward)
        request_body = b'{"jsonrpc":"2.0","id":1,"method":"initialize"}'
        environ = _build_environ(path="/test-server", body=request_body)
        resp = _ResponseCapture()

        app(environ, resp)

        self.assertEqual(received["server_key"], "test-server")
        self.assertEqual(received["body"], request_body)
        self.assertEqual(received["timeout"], 30)

    def test_上流HTTPError時は502とJSON_RPCエラーを返す(self):
        def error_forward(server, body, timeout):
            raise urllib.error.HTTPError(
                url="https://test.example.com/mcp/",
                code=500,
                msg="Internal Server Error",
                hdrs={},
                fp=io.BytesIO(b"upstream error"),
            )

        app = _make_app(forward_func=error_forward)
        request_body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        environ = _build_environ(path="/test-server", body=request_body)
        resp = _ResponseCapture()

        body = app(environ, resp)

        self.assertEqual(resp.status, "502 Bad Gateway")
        error_json = json.loads(body[0])
        self.assertEqual(error_json["error"]["code"], -32603)
        self.assertIn("500", error_json["error"]["message"])

    def test_上流URLError時は502とJSON_RPCエラーを返す(self):
        def error_forward(server, body, timeout):
            raise urllib.error.URLError(reason="Connection refused")

        app = _make_app(forward_func=error_forward)
        request_body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        environ = _build_environ(path="/test-server", body=request_body)
        resp = _ResponseCapture()

        body = app(environ, resp)

        self.assertEqual(resp.status, "502 Bad Gateway")
        error_json = json.loads(body[0])
        self.assertEqual(error_json["error"]["code"], -32603)
        self.assertIn("Connection refused", error_json["error"]["message"])


class ツールフィルタリングテスト(unittest.TestCase):

    def _server(
        self, allow_tools: List[str] = None, deny_tools: List[str] = None
    ) -> mcp_proxy.UpstreamServer:
        return mcp_proxy.UpstreamServer(
            key="test",
            endpoint="https://example.com/mcp/",
            transport_type="http",
            allow_tools=allow_tools or [],
            deny_tools=deny_tools or [],
        )

    def test_指定なしの場合は全ツールが許可される(self):
        server = self._server()
        self.assertTrue(mcp_proxy.is_tool_allowed("any_tool", server))

    def test_allowに完全一致するツールは許可される(self):
        server = self._server(allow_tools=["query", "list"])
        self.assertTrue(mcp_proxy.is_tool_allowed("query", server))

    def test_allowに含まれないツールは拒否される(self):
        server = self._server(allow_tools=["query", "list"])
        self.assertFalse(mcp_proxy.is_tool_allowed("delete", server))

    def test_allowのglobパターンにマッチするツールは許可される(self):
        server = self._server(allow_tools=["query_*"])
        self.assertTrue(mcp_proxy.is_tool_allowed("query_nrql", server))
        self.assertFalse(mcp_proxy.is_tool_allowed("delete_dashboard", server))

    def test_denyに一致するツールは拒否される(self):
        server = self._server(deny_tools=["delete"])
        self.assertFalse(mcp_proxy.is_tool_allowed("delete", server))
        self.assertTrue(mcp_proxy.is_tool_allowed("query", server))

    def test_denyのglobパターンにマッチするツールは拒否される(self):
        server = self._server(deny_tools=["delete_*"])
        self.assertFalse(mcp_proxy.is_tool_allowed("delete_dashboard", server))
        self.assertTrue(mcp_proxy.is_tool_allowed("query_nrql", server))

    def test_allowとdeny両方指定時はdenyが優先される(self):
        server = self._server(
            allow_tools=["query_*"],
            deny_tools=["query_dangerous"],
        )
        self.assertTrue(mcp_proxy.is_tool_allowed("query_nrql", server))
        self.assertFalse(mcp_proxy.is_tool_allowed("query_dangerous", server))
        self.assertFalse(mcp_proxy.is_tool_allowed("delete", server))


class ツールリストフィルタリングテスト(unittest.TestCase):

    def _server(
        self, allow_tools: List[str] = None, deny_tools: List[str] = None
    ) -> mcp_proxy.UpstreamServer:
        return mcp_proxy.UpstreamServer(
            key="test",
            endpoint="https://example.com/mcp/",
            transport_type="http",
            allow_tools=allow_tools or [],
            deny_tools=deny_tools or [],
        )

    def test_フィルタ未設定の場合はレスポンスがそのまま返る(self):
        server = self._server()
        response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {"name": "query"},
                    {"name": "delete"},
                ]
            }
        }).encode("utf-8")

        result = mcp_proxy.filter_tools_list_response(response, server)
        self.assertEqual(result, response)

    def test_allowに基づいてツールがフィルタリングされる(self):
        server = self._server(allow_tools=["query_*"])
        response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {"name": "query_nrql"},
                    {"name": "delete_dashboard"},
                ]
            }
        }).encode("utf-8")

        result = json.loads(mcp_proxy.filter_tools_list_response(response, server))
        tool_names = [t["name"] for t in result["result"]["tools"]]
        self.assertEqual(tool_names, ["query_nrql"])

    def test_denyに基づいてツールがフィルタリングされる(self):
        server = self._server(deny_tools=["delete_*"])
        response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {"name": "query_nrql"},
                    {"name": "delete_dashboard"},
                ]
            }
        }).encode("utf-8")

        result = json.loads(mcp_proxy.filter_tools_list_response(response, server))
        tool_names = [t["name"] for t in result["result"]["tools"]]
        self.assertEqual(tool_names, ["query_nrql"])


class WSGIハンドラツールフィルタテスト(unittest.TestCase):

    def _make_filtered_app(
        self,
        allow_tools: List[str] = None,
        deny_tools: List[str] = None,
        forward_func: Callable = None,
    ) -> mcp_proxy.McpProxyApp:
        servers = [
            mcp_proxy.UpstreamServer(
                key="test-server",
                endpoint="https://test.example.com/mcp/",
                transport_type="http",
                allow_tools=allow_tools or [],
                deny_tools=deny_tools or [],
            ),
        ]
        if forward_func is None:
            forward_func = lambda s, b, t: b'{"jsonrpc":"2.0","id":1,"result":{}}'
        return mcp_proxy.McpProxyApp(servers, 30, forward_func=forward_func)

    def test_拒否されたツールのcallはエラーを返す(self):
        app = self._make_filtered_app(deny_tools=["delete_*"])
        request_body = json.dumps({
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": "delete_dashboard"},
        }).encode("utf-8")
        environ = _build_environ(path="/test-server", body=request_body)
        resp = _ResponseCapture()

        body = app(environ, resp)

        self.assertEqual(resp.status, "200 OK")
        result = json.loads(body[0])
        self.assertEqual(result["id"], 42)
        self.assertEqual(result["error"]["code"], -32601)

    def test_許可されたツールのcallは上流に転送される(self):
        forwarded = {"called": False}

        def fake_forward(server, body, timeout):
            forwarded["called"] = True
            return b'{"jsonrpc":"2.0","id":1,"result":{"content":[]}}'

        app = self._make_filtered_app(
            deny_tools=["delete_*"], forward_func=fake_forward
        )
        request_body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "query_nrql"},
        }).encode("utf-8")
        environ = _build_environ(path="/test-server", body=request_body)
        resp = _ResponseCapture()

        app(environ, resp)

        self.assertTrue(forwarded["called"])

    def test_tools_listのレスポンスがフィルタリングされる(self):
        upstream_response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "tools": [
                    {"name": "query_nrql", "description": "query"},
                    {"name": "delete_dashboard", "description": "delete"},
                ]
            }
        }).encode("utf-8")

        app = self._make_filtered_app(
            deny_tools=["delete_*"],
            forward_func=lambda s, b, t: upstream_response,
        )
        request_body = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        }).encode("utf-8")
        environ = _build_environ(path="/test-server", body=request_body)
        resp = _ResponseCapture()

        body = app(environ, resp)

        result = json.loads(body[0])
        tool_names = [t["name"] for t in result["result"]["tools"]]
        self.assertEqual(tool_names, ["query_nrql"])


class 認証設定パーステスト(unittest.TestCase):

    def test_Noneを渡すとNoneが返る(self):
        self.assertIsNone(mcp_proxy.parse_auth(None))

    def test_未知の認証タイプでValueErrorが発生する(self):
        with self.assertRaises(ValueError):
            mcp_proxy.parse_auth({"type": "unknown"})

    def test_bearerでtokenが未指定だとValueErrorが発生する(self):
        with self.assertRaises(ValueError):
            mcp_proxy.parse_auth({"type": "bearer"})

    def test_headerでnameが未指定だとValueErrorが発生する(self):
        with self.assertRaises(ValueError):
            mcp_proxy.parse_auth({"type": "header", "value": "v"})

    def test_headerでvalueが未指定だとValueErrorが発生する(self):
        with self.assertRaises(ValueError):
            mcp_proxy.parse_auth({"type": "header", "name": "n"})


if __name__ == "__main__":
    unittest.main()
