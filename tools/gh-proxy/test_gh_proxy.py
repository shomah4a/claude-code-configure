#!/usr/bin/env python3
"""gh-proxy.py のユニットテスト

ファイル名にハイフンを含むため、importlib で直接ロードする。
"""

import importlib.util
import os
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


if __name__ == "__main__":
    unittest.main()
