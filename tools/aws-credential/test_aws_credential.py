#!/usr/bin/env python3
"""aws-credential.py のユニットテスト

ファイル名にハイフンを含むため、importlib で直接ロードする。
"""

import importlib.util
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import List, Tuple

# 実際の認証情報ではないことが明らかなダミー値 (AWS公式ドキュメントの例示用文字列)
DUMMY_ACCESS_KEY_ID = "AKIAEXAMPLESECRET"
DUMMY_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCyEXAMPLEKEY"

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


class BuildAwsCommandArgsTest(unittest.TestCase):
    """aws コマンド引数組み立てのテスト"""

    def test_export_credentials取得の引数リストを組み立てる(self):
        self.assertEqual(
            aws_credential.build_export_credentials_args("my-dev-profile"),
            ["aws", "configure", "export-credentials", "--profile", "my-dev-profile",
             "--format", "process"],
        )

    def test_region取得の引数リストを組み立てる(self):
        self.assertEqual(
            aws_credential.build_get_region_args("my-dev-profile"),
            ["aws", "configure", "get", "region", "--profile", "my-dev-profile"],
        )


class ParseExportCredentialsOutputTest(unittest.TestCase):
    """parse_export_credentials_output のテスト"""

    def _valid_payload(self, **overrides):
        payload = {
            "Version": 1,
            "AccessKeyId": DUMMY_ACCESS_KEY_ID,
            "SecretAccessKey": DUMMY_SECRET_ACCESS_KEY,
            "SessionToken": "dummy-session-token",
            "Expiration": "2026-01-01T00:00:00Z",
        }
        payload.update(overrides)
        return payload

    def test_全フィールドありの出力を解析できる(self):
        credentials = aws_credential.parse_export_credentials_output(
            json.dumps(self._valid_payload())
        )
        self.assertEqual(
            credentials,
            aws_credential.AwsCredentials(
                access_key_id=DUMMY_ACCESS_KEY_ID,
                secret_access_key=DUMMY_SECRET_ACCESS_KEY,
                session_token="dummy-session-token",
                expiration="2026-01-01T00:00:00Z",
            ),
        )

    def test_SessionTokenとExpirationが無い出力を解析できる(self):
        payload = self._valid_payload()
        del payload["SessionToken"]
        del payload["Expiration"]
        credentials = aws_credential.parse_export_credentials_output(json.dumps(payload))
        self.assertIsNone(credentials.session_token)
        self.assertIsNone(credentials.expiration)

    def test_未知フィールドは無視される(self):
        payload = self._valid_payload(Unknown="ignored-value")
        credentials = aws_credential.parse_export_credentials_output(json.dumps(payload))
        self.assertEqual(credentials.access_key_id, DUMMY_ACCESS_KEY_ID)

    def test_不正なJSONのときエラーメッセージにダミー秘密が含まれない(self):
        broken_json = f"not-json {DUMMY_ACCESS_KEY_ID} {DUMMY_SECRET_ACCESS_KEY}"
        with self.assertRaises(aws_credential.CredentialFetchError) as ctx:
            aws_credential.parse_export_credentials_output(broken_json)
        self.assertNotIn(DUMMY_ACCESS_KEY_ID, str(ctx.exception))
        self.assertNotIn(DUMMY_SECRET_ACCESS_KEY, str(ctx.exception))

    def test_JSON配列を渡すとCredentialFetchErrorになる(self):
        with self.assertRaises(aws_credential.CredentialFetchError):
            aws_credential.parse_export_credentials_output(json.dumps([1, 2, 3]))

    def test_Versionが2だとCredentialFetchErrorになる(self):
        with self.assertRaises(aws_credential.CredentialFetchError):
            aws_credential.parse_export_credentials_output(
                json.dumps(self._valid_payload(Version=2))
            )

    def test_AccessKeyId欠落はCredentialFetchErrorでメッセージにフィールド名が含まれる(self):
        payload = self._valid_payload()
        del payload["AccessKeyId"]
        with self.assertRaises(aws_credential.CredentialFetchError) as ctx:
            aws_credential.parse_export_credentials_output(json.dumps(payload))
        self.assertIn("AccessKeyId", str(ctx.exception))


