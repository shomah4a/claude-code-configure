#!/usr/bin/env python3
"""aws-credential.py のユニットテスト

ファイル名にハイフンを含むため、importlib で直接ロードする。
"""

import importlib.util
import io
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

    def test_YAML構文エラーの設定ファイルはConfigErrorになる(self):
        messages: List[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "aws-credential.yml"
            # インデントが不正な YAML (マッピングとリストの混在) を書く
            config_path.write_text(
                "credentials:\n  dev:\n  profile: my-dev-profile\n - broken\n",
                encoding="utf-8",
            )
            with self.assertRaises(aws_credential.ConfigError):
                aws_credential.load_config(config_path, messages.append)

    def test_設定ファイルのパスがディレクトリだとConfigErrorになる(self):
        messages: List[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "aws-credential.yml"
            config_path.mkdir()
            with self.assertRaises(aws_credential.ConfigError):
                aws_credential.load_config(config_path, messages.append)


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


class BuildToolsTest(unittest.TestCase):
    """build_tools のテスト"""

    def test_enumに記載順でnameが入る(self):
        entries = [
            aws_credential.CredentialEntry(name="dev", profile="dev-profile"),
            aws_credential.CredentialEntry(name="staging", profile="staging-profile"),
        ]
        tools = aws_credential.build_tools(entries)
        self.assertEqual(
            tools[0]["inputSchema"]["properties"]["name"]["enum"],
            ["dev", "staging"],
        )

    def test_requiredにnameが入る(self):
        tools = aws_credential.build_tools(
            [aws_credential.CredentialEntry(name="dev", profile="dev-profile")]
        )
        self.assertEqual(tools[0]["inputSchema"]["required"], ["name"])


class ValidateArgumentsTest(unittest.TestCase):
    """validate_arguments のテスト"""

    def _entries(self) -> List:
        return [
            aws_credential.CredentialEntry(name="dev", profile="dev-profile"),
            aws_credential.CredentialEntry(name="staging", profile="staging-profile"),
        ]

    def test_一致するnameのCredentialEntryを返す(self):
        entry = aws_credential.validate_arguments({"name": "staging"}, self._entries())
        self.assertEqual(
            entry,
            aws_credential.CredentialEntry(name="staging", profile="staging-profile"),
        )

    def test_nameが欠落しているとValidationErrorになる(self):
        with self.assertRaises(aws_credential.ValidationError):
            aws_credential.validate_arguments({}, self._entries())

    def test_未知のフィールドを含むとValidationErrorになる(self):
        with self.assertRaises(aws_credential.ValidationError):
            aws_credential.validate_arguments(
                {"name": "dev", "region": "ap-northeast-1"}, self._entries()
            )

    def test_未登録のnameを指定するとValidationErrorになる(self):
        with self.assertRaises(aws_credential.ValidationError):
            aws_credential.validate_arguments({"name": "unknown"}, self._entries())

    def test_argumentsがdictでないとValidationErrorになる(self):
        with self.assertRaises(aws_credential.ValidationError):
            aws_credential.validate_arguments(["dev"], self._entries())


class HandleToolsCallTest(unittest.TestCase):
    """handle_tools_call のテスト"""

    def _entries(self) -> List:
        return [aws_credential.CredentialEntry(name="dev", profile="dev-profile")]

    def _make_run_command(self, export_stdout: str, export_code: int = 0):
        """コマンド引数を見て export-credentials と get region の戻り値を切り替える偽の RunCommand"""
        def run_command(command: List[str]) -> Tuple[str, str, int]:
            if "export-credentials" in command:
                return (export_stdout, "", export_code)
            return ("ap-northeast-1", "", 0)
        return run_command

    def _valid_export_stdout(self) -> str:
        return json.dumps({
            "Version": 1,
            "AccessKeyId": DUMMY_ACCESS_KEY_ID,
            "SecretAccessKey": DUMMY_SECRET_ACCESS_KEY,
        })

    def test_成功時はJSON形式のtextにAWS_ACCESS_KEY_IDを含む(self):
        run_command = self._make_run_command(self._valid_export_stdout())
        messages: List[str] = []
        result = aws_credential.handle_tools_call(
            {"name": aws_credential.TOOL_NAME, "arguments": {"name": "dev"}},
            self._entries(), run_command, messages.append,
        )
        text = result["content"][0]["text"]
        self.assertIn("AWS_ACCESS_KEY_ID", text)
        self.assertEqual(json.loads(text)["AWS_ACCESS_KEY_ID"], DUMMY_ACCESS_KEY_ID)

    def test_認証情報取得に失敗するとisErrorがTrueでダミー秘密を含まない(self):
        broken_stdout = f"not-json {DUMMY_ACCESS_KEY_ID} {DUMMY_SECRET_ACCESS_KEY}"
        run_command = self._make_run_command(broken_stdout)
        messages: List[str] = []
        result = aws_credential.handle_tools_call(
            {"name": aws_credential.TOOL_NAME, "arguments": {"name": "dev"}},
            self._entries(), run_command, messages.append,
        )
        self.assertTrue(result["isError"])
        self.assertNotIn(DUMMY_ACCESS_KEY_ID, result["content"][0]["text"])
        self.assertNotIn(DUMMY_SECRET_ACCESS_KEY, result["content"][0]["text"])

    def test_未知のツール名を指定するとValidationErrorになる(self):
        def unused_run_command(command: List[str]) -> Tuple[str, str, int]:
            raise AssertionError("run_command は呼ばれないはずです")

        messages: List[str] = []
        with self.assertRaises(aws_credential.ValidationError):
            aws_credential.handle_tools_call(
                {"name": "unknown_tool", "arguments": {"name": "dev"}},
                self._entries(), unused_run_command, messages.append,
            )


class HandleJsonrpcRequestTest(unittest.TestCase):
    """handle_jsonrpc_request のテスト"""

    def _entries(self) -> List:
        return [aws_credential.CredentialEntry(name="dev", profile="dev-profile")]

    def _no_op_run_command(self, command: List[str]) -> Tuple[str, str, int]:
        return ("", "", 0)

    def test_initializeはserverInfoのnameを返す(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_credential.handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            entries, aws_credential.build_tools(entries), self._no_op_run_command, messages.append,
        )
        self.assertEqual(response["result"]["serverInfo"]["name"], aws_credential.SERVER_NAME)

    def test_tools_listはツール1件を返す(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_credential.handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            entries, aws_credential.build_tools(entries), self._no_op_run_command, messages.append,
        )
        self.assertEqual(len(response["result"]["tools"]), 1)

    def test_未知のメソッドはMETHOD_NOT_FOUNDになる(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_credential.handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "unknown/method", "params": {}},
            entries, aws_credential.build_tools(entries), self._no_op_run_command, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_credential.METHOD_NOT_FOUND)

    def test_jsonrpcが2_0でないとINVALID_REQUESTになる(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_credential.handle_jsonrpc_request(
            {"jsonrpc": "1.0", "id": 1, "method": "initialize", "params": {}},
            entries, aws_credential.build_tools(entries), self._no_op_run_command, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_credential.INVALID_REQUEST)

    def test_ValidationErrorはINVALID_PARAMSになる(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_credential.handle_jsonrpc_request(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": aws_credential.TOOL_NAME, "arguments": {"name": "unknown"}},
            },
            entries, aws_credential.build_tools(entries), self._no_op_run_command, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_credential.INVALID_PARAMS)

    def test_予期しない例外はINTERNAL_ERRORでダミー秘密がmessageに含まれずreportには含まれる(self):
        entries = self._entries()

        def failing_run_command(command: List[str]) -> Tuple[str, str, int]:
            raise RuntimeError(f"{DUMMY_ACCESS_KEY_ID} を含む")

        messages: List[str] = []
        response = aws_credential.handle_jsonrpc_request(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": aws_credential.TOOL_NAME, "arguments": {"name": "dev"}},
            },
            entries, aws_credential.build_tools(entries), failing_run_command, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_credential.INTERNAL_ERROR)
        self.assertNotIn(DUMMY_ACCESS_KEY_ID, response["error"]["message"])
        self.assertTrue(any(DUMMY_ACCESS_KEY_ID in message for message in messages))


class ExtractHostNameTest(unittest.TestCase):
    """extract_host_name のテスト"""

    def test_ポートありのホスト名からポートを除く(self):
        self.assertEqual(aws_credential.extract_host_name("localhost:30722"), "localhost")

    def test_ポートなしのホスト名はそのまま返す(self):
        self.assertEqual(aws_credential.extract_host_name("localhost"), "localhost")

    def test_IPv6アドレスはポートだけを除いて角括弧付きで返す(self):
        self.assertEqual(aws_credential.extract_host_name("[::1]:30722"), "[::1]")

    def test_ポートなしのIPv6アドレスはそのまま返す(self):
        self.assertEqual(aws_credential.extract_host_name("[::1]"), "[::1]")

    def test_前後の空白を除去する(self):
        self.assertEqual(aws_credential.extract_host_name("  localhost:30722  "), "localhost")


class ExtractOriginHostTest(unittest.TestCase):
    """extract_origin_host のテスト"""

    def test_スキームとポートを含むOriginからホスト名を取り出す(self):
        self.assertEqual(aws_credential.extract_origin_host("http://localhost:30722"), "localhost")

    def test_ポートなしのOriginからホスト名を取り出す(self):
        self.assertEqual(aws_credential.extract_origin_host("https://localhost"), "localhost")

    def test_IPv6のOriginからホスト名を取り出す(self):
        self.assertEqual(aws_credential.extract_origin_host("http://[::1]:30722"), "[::1]")

    def test_netlocが空のOriginは空文字列を返す(self):
        self.assertEqual(aws_credential.extract_origin_host("not-a-url"), "")

    def test_nullという文字列は空文字列を返す(self):
        self.assertEqual(aws_credential.extract_origin_host("null"), "")


class WsgiApplicationTest(unittest.TestCase):
    """create_application が返す WSGI アプリケーションのテスト"""

    def _entries(self) -> List:
        return [aws_credential.CredentialEntry(name="dev", profile="dev-profile")]

    def _run_command(self, command: List[str]) -> Tuple[str, str, int]:
        if "export-credentials" in command:
            return (json.dumps({
                "Version": 1,
                "AccessKeyId": DUMMY_ACCESS_KEY_ID,
                "SecretAccessKey": DUMMY_SECRET_ACCESS_KEY,
            }), "", 0)
        return ("ap-northeast-1", "", 0)

    def _create_app(self, messages: List[str], allowed_hosts=None):
        return aws_credential.create_application(
            self._entries(), self._run_command, messages.append,
            allowed_hosts if allowed_hosts is not None else aws_credential.DEFAULT_ALLOWED_HOSTS,
        )

    def _call_app(
        self, app, body: Optional[bytes], method: str = "POST",
        content_type: str = "application/json",
        host: Optional[str] = "localhost:30722", origin: Optional[str] = None,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """WSGI アプリを直接呼び出し、(status, レスポンスJSON) を返すテスト用ヘルパー

        host / origin は既定で正規のループバック向けの値を入れ、引数で上書きできるようにする。
        """
        captured_status: List[str] = []

        def start_response(status: str, headers: List[Tuple[str, str]]) -> None:
            captured_status.append(status)

        body_bytes = body if body is not None else b""
        environ: Dict[str, Any] = {
            "REQUEST_METHOD": method,
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(body_bytes)),
            "wsgi.input": io.BytesIO(body_bytes),
        }
        if host is not None:
            environ["HTTP_HOST"] = host
        if origin is not None:
            environ["HTTP_ORIGIN"] = origin

        result = app(environ, start_response)
        response_body = b"".join(result)
        try:
            parsed = json.loads(response_body) if response_body else None
        except json.JSONDecodeError:
            parsed = None
        return captured_status[0], parsed

    def test_GETは405になる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, None, method="GET")
        self.assertTrue(status.startswith("405"))

    def test_Content_Typeがtext_plainだと415になる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, b"{}", content_type="text/plain")
        self.assertTrue(status.startswith("415"))

    def test_不正なJSON本文は200でPARSE_ERRORのJSON_RPCエラーになる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, parsed = self._call_app(app, b"not-json")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(parsed["error"]["code"], aws_credential.PARSE_ERROR)
        self.assertIsNone(parsed["id"])

    def test_JSON配列の本文はINVALID_REQUESTになる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, parsed = self._call_app(app, b"[1, 2, 3]")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(parsed["error"]["code"], aws_credential.INVALID_REQUEST)

    def test_正常なtools_callは200でcontentを返す(self):
        messages: List[str] = []
        app = self._create_app(messages)
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": aws_credential.TOOL_NAME, "arguments": {"name": "dev"}},
        }).encode("utf-8")
        status, parsed = self._call_app(app, body)
        self.assertTrue(status.startswith("200"))
        self.assertIn("content", parsed["result"])

    def test_Hostがlocalhostなら通る(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, b"{}", host="localhost:30722")
        self.assertTrue(status.startswith("200"))

    def test_Hostが127_0_0_1なら通る(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, b"{}", host="127.0.0.1:30722")
        self.assertTrue(status.startswith("200"))

    def test_HostがIPv6ループバックなら通る(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, b"{}", host="[::1]:30722")
        self.assertTrue(status.startswith("200"))

    def test_Hostが許可されていないホストだと403になる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, b"{}", host="attacker.example:30722")
        self.assertTrue(status.startswith("403"))

    def test_Hostヘッダが無いと403になる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, b"{}", host=None)
        self.assertTrue(status.startswith("403"))

    def test_Originが許可されていないホストだとHostがlocalhostでも403になる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(
            app, b"{}", host="localhost:30722", origin="http://attacker.example:30722",
        )
        self.assertTrue(status.startswith("403"))

    def test_Originがlocalhostなら通る(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(
            app, b"{}", host="localhost:30722", origin="http://localhost:30722",
        )
        self.assertTrue(status.startswith("200"))

    def test_Originがnullだと403になる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, b"{}", host="localhost:30722", origin="null")
        self.assertTrue(status.startswith("403"))

    def test_環境変数で追加したホストは通る(self):
        messages: List[str] = []
        allowed_hosts = aws_credential.resolve_allowed_hosts(
            {aws_credential.ALLOWED_HOSTS_ENV: "host.docker.internal"}
        )
        app = self._create_app(messages, allowed_hosts=allowed_hosts)
        status, _parsed = self._call_app(app, b"{}", host="host.docker.internal:30722")
        self.assertTrue(status.startswith("200"))

    def test_403のときreportにメッセージが渡る(self):
        messages: List[str] = []
        app = self._create_app(messages)
        self._call_app(app, b"{}", host="attacker.example:30722")
        self.assertEqual(len(messages), 1)
        self.assertIn("attacker.example", messages[0])


