#!/usr/bin/env python3
"""
GitHub CLI MCP Proxy Server

Model Context Protocol (MCP) サーバーとして動作し、
GitHub CLI (gh) コマンドへのreadonly操作を提供します。

このサーバーはHTTP経由でJSON-RPC 2.0メッセージを受け取り、
安全にgh コマンドを実行して結果を返します。
"""

import json
import subprocess
import re
import os
import sys
from wsgiref.simple_server import make_server
from typing import Dict, Any, List, Optional, Tuple

# サーバー設定
PORT = int(os.environ.get('GH_PROXY_PORT', '30721'))
TIMEOUT = int(os.environ.get('GH_PROXY_TIMEOUT', '30'))
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "gh-proxy"
SERVER_VERSION = "1.0.0"

# JSON-RPCエラーコード
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# ツール定義
TOOLS = [
    {
        "name": "gh_repo_view",
        "description": "指定されたGitHubリポジトリの情報を取得します",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "リポジトリのオーナー名",
                    "pattern": "^[a-zA-Z0-9][a-zA-Z0-9-]*$"
                },
                "repository_name": {
                    "type": "string",
                    "description": "リポジトリ名",
                    "pattern": "^[a-zA-Z0-9._-]+$"
                }
            },
            "required": ["owner", "repository_name"]
        }
    },
    {
        "name": "gh_pr_list",
        "description": "指定されたリポジトリのPull Request一覧を取得します",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "リポジトリのオーナー名",
                    "pattern": "^[a-zA-Z0-9][a-zA-Z0-9-]*$"
                },
                "repository_name": {
                    "type": "string",
                    "description": "リポジトリ名",
                    "pattern": "^[a-zA-Z0-9._-]+$"
                },
                "state": {
                    "type": "string",
                    "description": "PRの状態",
                    "enum": ["open", "closed", "merged", "all"]
                },
                "limit": {
                    "type": "integer",
                    "description": "取得する最大件数",
                    "minimum": 1,
                    "maximum": 100
                },
                "search": {
                    "type": "string",
                    "description": "検索クエリ（例: created:>2024-01-01, updated:<2024-06-01）"
                }
            },
            "required": ["owner", "repository_name"]
        }
    },
    {
        "name": "gh_pr_view",
        "description": "指定されたPull Requestの詳細情報を取得します",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "リポジトリのオーナー名",
                    "pattern": "^[a-zA-Z0-9][a-zA-Z0-9-]*$"
                },
                "repository_name": {
                    "type": "string",
                    "description": "リポジトリ名",
                    "pattern": "^[a-zA-Z0-9._-]+$"
                },
                "number": {
                    "type": "integer",
                    "description": "PR番号",
                    "minimum": 1
                }
            },
            "required": ["owner", "repository_name", "number"]
        }
    },
    {
        "name": "gh_issue_list",
        "description": "指定されたリポジトリのIssue一覧を取得します",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "リポジトリのオーナー名",
                    "pattern": "^[a-zA-Z0-9][a-zA-Z0-9-]*$"
                },
                "repository_name": {
                    "type": "string",
                    "description": "リポジトリ名",
                    "pattern": "^[a-zA-Z0-9._-]+$"
                },
                "state": {
                    "type": "string",
                    "description": "Issueの状態",
                    "enum": ["open", "closed", "all"]
                },
                "limit": {
                    "type": "integer",
                    "description": "取得する最大件数",
                    "minimum": 1,
                    "maximum": 100
                },
                "search": {
                    "type": "string",
                    "description": "検索クエリ（例: created:>2024-01-01, updated:<2024-06-01）"
                }
            },
            "required": ["owner", "repository_name"]
        }
    },
    {
        "name": "gh_issue_view",
        "description": "指定されたIssueの詳細情報を取得します",
        "inputSchema": {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "リポジトリのオーナー名",
                    "pattern": "^[a-zA-Z0-9][a-zA-Z0-9-]*$"
                },
                "repository_name": {
                    "type": "string",
                    "description": "リポジトリ名",
                    "pattern": "^[a-zA-Z0-9._-]+$"
                },
                "number": {
                    "type": "integer",
                    "description": "Issue番号",
                    "minimum": 1
                }
            },
            "required": ["owner", "repository_name", "number"]
        }
    }
]


class ValidationError(Exception):
    """引数バリデーションエラー"""
    pass


class ToolExecutionError(Exception):
    """ツール実行エラー"""
    pass


def validate_string_pattern(value: str, pattern: str, field_name: str) -> None:
    """文字列が指定されたパターンに一致するか検証"""
    if not re.match(pattern, value):
        raise ValidationError(
            f"{field_name} が無効な形式です: {value}"
        )


