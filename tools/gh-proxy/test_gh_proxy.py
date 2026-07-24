#!/usr/bin/env python3
"""gh-proxy.py のユニットテスト

ファイル名にハイフンを含むため、importlib で直接ロードする。
"""

import importlib.util
import os
import tempfile
import unittest

_MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gh-proxy.py")
_SPEC = importlib.util.spec_from_file_location("gh_proxy", _MODULE_PATH)
gh_proxy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gh_proxy)


class BuildGhCommandArgsTest(unittest.TestCase):
    """既存 readonly ツールの gh コマンド引数組み立ての回帰テスト"""

    def test_リポジトリ情報取得の引数リストを組み立てる(self):
        self.assertEqual(
            gh_proxy.build_gh_repo_view_args("octocat/hello-world"),
            ["repo", "view", "octocat/hello-world", "--json",
             "name,owner,description,url,stargazerCount,forkCount,createdAt,updatedAt"],
        )

    def test_PR一覧取得で任意引数なしの場合は基本引数のみを組み立てる(self):
        self.assertEqual(
            gh_proxy.build_gh_pr_list_args("octocat/hello-world", {}),
            ["pr", "list", "--repo", "octocat/hello-world", "--json",
             "number,title,state,author,createdAt,updatedAt"],
        )

    def test_PR一覧取得でstateとlimitとsearchを指定すると対応するオプションを付加する(self):
        arguments = {"state": "open", "limit": 10, "search": "created:>2024-01-01"}
        self.assertEqual(
            gh_proxy.build_gh_pr_list_args("octocat/hello-world", arguments),
            ["pr", "list", "--repo", "octocat/hello-world", "--json",
             "number,title,state,author,createdAt,updatedAt",
             "--state", "open", "--limit", "10", "--search", "created:>2024-01-01"],
        )

    def test_PR詳細取得の引数リストにPR番号を文字列として含める(self):
        self.assertEqual(
            gh_proxy.build_gh_pr_view_args("octocat/hello-world", 123),
            ["pr", "view", "123", "--repo", "octocat/hello-world", "--json",
             "number,title,body,state,author,createdAt,updatedAt,mergeable,mergedAt"],
        )

    def test_Issue一覧取得で任意引数なしの場合は基本引数のみを組み立てる(self):
        self.assertEqual(
            gh_proxy.build_gh_issue_list_args("octocat/hello-world", {}),
            ["issue", "list", "--repo", "octocat/hello-world", "--json",
             "number,title,state,author,createdAt,updatedAt"],
        )

    def test_Issue一覧取得でstateとlimitとsearchを指定すると対応するオプションを付加する(self):
        arguments = {"state": "closed", "limit": 5, "search": "updated:<2024-06-01"}
        self.assertEqual(
            gh_proxy.build_gh_issue_list_args("octocat/hello-world", arguments),
            ["issue", "list", "--repo", "octocat/hello-world", "--json",
             "number,title,state,author,createdAt,updatedAt",
             "--state", "closed", "--limit", "5", "--search", "updated:<2024-06-01"],
        )

    def test_Issue詳細取得の引数リストにIssue番号を文字列として含める(self):
        self.assertEqual(
            gh_proxy.build_gh_issue_view_args("octocat/hello-world", 456),
            ["issue", "view", "456", "--repo", "octocat/hello-world", "--json",
             "number,title,body,state,author,createdAt,updatedAt"],
        )

    def test_PRコメント取得の引数リストはcommentsフィールドのみを要求する(self):
        self.assertEqual(
            gh_proxy.build_gh_pr_comments_args("octocat/hello-world", 123),
            ["pr", "view", "123", "--repo", "octocat/hello-world", "--json", "comments"],
        )

    def test_Issueコメント取得の引数リストはcommentsフィールドのみを要求する(self):
        self.assertEqual(
            gh_proxy.build_gh_issue_comments_args("octocat/hello-world", 456),
            ["issue", "view", "456", "--repo", "octocat/hello-world", "--json", "comments"],
        )