class BuildEnvMappingTest(unittest.TestCase):
    """build_env_mapping のテスト"""

    def _credentials(self, **overrides):
        base = {
            "access_key_id": DUMMY_ACCESS_KEY_ID,
            "secret_access_key": DUMMY_SECRET_ACCESS_KEY,
            "session_token": None,
            "expiration": None,
        }
        base.update(overrides)
        return aws_credential.AwsCredentials(**base)

    def test_session_tokenとexpirationとregionがある場合の挿入順(self):
        env = aws_credential.build_env_mapping(
            self._credentials(session_token="dummy-session-token", expiration="2026-01-01T00:00:00Z"),
            region="ap-northeast-1",
        )
        self.assertEqual(
            list(env.keys()),
            ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
             "AWS_CREDENTIAL_EXPIRATION", "AWS_REGION", "AWS_DEFAULT_REGION"],
        )
        self.assertEqual(env["AWS_REGION"], "ap-northeast-1")
        self.assertEqual(env["AWS_DEFAULT_REGION"], "ap-northeast-1")

    def test_session_tokenとexpirationとregionが無い場合はキーが含まれない(self):
        env = aws_credential.build_env_mapping(self._credentials(), region=None)
        self.assertEqual(
            list(env.keys()),
            ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        )


class FetchRegionTest(unittest.TestCase):
    """fetch_region のテスト"""

    def _run_command_returning(self, result: Tuple[str, str, int]):
        def run_command(command: List[str]) -> Tuple[str, str, int]:
            return result
        return run_command

    def test_成功時はstripしたregionを返す(self):
        run_command = self._run_command_returning(("  ap-northeast-1  \n", "", 0))
        messages: List[str] = []
        region = aws_credential.fetch_region("dev", run_command, messages.append)
        self.assertEqual(region, "ap-northeast-1")
        self.assertEqual(messages, [])

    def test_終了コードが非0の場合はNoneを返しreportに終了コードが渡る(self):
        run_command = self._run_command_returning(("", "profile not found", 1))
        messages: List[str] = []
        region = aws_credential.fetch_region("dev", run_command, messages.append)
        self.assertIsNone(region)
        self.assertEqual(len(messages), 1)
        self.assertIn("1", messages[0])

    def test_出力が空文字の場合はNoneを返す(self):
        run_command = self._run_command_returning(("   \n", "", 0))
        messages: List[str] = []
        region = aws_credential.fetch_region("dev", run_command, messages.append)
        self.assertIsNone(region)


class FetchCredentialsTest(unittest.TestCase):
    """fetch_credentials のテスト"""

    def _make_run_command(self, export_result: Tuple[str, str, int],
                           region_result: Tuple[str, str, int] = ("ap-northeast-1", "", 0)):
        """コマンド引数を見て export-credentials と get region の戻り値を切り替える偽の RunCommand"""
        def run_command(command: List[str]) -> Tuple[str, str, int]:
            if "export-credentials" in command:
                return export_result
            if "get" in command:
                return region_result
            raise AssertionError(f"想定外のコマンドが呼ばれました: {command}")
        return run_command

    def _valid_export_stdout(self) -> str:
        return json.dumps({
            "Version": 1,
            "AccessKeyId": DUMMY_ACCESS_KEY_ID,
            "SecretAccessKey": DUMMY_SECRET_ACCESS_KEY,
        })

    def test_成功時はregion込みの環境変数マッピングを返す(self):
        run_command = self._make_run_command(
            export_result=(self._valid_export_stdout(), "", 0),
            region_result=("ap-northeast-1", "", 0),
        )
        messages: List[str] = []
        env = aws_credential.fetch_credentials("dev", run_command, messages.append)
        self.assertEqual(env["AWS_ACCESS_KEY_ID"], DUMMY_ACCESS_KEY_ID)
        self.assertEqual(env["AWS_REGION"], "ap-northeast-1")

    def test_export失敗時のメッセージには終了コードのみ含まれstderrはreportにのみ渡る(self):
        run_command = self._make_run_command(export_result=("", "credential detail leak", 2))
        messages: List[str] = []
        with self.assertRaises(aws_credential.CredentialFetchError) as ctx:
            aws_credential.fetch_credentials("dev", run_command, messages.append)
        self.assertIn("2", str(ctx.exception))
        self.assertNotIn("credential detail leak", str(ctx.exception))
        self.assertTrue(any("credential detail leak" in message for message in messages))

    def test_region取得に失敗しても認証情報は返る(self):
        run_command = self._make_run_command(
            export_result=(self._valid_export_stdout(), "", 0),
            region_result=("", "region error", 1),
        )
        messages: List[str] = []
        env = aws_credential.fetch_credentials("dev", run_command, messages.append)
        self.assertEqual(env["AWS_ACCESS_KEY_ID"], DUMMY_ACCESS_KEY_ID)
        self.assertNotIn("AWS_REGION", env)

    def test_Invalid_choiceを含む失敗はAWS_CLI_v2案内のメッセージになる(self):
        run_command = self._make_run_command(
            export_result=("", "Invalid choice: 'export-credentials'", 2)
        )
        messages: List[str] = []
        with self.assertRaises(aws_credential.CredentialFetchError) as ctx:
            aws_credential.fetch_credentials("dev", run_command, messages.append)
        self.assertIn("AWS CLI v2", str(ctx.exception))

    def test_不正なJSON応答のときエラーメッセージにダミー秘密が含まれない(self):
        broken_stdout = f"not-json {DUMMY_ACCESS_KEY_ID} {DUMMY_SECRET_ACCESS_KEY}"
        run_command = self._make_run_command(export_result=(broken_stdout, "", 0))
        messages: List[str] = []
        with self.assertRaises(aws_credential.CredentialFetchError) as ctx:
            aws_credential.fetch_credentials("dev", run_command, messages.append)
        self.assertNotIn(DUMMY_ACCESS_KEY_ID, str(ctx.exception))
        self.assertNotIn(DUMMY_SECRET_ACCESS_KEY, str(ctx.exception))