def validate_integer_range(value: int, minimum: Optional[int], maximum: Optional[int], field_name: str) -> None:
    """整数が指定された範囲内にあるか検証"""
    if minimum is not None and value < minimum:
        raise ValidationError(
            f"{field_name} は {minimum} 以上である必要があります: {value}"
        )
    if maximum is not None and value > maximum:
        raise ValidationError(
            f"{field_name} は {maximum} 以下である必要があります: {value}"
        )


def validate_enum(value: str, enum_values: List[str], field_name: str) -> None:
    """文字列が指定された列挙値のいずれかに一致するか検証"""
    if value not in enum_values:
        raise ValidationError(
            f"{field_name} は {', '.join(enum_values)} のいずれかである必要があります: {value}"
        )


def validate_arguments(tool_name: str, arguments: Dict[str, Any]) -> None:
    """ツール引数を検証"""
    # ツール定義を取得
    tool_def = next((t for t in TOOLS if t["name"] == tool_name), None)
    if not tool_def:
        raise ValidationError(f"未知のツール: {tool_name}")

    schema = tool_def["inputSchema"]
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # 必須フィールドの確認
    for field in required:
        if field not in arguments:
            raise ValidationError(f"必須フィールドが不足しています: {field}")

    # 各フィールドの検証
    for field, value in arguments.items():
        if field not in properties:
            raise ValidationError(f"未知のフィールド: {field}")

        prop = properties[field]
        prop_type = prop.get("type")

        # 型チェック
        if prop_type == "string" and not isinstance(value, str):
            raise ValidationError(f"{field} は文字列である必要があります")
        elif prop_type == "integer" and not isinstance(value, int):
            raise ValidationError(f"{field} は整数である必要があります")

        # パターン検証
        if "pattern" in prop and isinstance(value, str):
            validate_string_pattern(value, prop["pattern"], field)

        # 列挙値検証
        if "enum" in prop and isinstance(value, str):
            validate_enum(value, prop["enum"], field)

        # 整数範囲検証
        if prop_type == "integer":
            validate_integer_range(
                value,
                prop.get("minimum"),
                prop.get("maximum"),
                field
            )


def execute_gh_command(args: List[str], timeout: int = None) -> Tuple[str, str, int]:
    """
    gh コマンドを安全に実行

    Args:
        args: gh コマンドの引数リスト
        timeout: タイムアウト（秒）。Noneの場合はGH_PROXY_TIMEOUT環境変数またはデフォルト30秒を使用

    Returns:
        (stdout, stderr, return_code) のタプル
    """
    if timeout is None:
        timeout = TIMEOUT
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        raise ToolExecutionError(f"コマンド実行がタイムアウトしました（{timeout}秒）")
    except FileNotFoundError:
        raise ToolExecutionError("gh コマンドが見つかりません。GitHub CLI をインストールしてください")
    except Exception as e:
        raise ToolExecutionError(f"コマンド実行中にエラーが発生しました: {str(e)}")


