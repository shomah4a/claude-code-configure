#!/usr/bin/env python3
"""aws-credential.py のユニットテスト

ファイル名にハイフンを含むため、importlib で直接ロードする。
"""

import importlib.util
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import List

_MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aws-credential.py")
_SPEC = importlib.util.spec_from_file_location("aws_credential", _MODULE_PATH)
aws_credential = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(aws_credential)


class ParseEntriesTest(unittest.TestCase):
    """parse_entries のテスト"""

    def _report(self, messages: List[str]):
        return messages.append

    def test_profileを持つ1エントリを読み込める(self):
        messages: List[str] = []
        entries = aws_credential.parse_entries(
            {"credentials": {"dev": {"profile": "my-dev-profile"}}},
            self._report(messages),
        )
        self.assertEqual(entries, [aws_credential.CredentialEntry(name="dev", profile="my-dev-profile")])
        self.assertEqual(messages, [])

    def test_複数エントリを記載順に読み込める(self):
        messages: List[str] = []
        entries = aws_credential.parse_entries(
            {
                "credentials": {
                    "dev": {"profile": "dev-profile"},
                    "staging": {"profile": "staging-profile"},
                    "prod": {"profile": "prod-profile"},
                }
            },
            self._report(messages),
        )
        self.assertEqual(
            [entry.name for entry in entries],
            ["dev", "staging", "prod"],
        )

    def test_profileが無いエントリはスキップされreportにメッセージが渡る(self):
        messages: List[str] = []
        entries = aws_credential.parse_entries(
            {
                "credentials": {
                    "dev": {"profile": "dev-profile"},
                    "broken": {},
                }
            },
            self._report(messages),
        )
        self.assertEqual([entry.name for entry in entries], ["dev"])
        self.assertEqual(len(messages), 1)
        self.assertIn("broken", messages[0])

    def test_nameが先頭ハイフンのエントリはスキップされる(self):
        messages: List[str] = []
        entries = aws_credential.parse_entries(
            {
                "credentials": {
                    "dev": {"profile": "dev-profile"},
                    "-evil": {"profile": "evil-profile"},
                }
            },
            self._report(messages),
        )
        self.assertEqual([entry.name for entry in entries], ["dev"])

    def test_profileが先頭ハイフンのエントリはスキップされる(self):
        messages: List[str] = []
        entries = aws_credential.parse_entries(
            {
                "credentials": {
                    "dev": {"profile": "dev-profile"},
                    "broken": {"profile": "-evil-profile"},
                }
            },
            self._report(messages),
        )
        self.assertEqual([entry.name for entry in entries], ["dev"])

    def test_profile以外の未知キーを持つエントリはスキップされる(self):
        messages: List[str] = []
        entries = aws_credential.parse_entries(
            {
                "credentials": {
                    "dev": {"profile": "dev-profile"},
                    "broken": {"profile": "broken-profile", "region": "ap-northeast-1"},
                }
            },
            self._report(messages),
        )
        self.assertEqual([entry.name for entry in entries], ["dev"])

    def test_credentialsキーが無いとConfigErrorになる(self):
        messages: List[str] = []
        with self.assertRaises(aws_credential.ConfigError):
            aws_credential.parse_entries({}, self._report(messages))

    def test_credentialsがdictでないとConfigErrorになる(self):
        messages: List[str] = []
        with self.assertRaises(aws_credential.ConfigError):
            aws_credential.parse_entries({"credentials": ["dev"]}, self._report(messages))

    def test_有効エントリが0件だとConfigErrorになる(self):
        messages: List[str] = []
        with self.assertRaises(aws_credential.ConfigError):
            aws_credential.parse_entries(
                {"credentials": {"broken": {}}},
                self._report(messages),
            )


class LoadConfigTest(unittest.TestCase):
    """load_config のテスト"""

    def _write_yaml(self, tmp_path: Path, content: str) -> Path:
        config_path = tmp_path / "aws-credential.yml"
        config_path.write_text(textwrap.dedent(content), encoding="utf-8")
        return config_path

    def test_設定ファイルが存在しないとConfigErrorになる(self):
        messages: List[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "nonexistent.yml"
            with self.assertRaises(aws_credential.ConfigError):
                aws_credential.load_config(config_path, messages.append)

    def test_ファイルからprofileを持つエントリを読み込める(self):
        messages: List[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_yaml(Path(tmp), """\
                credentials:
                  dev:
                    profile: my-dev-profile
            """)
            entries = aws_credential.load_config(config_path, messages.append)
            self.assertEqual(
                entries,
                [aws_credential.CredentialEntry(name="dev", profile="my-dev-profile")],
            )


class ResolveConfigPathTest(unittest.TestCase):
    """resolve_config_path のテスト"""

    def test_環境変数が設定されていればそのパスが使われる(self):
        path = aws_credential.resolve_config_path(
            {aws_credential.CONFIG_PATH_ENV: "/custom/aws-credential.yml"}
        )
        self.assertEqual(path, Path("/custom/aws-credential.yml"))

    def test_環境変数が無ければ既定パスが使われる(self):
        path = aws_credential.resolve_config_path({})
        self.assertEqual(path, aws_credential.DEFAULT_CONFIG_PATH)


if __name__ == "__main__":
    unittest.main()