class ValidateArgumentsTest(unittest.TestCase):
    """既存ツールの引数バリデーションの回帰テスト"""

    def test_必須フィールドが欠けているとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_arguments("gh_repo_view", {"owner": "octocat"})

    def test_未知のフィールドが含まれるとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_arguments(
                "gh_repo_view",
                {"owner": "octocat", "repository_name": "hello-world", "unknown": "x"},
            )

    def test_ownerが先頭ハイフンだとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_arguments(
                "gh_repo_view",
                {"owner": "-octocat", "repository_name": "hello-world"},
            )

    def test_limitが上限100を超えるとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_arguments(
                "gh_pr_list",
                {"owner": "octocat", "repository_name": "hello-world", "limit": 101},
            )

    def test_stateが列挙値以外だとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_arguments(
                "gh_pr_list",
                {"owner": "octocat", "repository_name": "hello-world", "state": "draft"},
            )

    def test_未知のツール名だとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_arguments("gh_unknown", {})

    def test_全フィールドが規約を満たす場合は例外にならない(self):
        gh_proxy.validate_arguments(
            "gh_pr_list",
            {"owner": "octocat", "repository_name": "hello-world",
             "state": "open", "limit": 10, "search": "created:>2024-01-01"},
        )


class ExecuteToolDispatchTest(unittest.TestCase):
    """execute_tool のディスパッチの回帰テスト"""

    def test_未知のツール名を実行するとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.execute_tool("gh_unknown", {"owner": "octocat", "repository_name": "hello-world"})

    def test_既存7ツールがディスパッチ表に登録されている(self):
        self.assertEqual(
            set(gh_proxy.REPO_TOOL_EXECUTORS.keys()),
            {"gh_repo_view", "gh_pr_list", "gh_pr_view", "gh_issue_list",
             "gh_issue_view", "gh_pr_comments", "gh_issue_comments"},
        )


class BranchNameValidationTest(unittest.TestCase):
    """validate_branch_name のテスト"""

    def test_英数字とスラッシュとドットとハイフンを含むブランチ名を受け入れる(self):
        gh_proxy.validate_branch_name("feature/foo-bar.v2", "branch")

    def test_先頭がハイフンのブランチ名はValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_branch_name("-force", "branch")

    def test_先頭がコロンのブランチ名はValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_branch_name(":main", "branch")

    def test_先頭がプラスのブランチ名はValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_branch_name("+main", "branch")

    def test_コロンを途中に含むブランチ名はValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_branch_name("feature:main", "branch")

    def test_空白を含むブランチ名はValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_branch_name("feature branch", "branch")

    def test_ドット2連続を含むブランチ名はValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_branch_name("feature..main", "branch")

    def test_空文字のブランチ名はValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_branch_name("", "branch")


class RepositoryPathValidationTest(unittest.TestCase):
    """validate_repository_path のテスト"""

    def test_相対パスはValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_repository_path("relative/path")

    def test_存在しないディレクトリはValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_repository_path("/nonexistent-gh-proxy-test-dir")

    def test_dotgitがないディレクトリはValidationErrorになる(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(gh_proxy.ValidationError):
                gh_proxy.validate_repository_path(tmpdir)

    def test_dotgitディレクトリを持つディレクトリを受け入れる(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.mkdir(os.path.join(tmpdir, ".git"))
            gh_proxy.validate_repository_path(tmpdir)

    def test_dotgitファイルを持つディレクトリを受け入れる(self):
        # git worktree ではリポジトリルートの .git はファイルになる
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, ".git"), "w") as f:
                f.write("gitdir: /somewhere/.git/worktrees/x\n")
            gh_proxy.validate_repository_path(tmpdir)


class BuildGitPushArgsTest(unittest.TestCase):
    """git_push の引数組み立てのテスト"""

    def test_パスとブランチからリモートorigin固定の引数リストを組み立てる(self):
        self.assertEqual(
            gh_proxy.build_git_push_args("/home/user/repo", "feature/x"),
            ["-C", "/home/user/repo", "push", "origin", "feature/x"],
        )


class GitPushSchemaValidationTest(unittest.TestCase):
    """git_push のスキーマレベルの引数検証のテスト"""

    def test_pathとbranchが揃っていれば例外にならない(self):
        gh_proxy.validate_arguments("git_push", {"path": "/home/user/repo", "branch": "main"})

    def test_branchが先頭ハイフンだとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_arguments("git_push", {"path": "/home/user/repo", "branch": "--force"})

    def test_pathが欠けているとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_arguments("git_push", {"branch": "main"})

    def test_ownerフィールドを渡すとValidationErrorになる(self):
        with self.assertRaises(gh_proxy.ValidationError):
            gh_proxy.validate_arguments(
                "git_push",
                {"path": "/home/user/repo", "branch": "main", "owner": "octocat"},
            )


if __name__ == "__main__":
    unittest.main()