def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    ツールを実行

    Args:
        tool_name: ツール名
        arguments: ツール引数

    Returns:
        MCP content 形式の結果リスト
    """
    owner = arguments["owner"]
    repo_name = arguments["repository_name"]
    repo = f"{owner}/{repo_name}"

    if tool_name == "gh_repo_view":
        args = ["repo", "view", repo, "--json", "name,owner,description,url,stargazerCount,forkCount,createdAt,updatedAt"]
        stdout, stderr, code = execute_gh_command(args)

        if code != 0:
            raise ToolExecutionError(f"gh repo view failed: {stderr}")

        return [{"type": "text", "text": stdout}]

    elif tool_name == "gh_pr_list":
        args = ["pr", "list", "--repo", repo, "--json", "number,title,state,author,createdAt,updatedAt"]

        if "state" in arguments:
            args.extend(["--state", arguments["state"]])

        if "limit" in arguments:
            args.extend(["--limit", str(arguments["limit"])])

        if "search" in arguments:
            args.extend(["--search", arguments["search"]])

        stdout, stderr, code = execute_gh_command(args)

        if code != 0:
            raise ToolExecutionError(f"gh pr list failed: {stderr}")

        return [{"type": "text", "text": stdout}]

    elif tool_name == "gh_pr_view":
        number = arguments["number"]
        args = ["pr", "view", str(number), "--repo", repo, "--json", "number,title,body,state,author,createdAt,updatedAt,mergeable,mergedAt"]

        stdout, stderr, code = execute_gh_command(args)

        if code != 0:
            raise ToolExecutionError(f"gh pr view failed: {stderr}")

        return [{"type": "text", "text": stdout}]

    elif tool_name == "gh_issue_list":
        args = ["issue", "list", "--repo", repo, "--json", "number,title,state,author,createdAt,updatedAt"]

        if "state" in arguments:
            args.extend(["--state", arguments["state"]])

        if "limit" in arguments:
            args.extend(["--limit", str(arguments["limit"])])

        if "search" in arguments:
            args.extend(["--search", arguments["search"]])

        stdout, stderr, code = execute_gh_command(args)

        if code != 0:
            raise ToolExecutionError(f"gh issue list failed: {stderr}")

        return [{"type": "text", "text": stdout}]

    elif tool_name == "gh_issue_view":
        number = arguments["number"]
        args = ["issue", "view", str(number), "--repo", repo, "--json", "number,title,body,state,author,createdAt,updatedAt"]

        stdout, stderr, code = execute_gh_command(args)

        if code != 0:
            raise ToolExecutionError(f"gh issue view failed: {stderr}")

        return [{"type": "text", "text": stdout}]

    else:
        raise ValidationError(f"未知のツール: {tool_name}")


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


def handle_tools_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """tools/list メソッドの処理"""
    return {
        "tools": TOOLS
    }


def handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """tools/call メソッドの処理"""
    if "name" not in params:
        raise ValidationError("ツール名が指定されていません")

    tool_name = params["name"]
    arguments = params.get("arguments", {})

    # 引数検証
    validate_arguments(tool_name, arguments)

    # ツール実行
    try:
        content = execute_tool(tool_name, arguments)
        return {
            "content": content
        }
    except ToolExecutionError as e:
        return {
            "content": [{"type": "text", "text": f"エラー: {str(e)}"}],
            "isError": True
        }


def create_error_response(request_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    """JSON-RPCエラーレスポンスを生成"""
    error = {
        "code": code,
        "message": message
    }
    if data is not None:
        error["data"] = data

    response = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error
    }
    return response


def create_success_response(request_id: Any, result: Any) -> Dict[str, Any]:
    """JSON-RPC成功レスポンスを生成"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    }


def handle_jsonrpc_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-RPCリクエストを処理"""
    # JSON-RPC 2.0 の基本検証
    if request.get("jsonrpc") != "2.0":
        return create_error_response(
            request.get("id"),
            INVALID_REQUEST,
            "jsonrpc フィールドは '2.0' である必要があります"
        )

    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if not method:
        return create_error_response(
            request_id,
            INVALID_REQUEST,
            "method フィールドが必要です"
        )

    try:
        # メソッドディスパッチ
        if method == "initialize":
            result = handle_initialize(params)
        elif method == "tools/list":
            result = handle_tools_list(params)
        elif method == "tools/call":
            result = handle_tools_call(params)
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
        return create_error_response(
            request_id,
            INTERNAL_ERROR,
            f"内部エラー: {str(e)}"
        )


def application(environ: Dict[str, Any], start_response) -> List[bytes]:
    """WSGI アプリケーション"""
    # POSTメソッドのみ許可
    if environ["REQUEST_METHOD"] != "POST":
        start_response("405 Method Not Allowed", [("Content-Type", "text/plain")])
        return [b"Method Not Allowed"]

    # Content-Typeチェック
    content_type = environ.get("CONTENT_TYPE", "")
    if not content_type.startswith("application/json"):
        start_response("415 Unsupported Media Type", [("Content-Type", "text/plain")])
        return [b"Content-Type must be application/json"]

    # リクエストボディの読み取り
    try:
        content_length = int(environ.get("CONTENT_LENGTH", 0))
        request_body = environ["wsgi.input"].read(content_length)
        request = json.loads(request_body.decode("utf-8"))
    except json.JSONDecodeError:
        response = create_error_response(None, PARSE_ERROR, "JSONのパースに失敗しました")
        response_body = json.dumps(response).encode("utf-8")
        start_response("200 OK", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(response_body)))
        ])
        return [response_body]
    except Exception as e:
        start_response("400 Bad Request", [("Content-Type", "text/plain")])
        return [f"リクエストの読み取りに失敗しました: {str(e)}".encode("utf-8")]

    # JSON-RPCリクエスト処理
    response = handle_jsonrpc_request(request)
    response_body = json.dumps(response).encode("utf-8")

    start_response("200 OK", [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(response_body)))
    ])
    return [response_body]


def main():
    """メイン関数"""
    print(f"GitHub CLI MCP Proxy Server")
    print(f"Protocol Version: {PROTOCOL_VERSION}")
    print(f"Server: {SERVER_NAME} v{SERVER_VERSION}")
    print(f"Port: {PORT}")
    print()
    print("サーバーを起動しています...")

    with make_server("127.0.0.1", PORT, application) as httpd:
        print(f"サーバーが起動しました: http://127.0.0.1:{PORT}")
        print("Ctrl+C で停止します")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nサーバーを停止しています...")


if __name__ == "__main__":
    main()