class ResolveBindTest(unittest.TestCase):
    """resolve_bind のテスト"""

    def test_環境変数が無ければ既定値を使う(self):
        self.assertEqual(aws_credential.resolve_bind({}), aws_credential.DEFAULT_BIND)

    def test_環境変数が設定されていればその値を使う(self):
        bind = aws_credential.resolve_bind({aws_credential.BIND_ENV: "0.0.0.0"})
        self.assertEqual(bind, "0.0.0.0")


class ResolvePortTest(unittest.TestCase):
    """resolve_port のテスト"""

    def test_環境変数が無ければ既定値を使う(self):
        self.assertEqual(aws_credential.resolve_port({}), aws_credential.DEFAULT_PORT)

    def test_環境変数が数値文字列であればその値を使う(self):
        self.assertEqual(aws_credential.resolve_port({aws_credential.PORT_ENV: "12345"}), 12345)

    def test_環境変数が数値でなければConfigErrorになる(self):
        with self.assertRaises(aws_credential.ConfigError):
            aws_credential.resolve_port({aws_credential.PORT_ENV: "not-a-number"})


class ResolveAllowedHostsTest(unittest.TestCase):
    """resolve_allowed_hosts のテスト"""

    def test_環境変数が無ければ既定のホスト集合を使う(self):
        self.assertEqual(
            aws_credential.resolve_allowed_hosts({}),
            aws_credential.DEFAULT_ALLOWED_HOSTS,
        )

    def test_環境変数のホストを既定のホスト集合に追加する(self):
        allowed_hosts = aws_credential.resolve_allowed_hosts(
            {aws_credential.ALLOWED_HOSTS_ENV: "host.docker.internal, example.internal"}
        )
        self.assertIn("host.docker.internal", allowed_hosts)
        self.assertIn("example.internal", allowed_hosts)
        self.assertIn("localhost", allowed_hosts)


if __name__ == "__main__":
    unittest.main()
