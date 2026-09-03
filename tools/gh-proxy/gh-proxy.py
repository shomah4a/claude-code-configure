#!/usr/bin/env python3
"""
GitHub CLI MCP Proxy Server

Model Context Protocol (MCP) サーバーとして動作し、
GitHub CLI (gh) / git コマンドへの操作を提供します。
readonly操作に加え、git push と Pull Request 作成の書き込み操作を提供します。

このサーバーはHTTP経由でJSON-RPC 2.0メッセージを受け取り、
安全にgh / git コマンドを実行して結果を返します。
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
SERVER_VERSION = "1.2.0"

# JSON-RPCエラーコード
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# ブランチ名の許可パターン
# 先頭の - / : / + を拒否することで、オプション注入・削除refspec・force refspecを防ぐ
BRANCH_NAME_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._/-]*$"

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
        "description": "指定されたPull Requestの詳細情報を取得します。closingIssuesReferences (このPRがクローズ対象とするIssue) を含みます。gh 2.72.0 以上が必要です",
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
        "description": "指定されたIssueの詳細情報を取得します。関連情報として closedByPullRequestsReferences (このIssueをクローズするPR)、parent、subIssues、subIssuesSummary、blockedBy、blocking を含みます。gh 2.94.0 以上が必要です",
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
    },
    {
        "name": "gh_pr_comments",
        "description": "指定されたPull Requestのコメント一覧を取得します",
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
        "name": "gh_issue_comments",
        "description": "指定されたIssueのコメント一覧を取得します",
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
    },
    {
        "name": "gh_pr_create",
        "description": "指定されたリポジトリにPull Requestを作成します",
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
                "branch": {
                    "type": "string",
                    "description": "head となるブランチ名",
                    "pattern": BRANCH_NAME_PATTERN
                },
                "title": {
                    "type": "string",
                    "description": "Pull Request のタイトル",
                    "minLength": 1
                },
                "body": {
                    "type": "string",
                    "description": "Pull Request の本文"
                },
                "base": {
                    "type": "string",
                    "description": "base となるブランチ名。省略時はリポジトリのデフォルトブランチを使用",
                    "pattern": BRANCH_NAME_PATTERN
                }
            },
            "required": ["owner", "repository_name", "branch", "title", "body"]
        }
    },
    {
        "name": "git_push",
        "description": "クローン済みリポジトリのブランチを origin へ push します",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "クローン済みリポジトリルートの絶対パス"
                },
                "branch": {
                    "type": "string",
                    "description": "push するブランチ名",
                    "pattern": BRANCH_NAME_PATTERN
                }
            },
            "required": ["path", "branch"]
        }
    },
    {
        "name": "git_merge_default_branch",
        "description": "クローン済みリポジトリで git fetch --all を実行し、origin のデフォルトブランチを現在のブランチへマージします。コンフリクト時は merge --abort で巻き戻します",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "クローン済みリポジトリルートの絶対パス"
                }
            },
            "required": ["path"]
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


def validate_string_min_length(value: str, min_length: int, field_name: str) -> None:
    """文字列が最小文字数以上か検証"""
    if len(value) < min_length:
        raise ValidationError(
            f"{field_name} は {min_length} 文字以上である必要があります"
        )


def validate_enum(value: str, enum_values: List[str], field_name: str) -> None:
    """文字列が指定された列挙値のいずれかに一致するか検証"""
    if value not in enum_values:
        raise ValidationError(
            f"{field_name} は {', '.join(enum_values)} のいずれかである必要があります: {value}"
        )


def validate_branch_name(branch: str, field_name: str) -> None:
    """ブランチ名が push に安全な形式か検証"""
    validate_string_pattern(branch, BRANCH_NAME_PATTERN, field_name)
    if ".." in branch:
        raise ValidationError(
            f"{field_name} に '..' を含めることはできません: {branch}"
        )


def validate_repository_path(path: str) -> None:
    """クローン済みリポジトリルートのパスか検証"""
    if not os.path.isabs(path):
        raise ValidationError(f"path は絶対パスである必要があります: {path}")
    if not os.path.isdir(path):
        raise ValidationError(f"path のディレクトリが存在しません: {path}")
    if not os.path.exists(os.path.join(path, ".git")):
        raise ValidationError(f"path は git リポジトリのルートではありません: {path}")


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

        # 最小文字数検証
        if "minLength" in prop and isinstance(value, str):
            validate_string_min_length(value, prop["minLength"], field)

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


def run_subprocess(command: List[str], timeout: Optional[int], command_not_found_message: str, cwd: Optional[str] = None) -> Tuple[str, str, int]:
    """
    コマンドを安全に実行

    Args:
        command: 実行するコマンドと引数のリスト
        timeout: タイムアウト（秒）。Noneの場合はGH_PROXY_TIMEOUT環境変数またはデフォルト30秒を使用
        command_not_found_message: コマンドが存在しない場合のエラーメッセージ
        cwd: コマンドを実行する作業ディレクトリ。Noneの場合はサーバープロセスの作業ディレクトリ

    Returns:
        (stdout, stderr, return_code) のタプル
    """
    if timeout is None:
        timeout = TIMEOUT
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            cwd=cwd
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        raise ToolExecutionError(f"コマンド実行がタイムアウトしました（{timeout}秒）")
    except FileNotFoundError:
        raise ToolExecutionError(command_not_found_message)
    except Exception as e:
        raise ToolExecutionError(f"コマンド実行中にエラーが発生しました: {str(e)}")


def execute_gh_command(args: List[str], timeout: int = None, cwd: Optional[str] = None) -> Tuple[str, str, int]:
    """gh コマンドを安全に実行"""
    return run_subprocess(
        ["gh"] + args,
        timeout,
        "gh コマンドが見つかりません。GitHub CLI をインストールしてください",
        cwd=cwd
    )


def execute_git_command(args: List[str], timeout: int = None) -> Tuple[str, str, int]:
    """git コマンドを安全に実行"""
    return run_subprocess(
        ["git"] + args,
        timeout,
        "git コマンドが見つかりません。git をインストールしてください"
    )


def build_gh_repo_view_args(repo: str) -> List[str]:
    """gh_repo_view の gh コマンド引数を組み立てる"""
    return ["repo", "view", repo, "--json", "name,owner,description,url,stargazerCount,forkCount,createdAt,updatedAt"]


def build_gh_pr_list_args(repo: str, arguments: Dict[str, Any]) -> List[str]:
    """gh_pr_list の gh コマンド引数を組み立てる"""
    args = ["pr", "list", "--repo", repo, "--json", "number,title,state,author,createdAt,updatedAt"]

    if "state" in arguments:
        args.extend(["--state", arguments["state"]])

    if "limit" in arguments:
        args.extend(["--limit", str(arguments["limit"])])

    if "search" in arguments:
        args.extend(["--search", arguments["search"]])

    return args


def build_gh_pr_view_args(repo: str, number: int) -> List[str]:
    """gh_pr_view の gh コマンド引数を組み立てる"""
    return ["pr", "view", str(number), "--repo", repo, "--json", "number,title,body,state,author,createdAt,updatedAt,mergeable,mergedAt,closingIssuesReferences"]


def build_gh_issue_list_args(repo: str, arguments: Dict[str, Any]) -> List[str]:
    """gh_issue_list の gh コマンド引数を組み立てる"""
    args = ["issue", "list", "--repo", repo, "--json", "number,title,state,author,createdAt,updatedAt"]

    if "state" in arguments:
        args.extend(["--state", arguments["state"]])

    if "limit" in arguments:
        args.extend(["--limit", str(arguments["limit"])])

    if "search" in arguments:
        args.extend(["--search", arguments["search"]])

    return args


def build_gh_issue_view_args(repo: str, number: int) -> List[str]:
    """gh_issue_view の gh コマンド引数を組み立てる"""
    return ["issue", "view", str(number), "--repo", repo, "--json", "number,title,body,state,author,createdAt,updatedAt,closedByPullRequestsReferences,parent,subIssues,subIssuesSummary,blockedBy,blocking"]


def build_gh_pr_comments_args(repo: str, number: int) -> List[str]:
    """gh_pr_comments の gh コマンド引数を組み立てる"""
    return ["pr", "view", str(number), "--repo", repo, "--json", "comments"]


def build_gh_issue_comments_args(repo: str, number: int) -> List[str]:
    """gh_issue_comments の gh コマンド引数を組み立てる"""
    return ["issue", "view", str(number), "--repo", repo, "--json", "comments"]


def run_gh_tool(args: List[str], error_label: str) -> List[Dict[str, Any]]:
    """gh コマンドを実行し、結果を MCP content 形式で返す"""
    stdout, stderr, code = execute_gh_command(args)

    if code != 0:
        raise ToolExecutionError(f"{error_label}: {stderr}")

    return [{"type": "text", "text": stdout}]


def execute_gh_repo_view(repo: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """gh_repo_view ツールの実行"""
    return run_gh_tool(build_gh_repo_view_args(repo), "gh repo view failed")


def execute_gh_pr_list(repo: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """gh_pr_list ツールの実行"""
    return run_gh_tool(build_gh_pr_list_args(repo, arguments), "gh pr list failed")


def execute_gh_pr_view(repo: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """gh_pr_view ツールの実行"""
    return run_gh_tool(build_gh_pr_view_args(repo, arguments["number"]), "gh pr view failed")


def execute_gh_issue_list(repo: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """gh_issue_list ツールの実行"""
    return run_gh_tool(build_gh_issue_list_args(repo, arguments), "gh issue list failed")


def execute_gh_issue_view(repo: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """gh_issue_view ツールの実行"""
    return run_gh_tool(build_gh_issue_view_args(repo, arguments["number"]), "gh issue view failed")


def execute_gh_pr_comments(repo: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """gh_pr_comments ツールの実行"""
    return run_gh_tool(build_gh_pr_comments_args(repo, arguments["number"]), "gh pr view failed")


def execute_gh_issue_comments(repo: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """gh_issue_comments ツールの実行"""
    return run_gh_tool(build_gh_issue_comments_args(repo, arguments["number"]), "gh issue view failed")


def build_gh_default_branch_args(repo: str) -> List[str]:
    """デフォルトブランチ取得の gh コマンド引数を組み立てる"""
    return ["api", f"repos/{repo}", "--jq", ".default_branch"]


def build_gh_pr_create_args(repo: str, branch: str, title: str, body: str, base: str) -> List[str]:
    """gh_pr_create の gh コマンド引数を組み立てる"""
    return [
        "api", f"repos/{repo}/pulls",
        "-f", f"title={title}",
        "-f", f"head={branch}",
        "-f", f"base={base}",
        "-f", f"body={body}",
    ]


def resolve_default_branch(repo: str) -> str:
    """リポジトリのデフォルトブランチ名を取得する"""
    stdout, stderr, code = execute_gh_command(build_gh_default_branch_args(repo))

    if code != 0:
        raise ToolExecutionError(f"デフォルトブランチの取得に失敗しました: {stderr}")

    default_branch = stdout.strip()
    if not default_branch:
        raise ToolExecutionError("デフォルトブランチを解決できませんでした")

    return default_branch


def execute_gh_pr_create(repo: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """gh_pr_create ツールの実行"""
    branch = arguments["branch"]
    validate_branch_name(branch, "branch")

    if "base" in arguments:
        base = arguments["base"]
        validate_branch_name(base, "base")
    else:
        base = resolve_default_branch(repo)

    args = build_gh_pr_create_args(repo, branch, arguments["title"], arguments["body"], base)
    return run_gh_tool(args, "gh pr create failed")


def build_git_push_args(path: str, branch: str) -> List[str]:
    """git_push の git コマンド引数を組み立てる"""
    return ["-C", path, "push", "origin", branch]


def build_gh_local_default_branch_args() -> List[str]:
    """カレントリポジトリのデフォルトブランチ取得の gh コマンド引数を組み立てる"""
    return ["repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"]


def validate_branch_is_not_default(branch: str, default_branch: str) -> None:
    """push 対象ブランチがデフォルトブランチでないことを検証"""
    if branch == default_branch:
        raise ValidationError(
            f"デフォルトブランチ ({default_branch}) への push は許可されていません"
        )


def resolve_local_repo_default_branch(path: str) -> str:
    """
    path のリポジトリのデフォルトブランチ名を gh で取得する

    デフォルトブランチに依存する判定を確実に行うため、判定できない場合は
    例外を送出して呼び出し元の処理を中断する (fail-closed)。
    """
    stdout, stderr, code = execute_gh_command(build_gh_local_default_branch_args(), cwd=path)

    if code != 0:
        raise ToolExecutionError(
            f"デフォルトブランチの取得に失敗したため処理を中断しました: {stderr}"
        )

    default_branch = stdout.strip()
    if not default_branch:
        raise ToolExecutionError("デフォルトブランチを解決できなかったため処理を中断しました")

    return default_branch


def execute_git_push(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """git_push ツールの実行"""
    path = arguments["path"]
    branch = arguments["branch"]

    validate_repository_path(path)
    validate_branch_name(branch, "branch")
    validate_branch_is_not_default(branch, resolve_local_repo_default_branch(path))

    stdout, stderr, code = execute_git_command(build_git_push_args(path, branch))

    if code != 0:
        raise ToolExecutionError(f"git push failed: {stderr}")

    # git push は進捗や結果を stderr に出力するため、stdout が空の場合は stderr を返す
    output = stdout if stdout.strip() else stderr
    return [{"type": "text", "text": output}]


def build_git_fetch_all_args(path: str) -> List[str]:
    """git_merge_default_branch の fetch フェーズの git コマンド引数を組み立てる"""
    return ["-C", path, "fetch", "--all"]


def build_git_merge_args(path: str, default_branch: str) -> List[str]:
    """git_merge_default_branch の merge フェーズの git コマンド引数を組み立てる"""
    return ["-C", path, "merge", f"origin/{default_branch}", "--no-edit"]


def build_git_merge_abort_args(path: str) -> List[str]:
    """コンフリクト時に merge を巻き戻す git コマンド引数を組み立てる"""
    return ["-C", path, "merge", "--abort"]


def build_git_merge_head_check_args(path: str) -> List[str]:
    """マージ途中状態 (MERGE_HEAD の存在) を確認する git コマンド引数を組み立てる"""
    return ["-C", path, "rev-parse", "-q", "--verify", "MERGE_HEAD"]


def is_merge_in_progress(path: str) -> bool:
    """
    path のリポジトリがマージ途中状態かを MERGE_HEAD の存在で判定する

    git のエラーメッセージはロケールにより翻訳されるため、
    文字列一致ではなく MERGE_HEAD の有無で判定する。
    """
    stdout, stderr, code = execute_git_command(build_git_merge_head_check_args(path))
    return code == 0


def ensure_no_merge_in_progress(path: str) -> None:
    """
    リポジトリがマージ途中状態でないことを検証する

    ツール外で開始された既存のマージを merge --abort で破棄してしまわないよう、
    既にマージ途中の場合は実行前に fail-closed で中断する。
    """
    if is_merge_in_progress(path):
        raise ToolExecutionError(
            "リポジトリが既にマージ途中状態のため実行を中断しました。"
            "コンフリクトを解決するか git merge --abort を実行してください"
        )


def abort_conflicted_merge(path: str) -> None:
    """コンフリクトした merge を --abort で巻き戻す"""
    stdout, stderr, code = execute_git_command(build_git_merge_abort_args(path))

    if code != 0:
        raise ToolExecutionError(
            f"マージがコンフリクトし、merge --abort にも失敗しました。"
            f"リポジトリがマージ途中状態のまま残っています: {stderr}"
        )


def execute_git_merge_default_branch(arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """git_merge_default_branch ツールの実行"""
    path = arguments["path"]

    validate_repository_path(path)
    ensure_no_merge_in_progress(path)

    default_branch = resolve_local_repo_default_branch(path)
    validate_branch_name(default_branch, "default_branch")

    stdout, stderr, code = execute_git_command(build_git_fetch_all_args(path))
    if code != 0:
        raise ToolExecutionError(f"git fetch failed: {stderr}")

    stdout, stderr, code = execute_git_command(build_git_merge_args(path, default_branch))

    if code != 0 and is_merge_in_progress(path):
        abort_conflicted_merge(path)
        raise ToolExecutionError(
            f"マージがコンフリクトしたため merge --abort で巻き戻しました: {stdout}{stderr}"
        )

    if code != 0:
        raise ToolExecutionError(f"git merge failed: {stdout}{stderr}")

    # 既に最新の場合など、git merge が結果を stderr のみに出力するケースに備える
    output = stdout if stdout.strip() else stderr
    return [{"type": "text", "text": output}]


# owner/repository_name を引数に取るツールの実行関数
# 実行関数は (repo, arguments) を受け取る
REPO_TOOL_EXECUTORS = {
    "gh_repo_view": execute_gh_repo_view,
    "gh_pr_list": execute_gh_pr_list,
    "gh_pr_view": execute_gh_pr_view,
    "gh_issue_list": execute_gh_issue_list,
    "gh_issue_view": execute_gh_issue_view,
    "gh_pr_comments": execute_gh_pr_comments,
    "gh_issue_comments": execute_gh_issue_comments,
    "gh_pr_create": execute_gh_pr_create,
}


def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    ツールを実行

    Args:
        tool_name: ツール名
        arguments: ツール引数

    Returns:
        MCP content 形式の結果リスト
    """
    if tool_name == "git_push":
        return execute_git_push(arguments)

    if tool_name == "git_merge_default_branch":
        return execute_git_merge_default_branch(arguments)

    executor = REPO_TOOL_EXECUTORS.get(tool_name)
    if executor is None:
        raise ValidationError(f"未知のツール: {tool_name}")

    repo = f"{arguments['owner']}/{arguments['repository_name']}"
    return executor(repo, arguments)


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

    print(f'method={method}, params={json.dumps(params)}', file=sys.stderr)

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
    content_length = int(environ.get("CONTENT_LENGTH", 0))
    request_body = environ["wsgi.input"].read(content_length)
    request = json.loads(request_body.decode("utf-8"))

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

    with make_server("", PORT, application) as httpd:
        print(f"サーバーが起動しました: http://127.0.0.1:{PORT}")
        print("Ctrl+C で停止します")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nサーバーを停止しています...")


if __name__ == "__main__":
    main()