class BuildSubprocessEnvTest(unittest.TestCase):
    """build_subprocess_env のテスト"""

    def test_許可された変数のみ残りAWS変数は落ちる(self):
        env = aws_credential.build_subprocess_env({
            "HOME": "/home/test",
            "PATH": "/usr/bin:/bin",
            "AWS_ACCESS_KEY_ID": DUMMY_ACCESS_KEY_ID,
            "AWS_PROFILE": "dev",
        })
        self.assertEqual(env, {"HOME": "/home/test", "PATH": "/usr/bin:/bin"})

    def test_存在しないキーは含まれない(self):
        env = aws_credential.build_subprocess_env({"HOME": "/home/test"})
        self.assertEqual(env, {"HOME": "/home/test"})


class ResolveTimeoutSecTest(unittest.TestCase):
    """resolve_timeout_sec のテスト"""

    def test_環境変数が無ければ既定値を使う(self):
        self.assertEqual(aws_credential.resolve_timeout_sec({}), aws_credential.DEFAULT_TIMEOUT_SEC)

    def test_環境変数が数値文字列であればその値を使う(self):
        timeout_sec = aws_credential.resolve_timeout_sec({aws_credential.TIMEOUT_ENV: "45"})
        self.assertEqual(timeout_sec, 45)

    def test_環境変数が数値でなければConfigErrorになる(self):
        with self.assertRaises(aws_credential.ConfigError):
            aws_credential.resolve_timeout_sec({aws_credential.TIMEOUT_ENV: "not-a-number"})


class CreateRunCommandTest(unittest.TestCase):
    """create_run_command のテスト (実プロセスを起動して検証する)"""

    def _base_env(self):
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    def test_標準出力と標準エラー出力と終了コードを取得できる(self):
        run_command = aws_credential.create_run_command(timeout_sec=5, env=self._base_env())
        stdout, stderr, code = run_command(["sh", "-c", "echo out; echo err 1>&2; exit 3"])
        self.assertEqual(stdout, "out\n")
        self.assertEqual(stderr, "err\n")
        self.assertEqual(code, 3)

    def test_存在しないコマンドはCredentialFetchErrorになる(self):
        run_command = aws_credential.create_run_command(timeout_sec=5, env=self._base_env())
        with self.assertRaises(aws_credential.CredentialFetchError):
            run_command(["aws-credential-test-nonexistent-command-xyz"])

    def test_タイムアウトするとCredentialFetchErrorになる(self):
        run_command = aws_credential.create_run_command(timeout_sec=1, env=self._base_env())
        with self.assertRaises(aws_credential.CredentialFetchError):
            run_command(["sh", "-c", "sleep 5"])

    def test_envに渡した変数だけが子プロセスに見える(self):
        source_environ = {
            "HOME": "test-home-value",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "AWS_ACCESS_KEY_ID": DUMMY_ACCESS_KEY_ID,
        }
        env = aws_credential.build_subprocess_env(source_environ)
        run_command = aws_credential.create_run_command(timeout_sec=5, env=env)
        stdout, _stderr, code = run_command(["sh", "-c", "echo $HOME-$AWS_ACCESS_KEY_ID"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "test-home-value-\n")


if __name__ == "__main__":
    unittest.main()
