#!/usr/bin/env python3
"""aws-cli-proxy.py のユニットテスト

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
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 実際の認証情報ではないことが明らかなダミー値 (AWS公式ドキュメントの例示用文字列)
DUMMY_ACCESS_KEY_ID = "AKIAEXAMPLESECRET"
DUMMY_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCyEXAMPLEKEY"

_MODULE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aws-cli-proxy.py")
_SPEC = importlib.util.spec_from_file_location("aws_cli_proxy", _MODULE_PATH)
aws_cli_proxy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(aws_cli_proxy)


class ParseProfileEntryTest(unittest.TestCase):
    """parse_profile_entry のテスト"""

    def test_confがdictでないとValueErrorになる(self):
        with self.assertRaises(ValueError):
            aws_cli_proxy.parse_profile_entry("dev", "not-a-dict")

    def test_profileの末尾に改行を含むとValueErrorになる(self):
        # IDENTIFIER_PATTERN は \Z を使っており、$ と違って末尾改行の直前にはマッチしないため拒否される
        with self.assertRaises(ValueError):
            aws_cli_proxy.parse_profile_entry("dev", {"profile": "dev\n"})


class ParseEntriesTest(unittest.TestCase):
    """parse_entries のテスト"""

    def _report(self, messages: List[str]):
        return messages.append

    def test_profileを持つ1エントリを読み込める(self):
        messages: List[str] = []
        entries = aws_cli_proxy.parse_entries(
            {"profiles": {"dev": {"profile": "my-dev-profile"}}},
            self._report(messages),
        )
        self.assertEqual(entries, [aws_cli_proxy.ProfileEntry(name="dev", profile="my-dev-profile")])
        self.assertEqual(messages, [])

    def test_複数エントリを記載順に読み込める(self):
        messages: List[str] = []
        entries = aws_cli_proxy.parse_entries(
            {
                "profiles": {
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
        entries = aws_cli_proxy.parse_entries(
            {
                "profiles": {
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
        entries = aws_cli_proxy.parse_entries(
            {
                "profiles": {
                    "dev": {"profile": "dev-profile"},
                    "-evil": {"profile": "evil-profile"},
                }
            },
            self._report(messages),
        )
        self.assertEqual([entry.name for entry in entries], ["dev"])

    def test_profileが先頭ハイフンのエントリはスキップされる(self):
        messages: List[str] = []
        entries = aws_cli_proxy.parse_entries(
            {
                "profiles": {
                    "dev": {"profile": "dev-profile"},
                    "broken": {"profile": "-evil-profile"},
                }
            },
            self._report(messages),
        )
        self.assertEqual([entry.name for entry in entries], ["dev"])

    def test_profile以外の未知キーを持つエントリはスキップされる(self):
        messages: List[str] = []
        entries = aws_cli_proxy.parse_entries(
            {
                "profiles": {
                    "dev": {"profile": "dev-profile"},
                    "broken": {"profile": "broken-profile", "region": "ap-northeast-1"},
                }
            },
            self._report(messages),
        )
        self.assertEqual([entry.name for entry in entries], ["dev"])

    def test_profilesキーが無いとConfigErrorになる(self):
        messages: List[str] = []
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.parse_entries({}, self._report(messages))

    def test_profilesがdictでないとConfigErrorになる(self):
        messages: List[str] = []
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.parse_entries({"profiles": ["dev"]}, self._report(messages))

    def test_有効エントリが0件だとConfigErrorになる(self):
        messages: List[str] = []
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.parse_entries(
                {"profiles": {"broken": {}}},
                self._report(messages),
            )


class LoadConfigTest(unittest.TestCase):
    """load_config のテスト"""

    def _write_yaml(self, tmp_path: Path, content: str) -> Path:
        config_path = tmp_path / "aws-cli-proxy.yml"
        config_path.write_text(textwrap.dedent(content), encoding="utf-8")
        return config_path

    def test_設定ファイルが存在しないとConfigErrorになる(self):
        messages: List[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "nonexistent.yml"
            with self.assertRaises(aws_cli_proxy.ConfigError):
                aws_cli_proxy.load_config(config_path, messages.append)

    def test_ファイルからprofileを持つエントリを読み込める(self):
        messages: List[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self._write_yaml(Path(tmp), """\
                profiles:
                  dev:
                    profile: my-dev-profile
            """)
            entries = aws_cli_proxy.load_config(config_path, messages.append)
            self.assertEqual(
                entries,
                [aws_cli_proxy.ProfileEntry(name="dev", profile="my-dev-profile")],
            )

    def test_YAML構文エラーの設定ファイルはConfigErrorになる(self):
        messages: List[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "aws-cli-proxy.yml"
            # インデントが不正な YAML (マッピングとリストの混在) を書く
            config_path.write_text(
                "profiles:\n  dev:\n  profile: my-dev-profile\n - broken\n",
                encoding="utf-8",
            )
            with self.assertRaises(aws_cli_proxy.ConfigError):
                aws_cli_proxy.load_config(config_path, messages.append)

    def test_設定ファイルのパスがディレクトリだとConfigErrorになる(self):
        messages: List[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "aws-cli-proxy.yml"
            config_path.mkdir()
            with self.assertRaises(aws_cli_proxy.ConfigError):
                aws_cli_proxy.load_config(config_path, messages.append)


class ResolveConfigPathTest(unittest.TestCase):
    """resolve_config_path のテスト"""

    def test_環境変数が設定されていればそのパスが使われる(self):
        path = aws_cli_proxy.resolve_config_path(
            {aws_cli_proxy.CONFIG_PATH_ENV: "/custom/aws-cli-proxy.yml"}
        )
        self.assertEqual(path, Path("/custom/aws-cli-proxy.yml"))

    def test_環境変数が無ければ既定パスが使われる(self):
        path = aws_cli_proxy.resolve_config_path({})
        self.assertEqual(path, aws_cli_proxy.DEFAULT_CONFIG_PATH)


class ResolveTimeoutSecTest(unittest.TestCase):
    """resolve_timeout_sec のテスト"""

    def test_環境変数が無ければ既定値を使う(self):
        self.assertEqual(aws_cli_proxy.resolve_timeout_sec({}), aws_cli_proxy.DEFAULT_TIMEOUT_SEC)

    def test_環境変数が数値文字列であればその値を使う(self):
        timeout_sec = aws_cli_proxy.resolve_timeout_sec({aws_cli_proxy.TIMEOUT_ENV: "45"})
        self.assertEqual(timeout_sec, 45)

    def test_環境変数が数値でなければConfigErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.resolve_timeout_sec({aws_cli_proxy.TIMEOUT_ENV: "not-a-number"})

    def test_タイムアウトが0以下だとConfigErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.resolve_timeout_sec({aws_cli_proxy.TIMEOUT_ENV: "0"})


class BuildAwsCommandTest(unittest.TestCase):
    """build_aws_command のテスト"""

    def test_aws_profile_pの後にargsがそのまま続く(self):
        self.assertEqual(
            aws_cli_proxy.build_aws_command("p", ["s3", "ls"]),
            ["aws", "--profile", "p", "s3", "ls"],
        )


class TruncateOutputTest(unittest.TestCase):
    """truncate_output のテスト"""

    def test_上限以下のバイト列はそのままdecodeされtruncatedはFalse(self):
        text, truncated = aws_cli_proxy.truncate_output(b"hello", 100)
        self.assertEqual(text, "hello")
        self.assertFalse(truncated)

    def test_上限を超えるバイト列は先頭limitバイトに切り詰め注記が続きtruncatedはTrue(self):
        text, truncated = aws_cli_proxy.truncate_output(b"a" * 10, 3)
        self.assertTrue(truncated)
        self.assertTrue(text.startswith("aaa"))
        self.assertIn(aws_cli_proxy.OUTPUT_TRUNCATED_NOTICE.format(limit=3), text)

    def test_マルチバイト文字が境界で切れても例外にならず置換文字を含む(self):
        data = "あ".encode("utf-8")
        text, truncated = aws_cli_proxy.truncate_output(data, 2)
        self.assertTrue(truncated)
        self.assertIn("�", text)

    def test_不正なUTF8バイト列は置換文字になる(self):
        text, truncated = aws_cli_proxy.truncate_output(b"\xff\xfe", 100)
        self.assertFalse(truncated)
        self.assertIn("�", text)


class ResolveMaxOutputBytesTest(unittest.TestCase):
    """resolve_max_output_bytes のテスト"""

    def test_環境変数が無ければ既定値を使う(self):
        self.assertEqual(
            aws_cli_proxy.resolve_max_output_bytes({}),
            aws_cli_proxy.DEFAULT_MAX_OUTPUT_BYTES,
        )

    def test_環境変数が数値文字列であればその値を使う(self):
        self.assertEqual(
            aws_cli_proxy.resolve_max_output_bytes({aws_cli_proxy.MAX_OUTPUT_BYTES_ENV: "2048"}),
            2048,
        )

    def test_環境変数が数値でなければConfigErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.resolve_max_output_bytes({aws_cli_proxy.MAX_OUTPUT_BYTES_ENV: "not-a-number"})

    def test_環境変数が0だとConfigErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.resolve_max_output_bytes({aws_cli_proxy.MAX_OUTPUT_BYTES_ENV: "0"})


class BuildAwsSubprocessEnvTest(unittest.TestCase):
    """build_aws_subprocess_env のテスト"""

    def test_元の環境変数は残りAWS_PAGERが空文字で追加される(self):
        env = aws_cli_proxy.build_aws_subprocess_env({"HOME": "test-home-value"})
        self.assertEqual(env["HOME"], "test-home-value")
        self.assertEqual(env["AWS_PAGER"], "")

    def test_元にAWS_PAGERがあっても空文字で上書きされる(self):
        env = aws_cli_proxy.build_aws_subprocess_env({"AWS_PAGER": "less"})
        self.assertEqual(env["AWS_PAGER"], "")


class CreateAwsCommandRunnerTest(unittest.TestCase):
    """create_aws_command_runner のテスト (実プロセスを起動して検証する)"""

    def _base_env(self) -> Dict[str, str]:
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    def test_標準出力と標準エラー出力と終了コードを取得できる(self):
        runner = aws_cli_proxy.create_aws_command_runner(
            timeout_sec=5, max_output_bytes=1024, env=self._base_env(),
        )
        result = runner(["sh", "-c", "echo out; echo err 1>&2; exit 3"])
        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")
        self.assertEqual(result.returncode, 3)
        self.assertFalse(result.truncated)

    def test_一時ディレクトリをcwdとして実行し呼び出し後に削除される(self):
        runner = aws_cli_proxy.create_aws_command_runner(
            timeout_sec=5, max_output_bytes=1024, env=self._base_env(),
        )
        result = runner(["sh", "-c", "pwd"])
        cwd = result.stdout.strip()
        self.assertNotIn(cwd, ("/", os.getcwd()))
        self.assertFalse(os.path.exists(cwd))

    def test_一時ディレクトリにファイルを書き込める(self):
        runner = aws_cli_proxy.create_aws_command_runner(
            timeout_sec=5, max_output_bytes=1024, env=self._base_env(),
        )
        result = runner(["sh", "-c", "echo x > f; cat f"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "x\n")

    def test_上限を超える出力はtruncatedがTrueで切り詰め注記を含む(self):
        runner = aws_cli_proxy.create_aws_command_runner(
            timeout_sec=5, max_output_bytes=100, env=self._base_env(),
        )
        result = runner(["sh", "-c", "head -c 3000 /dev/zero | tr '\\0' a"])
        self.assertTrue(result.truncated)
        self.assertIn(aws_cli_proxy.OUTPUT_TRUNCATED_NOTICE.format(limit=100), result.stdout)

    def test_タイムアウトするとCommandExecutionErrorになる(self):
        runner = aws_cli_proxy.create_aws_command_runner(
            timeout_sec=1, max_output_bytes=1024, env=self._base_env(),
        )
        with self.assertRaises(aws_cli_proxy.CommandExecutionError):
            runner(["sh", "-c", "sleep 5"])

    def test_存在しないコマンドはCommandExecutionErrorになる(self):
        runner = aws_cli_proxy.create_aws_command_runner(
            timeout_sec=5, max_output_bytes=1024, env=self._base_env(),
        )
        with self.assertRaises(aws_cli_proxy.CommandExecutionError):
            runner(["aws-cli-proxy-test-nonexistent-command-xyz"])

    def test_envに渡したAWS_PAGERが空文字のまま子プロセスに見える(self):
        env = aws_cli_proxy.build_aws_subprocess_env(self._base_env())
        runner = aws_cli_proxy.create_aws_command_runner(
            timeout_sec=5, max_output_bytes=1024, env=env,
        )
        result = runner(["sh", "-c", "echo [$AWS_PAGER]"])
        self.assertEqual(result.stdout, "[]\n")


class RunAwsTest(unittest.TestCase):
    """run_aws のテスト"""

    def test_runnerに渡るargvがaws_profile_pの後にargsが続く形になる(self):
        captured: List[Sequence[str]] = []

        def fake_runner(command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
            captured.append(command)
            return aws_cli_proxy.CommandResult(stdout="", stderr="", returncode=0, truncated=False)

        aws_cli_proxy.run_aws("p", ["s3", "ls"], fake_runner)
        self.assertEqual(captured[0], ["aws", "--profile", "p", "s3", "ls"])


class VerifyAwsCliV2Test(unittest.TestCase):
    """verify_aws_cli_v2 のテスト"""

    def _runner_returning(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        def runner(command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
            return aws_cli_proxy.CommandResult(
                stdout=stdout, stderr=stderr, returncode=returncode, truncated=False,
            )
        return runner

    def test_stdoutにv2のバージョン文字列が返るとそのまま返す(self):
        version = aws_cli_proxy.verify_aws_cli_v2(
            self._runner_returning(stdout="aws-cli/2.36.42 Python/3.12")
        )
        self.assertEqual(version, "aws-cli/2.36.42 Python/3.12")

    def test_stderrにv2のバージョン文字列が返っても同様に返す(self):
        version = aws_cli_proxy.verify_aws_cli_v2(
            self._runner_returning(stderr="aws-cli/2.36.42 Python/3.12")
        )
        self.assertEqual(version, "aws-cli/2.36.42 Python/3.12")

    def test_v1のバージョン文字列はConfigErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.verify_aws_cli_v2(
                self._runner_returning(stderr="aws-cli/1.46.1 Python/2.7")
            )

    def test_returncodeが非0だとConfigErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.verify_aws_cli_v2(
                self._runner_returning(stdout="aws-cli/2.36.42 Python/3.12", returncode=1)
            )

    def test_CommandExecutionErrorはメッセージを引き継いでConfigErrorになる(self):
        def failing_runner(command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
            raise aws_cli_proxy.CommandExecutionError("aws コマンドが見つかりません")

        with self.assertRaises(aws_cli_proxy.ConfigError) as ctx:
            aws_cli_proxy.verify_aws_cli_v2(failing_runner)
        self.assertIn("aws コマンドが見つかりません", str(ctx.exception))


class BuildToolsTest(unittest.TestCase):
    """build_tools のテスト"""

    def test_nameがaws_runになる(self):
        tools = aws_cli_proxy.build_tools(
            [aws_cli_proxy.ProfileEntry(name="dev", profile="dev-profile")]
        )
        self.assertEqual(tools[0]["name"], "aws_run")

    def test_enumに記載順でnameが入る(self):
        entries = [
            aws_cli_proxy.ProfileEntry(name="dev", profile="dev-profile"),
            aws_cli_proxy.ProfileEntry(name="staging", profile="staging-profile"),
        ]
        tools = aws_cli_proxy.build_tools(entries)
        self.assertEqual(
            tools[0]["inputSchema"]["properties"]["name"]["enum"],
            ["dev", "staging"],
        )

    def test_requiredにnameとargsが入る(self):
        tools = aws_cli_proxy.build_tools(
            [aws_cli_proxy.ProfileEntry(name="dev", profile="dev-profile")]
        )
        self.assertEqual(tools[0]["inputSchema"]["required"], ["name", "args"])

    def test_argsのitemsがstringでminItemsが1になる(self):
        tools = aws_cli_proxy.build_tools(
            [aws_cli_proxy.ProfileEntry(name="dev", profile="dev-profile")]
        )
        args_schema = tools[0]["inputSchema"]["properties"]["args"]
        self.assertEqual(args_schema["items"], {"type": "string"})
        self.assertEqual(args_schema["minItems"], 1)


class ValidateArgumentsTest(unittest.TestCase):
    """validate_arguments のテスト"""

    def _entries(self) -> List:
        return [
            aws_cli_proxy.ProfileEntry(name="dev", profile="dev-profile"),
            aws_cli_proxy.ProfileEntry(name="staging", profile="staging-profile"),
        ]

    def test_一致するProfileEntryとargsのタプルを返す(self):
        entry, args = aws_cli_proxy.validate_arguments(
            {"name": "staging", "args": ["s3", "ls"]}, self._entries()
        )
        self.assertEqual(entry, aws_cli_proxy.ProfileEntry(name="staging", profile="staging-profile"))
        self.assertEqual(args, ["s3", "ls"])

    def test_nameが欠落しているとValidationErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ValidationError):
            aws_cli_proxy.validate_arguments({"args": ["s3", "ls"]}, self._entries())

    def test_argsが欠落しているとValidationErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ValidationError):
            aws_cli_proxy.validate_arguments({"name": "dev"}, self._entries())

    def test_未知のフィールドを含むとValidationErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ValidationError):
            aws_cli_proxy.validate_arguments(
                {"name": "dev", "args": ["s3", "ls"], "region": "ap-northeast-1"}, self._entries()
            )

    def test_未登録のnameを指定するとValidationErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ValidationError):
            aws_cli_proxy.validate_arguments({"name": "unknown", "args": ["s3", "ls"]}, self._entries())

    def test_argumentsがdictでないとValidationErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ValidationError):
            aws_cli_proxy.validate_arguments(["dev"], self._entries())

    def test_argsにprofileオプションがあるとvalidate_aws_argsに委譲されてValidationErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ValidationError) as ctx:
            aws_cli_proxy.validate_arguments(
                {"name": "dev", "args": ["s3", "ls", "--profile", "other"]}, self._entries()
            )
        self.assertIn("指定できないオプション", str(ctx.exception))


class FindDeniedOptionTest(unittest.TestCase):
    """find_denied_option のテスト"""

    def test_拒否対象オプションが無ければNoneを返す(self):
        self.assertIsNone(aws_cli_proxy.find_denied_option(["s3", "ls"]))

    def test_profileオプションを検出するとprofileを返す(self):
        self.assertEqual(
            aws_cli_proxy.find_denied_option(["s3", "ls", "--profile", "other"]),
            "--profile",
        )

    def test_イコール形式のendpoint_urlオプションを検出するとendpoint_urlを返す(self):
        self.assertEqual(
            aws_cli_proxy.find_denied_option(["--endpoint-url=http://evil.example", "s3", "ls"]),
            "--endpoint-url",
        )


class ExtractConnectedValueTest(unittest.TestCase):
    """extract_connected_value のテスト"""

    def test_該当しない要素はNoneを返す(self):
        self.assertIsNone(aws_cli_proxy.extract_connected_value("s3"))
        self.assertIsNone(aws_cli_proxy.extract_connected_value("--query"))

    def test_イコールを1つ含む要素は等号以降を返す(self):
        self.assertEqual(aws_cli_proxy.extract_connected_value("--a=b"), "b")

    def test_イコールを複数含む要素は最初のイコール以降をまとめて返す(self):
        self.assertEqual(aws_cli_proxy.extract_connected_value("--a=b=c"), "b=c")


class ValidateAwsArgsTest(unittest.TestCase):
    """validate_aws_args のテスト"""

    def test_オプション連結形式のfileb参照も拒否される(self):
        with self.assertRaises(aws_cli_proxy.ValidationError) as ctx:
            aws_cli_proxy.validate_aws_args(["s3api", "put-object", "--body=fileb://x"])
        self.assertIn("ホストのファイルを参照する引数", str(ctx.exception))

    def _assert_denied(self, args: Any, expected_message_part: str) -> None:
        with self.assertRaises(aws_cli_proxy.ValidationError) as ctx:
            aws_cli_proxy.validate_aws_args(args)
        self.assertIn(expected_message_part, str(ctx.exception))

    def test_s3のlsは通る(self):
        aws_cli_proxy.validate_aws_args(["s3", "ls"])

    def test_stsのget_caller_identityにoutputオプションを付けても通る(self):
        aws_cli_proxy.validate_aws_args(["sts", "get-caller-identity", "--output", "json"])

    def test_末尾のprofileオプション指定は拒否される(self):
        self._assert_denied(["s3", "ls", "--profile", "other"], "指定できないオプション")

    def test_イコール形式のprofileオプション指定は拒否される(self):
        self._assert_denied(["--profile=other", "s3", "ls"], "指定できないオプション")

    def test_profileの省略形profでの指定は拒否される(self):
        self._assert_denied(["s3", "ls", "--prof", "other"], "指定できないオプション")

    def test_profileの省略形pでの指定は拒否される(self):
        self._assert_denied(["--p", "other", "s3", "ls"], "指定できないオプション")

    def test_値の無いprofileオプション単独指定は拒否される(self):
        self._assert_denied(["s3", "ls", "--profile"], "指定できないオプション")

    def test_debugフラグ指定は拒否される(self):
        self._assert_denied(["s3", "ls", "--debug"], "指定できないオプション")

    def test_debugの省略形での指定は拒否される(self):
        self._assert_denied(["--deb", "s3", "ls"], "指定できないオプション")

    def test_no_verify_sslフラグ指定は拒否される(self):
        self._assert_denied(["--no-verify-ssl", "s3", "ls"], "指定できないオプション")

    def test_endpoint_urlオプション指定は拒否される(self):
        self._assert_denied(["s3", "ls", "--endpoint-url", "http://evil.example"], "指定できないオプション")

    def test_イコール形式のendpoint_urlオプション指定は拒否される(self):
        self._assert_denied(["--endpoint-url=http://evil.example", "s3", "ls"], "指定できないオプション")

    def test_ca_bundleオプション指定は拒否される(self):
        self._assert_denied(["--ca-bundle", "x.pem", "s3", "ls"], "指定できないオプション")

    def test_セパレータ単体の要素は拒否される(self):
        self._assert_denied(["s3", "ls", "--", "--profile"], "-- は指定できません")

    def test_helpフラグ指定は拒否される(self):
        self._assert_denied(["s3", "ls", "--help"], "指定できないオプション")

    def test_helpフラグの省略形hでの指定は拒否される(self):
        self._assert_denied(["s3", "ls", "--h"], "指定できないオプション")

    def test_configureサブコマンド単独指定は拒否される(self):
        self._assert_denied(["configure", "list-profiles"], "指定できないサブコマンド")

    def test_configureが末尾にあっても拒否される(self):
        self._assert_denied(["s3", "ls", "configure"], "指定できないサブコマンド")

    def test_ssoサブコマンド指定は拒否される(self):
        self._assert_denied(["sso", "login"], "指定できないサブコマンド")

    def test_helpサブコマンド単独指定は拒否される(self):
        self._assert_denied(["help"], "指定できないサブコマンド")

    def test_helpが末尾にあっても拒否される(self):
        self._assert_denied(["s3", "help"], "指定できないサブコマンド")

    def test_historyサブコマンド指定は拒否される(self):
        self._assert_denied(["history", "list"], "指定できないサブコマンド")

    def test_s3cpのコピー先が絶対パスだと拒否される(self):
        self._assert_denied(["s3", "cp", "s3://b/k", "/tmp/x"], "ホストのファイルを参照する引数")

    def test_s3cpのコピー元がホームディレクトリ参照だと拒否される(self):
        self._assert_denied(["s3", "cp", "~/x", "s3://b/k"], "ホストのファイルを参照する引数")

    def test_s3cpのコピー元がカレントディレクトリ相対パスだと拒否される(self):
        self._assert_denied(["s3", "cp", "./x", "s3://b/k"], "ホストのファイルを参照する引数")

    def test_s3syncの同期先が親ディレクトリだと拒否される(self):
        self._assert_denied(["s3", "sync", "s3://b", ".."], "ホストのファイルを参照する引数")

    def test_パス途中に親ディレクトリ参照を含むと拒否される(self):
        self._assert_denied(["s3", "cp", "a/../b", "s3://b/k"], "ホストのファイルを参照する引数")

    def test_fileスキームのcli_input_json指定は拒否される(self):
        self._assert_denied(
            ["--cli-input-json", "file://x.json", "s3api", "list-buckets"],
            "ホストのファイルを参照する引数",
        )

    def test_大文字のFILEBスキーム指定は拒否される(self):
        self._assert_denied(
            ["s3api", "put-object", "--body", "FILEB://x"],
            "ホストのファイルを参照する引数",
        )

    def test_先頭スラッシュのロググループ名はホストパスと誤検知され拒否される(self):
        # 仕様として受容する誤検知: /aws/lambda/f はホストのパスではなく CloudWatch Logs の
        # ロググループ名だが、先頭が "/" であるため find_host_path_reference が検出してしまう
        self._assert_denied(
            ["logs", "describe-log-streams", "--log-group-name", "/aws/lambda/f"],
            "ホストのファイルを参照する引数",
        )

    def test_相対パスの出力ファイル名は通る(self):
        aws_cli_proxy.validate_aws_args(["s3", "cp", "s3://b/k", "out.json"])

    def test_イコール連結の絶対パスオプションは拒否される(self):
        self._assert_denied(
            ["cloudformation", "deploy", "--template-file=/etc/passwd"],
            "ホストのファイルを参照する引数",
        )

    def test_イコール連結のホームディレクトリ参照オプションは拒否される(self):
        self._assert_denied(["deploy", "push", "--source=~/x"], "ホストのファイルを参照する引数")

    def test_イコール連結で親ディレクトリ参照を含む値は拒否される(self):
        self._assert_denied(
            ["s3", "cp", "s3://b/k", "--x=a/../b"],
            "ホストのファイルを参照する引数",
        )

    def test_イコール連結でもパスに該当しない値は通る(self):
        aws_cli_proxy.validate_aws_args(["--query=Contents[]", "s3api", "list-objects"])

    def test_argsがlist以外だと拒否される(self):
        self._assert_denied("s3 ls", "args は文字列の配列である必要があります")

    def test_空リストは拒否される(self):
        self._assert_denied([], "args には 1 つ以上の引数が必要です")

    def test_文字列でない要素を含むと拒否される(self):
        self._assert_denied(["s3", 1], "args の要素は文字列である必要があります (位置 1)")

    def test_空文字列の要素を含むと拒否される(self):
        self._assert_denied(["s3", ""], "args に空文字列または改行を含む要素があります (位置 1)")

    def test_改行を含む要素を含むと拒否される(self):
        self._assert_denied(["s3", "ls\n"], "args に空文字列または改行を含む要素があります (位置 1)")


class HandleToolsCallTest(unittest.TestCase):
    """handle_tools_call のテスト"""

    def _entries(self) -> List:
        return [aws_cli_proxy.ProfileEntry(name="dev", profile="dev-profile")]

    def _runner_returning(self, result: "aws_cli_proxy.CommandResult"):
        def runner(command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
            return result
        return runner

    def test_成功時はtextがstdoutになりisErrorが無い(self):
        runner = self._runner_returning(
            aws_cli_proxy.CommandResult(
                stdout="bucket-a\nbucket-b\n", stderr="", returncode=0, truncated=False,
            )
        )
        messages: List[str] = []
        result = aws_cli_proxy.handle_tools_call(
            {"name": aws_cli_proxy.TOOL_NAME, "arguments": {"name": "dev", "args": ["s3", "ls"]}},
            self._entries(), runner, messages.append,
        )
        self.assertEqual(result["content"][0]["text"], "bucket-a\nbucket-b\n")
        self.assertNotIn("isError", result)

    def test_失敗時はisErrorがTrueでtextに終了コードとstderrを含む(self):
        runner = self._runner_returning(
            aws_cli_proxy.CommandResult(
                stdout="", stderr="An error occurred (AccessDenied)", returncode=254, truncated=False,
            )
        )
        messages: List[str] = []
        result = aws_cli_proxy.handle_tools_call(
            {"name": aws_cli_proxy.TOOL_NAME, "arguments": {"name": "dev", "args": ["s3", "ls"]}},
            self._entries(), runner, messages.append,
        )
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("254", text)
        self.assertIn("An error occurred (AccessDenied)", text)

    def test_実行後にnameとprofileとreturncodeとtruncatedをreportに渡す(self):
        runner = self._runner_returning(
            aws_cli_proxy.CommandResult(stdout="ok\n", stderr="", returncode=0, truncated=True)
        )
        messages: List[str] = []
        aws_cli_proxy.handle_tools_call(
            {"name": aws_cli_proxy.TOOL_NAME, "arguments": {"name": "dev", "args": ["s3", "ls"]}},
            self._entries(), runner, messages.append,
        )
        self.assertIn(
            "aws_run name=dev profile=dev-profile returncode=0 truncated=True",
            messages,
        )

    def test_runnerがCommandExecutionErrorを投げるとisErrorがTrueになる(self):
        def failing_runner(command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
            raise aws_cli_proxy.CommandExecutionError("aws コマンドが見つかりません")

        messages: List[str] = []
        result = aws_cli_proxy.handle_tools_call(
            {"name": aws_cli_proxy.TOOL_NAME, "arguments": {"name": "dev", "args": ["s3", "ls"]}},
            self._entries(), failing_runner, messages.append,
        )
        self.assertTrue(result["isError"])

    def test_runnerが受け取るargvがawsとprofileと指定したargsになる(self):
        captured: List[Sequence[str]] = []

        def capturing_runner(command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
            captured.append(command)
            return aws_cli_proxy.CommandResult(stdout="", stderr="", returncode=0, truncated=False)

        messages: List[str] = []
        aws_cli_proxy.handle_tools_call(
            {"name": aws_cli_proxy.TOOL_NAME, "arguments": {"name": "dev", "args": ["s3", "ls"]}},
            self._entries(), capturing_runner, messages.append,
        )
        self.assertEqual(captured[0], ["aws", "--profile", "dev-profile", "s3", "ls"])

    def test_未知のツール名を指定するとValidationErrorになる(self):
        def unused_runner(command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
            raise AssertionError("runner は呼ばれないはずです")

        messages: List[str] = []
        with self.assertRaises(aws_cli_proxy.ValidationError):
            aws_cli_proxy.handle_tools_call(
                {"name": "unknown_tool", "arguments": {"name": "dev", "args": ["s3", "ls"]}},
                self._entries(), unused_runner, messages.append,
            )

    def test_paramsにnameが無いとValidationErrorになる(self):
        def unused_runner(command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
            raise AssertionError("runner は呼ばれないはずです")

        messages: List[str] = []
        with self.assertRaises(aws_cli_proxy.ValidationError):
            aws_cli_proxy.handle_tools_call(
                {"arguments": {"name": "dev", "args": ["s3", "ls"]}},
                self._entries(), unused_runner, messages.append,
            )


class HandleJsonrpcRequestTest(unittest.TestCase):
    """handle_jsonrpc_request のテスト"""

    def _entries(self) -> List:
        return [aws_cli_proxy.ProfileEntry(name="dev", profile="dev-profile")]

    def _no_op_runner(self, command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
        return aws_cli_proxy.CommandResult(stdout="", stderr="", returncode=0, truncated=False)

    def test_initializeはserverInfoのnameを返す(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_cli_proxy.handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            entries, aws_cli_proxy.build_tools(entries), self._no_op_runner, messages.append,
        )
        self.assertEqual(response["result"]["serverInfo"]["name"], aws_cli_proxy.SERVER_NAME)

    def test_tools_listはツール1件を返す(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_cli_proxy.handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            entries, aws_cli_proxy.build_tools(entries), self._no_op_runner, messages.append,
        )
        self.assertEqual(len(response["result"]["tools"]), 1)

    def test_未知のメソッドはMETHOD_NOT_FOUNDになる(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_cli_proxy.handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "unknown/method", "params": {}},
            entries, aws_cli_proxy.build_tools(entries), self._no_op_runner, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_cli_proxy.METHOD_NOT_FOUND)

    def test_jsonrpcが2_0でないとINVALID_REQUESTになる(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_cli_proxy.handle_jsonrpc_request(
            {"jsonrpc": "1.0", "id": 1, "method": "initialize", "params": {}},
            entries, aws_cli_proxy.build_tools(entries), self._no_op_runner, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_cli_proxy.INVALID_REQUEST)

    def test_methodが欠落しているとINVALID_REQUESTになる(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_cli_proxy.handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "params": {}},
            entries, aws_cli_proxy.build_tools(entries), self._no_op_runner, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_cli_proxy.INVALID_REQUEST)

    def test_ValidationErrorはINVALID_PARAMSになる(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_cli_proxy.handle_jsonrpc_request(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": aws_cli_proxy.TOOL_NAME,
                    "arguments": {"name": "unknown", "args": ["s3", "ls"]},
                },
            },
            entries, aws_cli_proxy.build_tools(entries), self._no_op_runner, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_cli_proxy.INVALID_PARAMS)

    def test_argsにprofオプションを含むtools_callはINVALID_PARAMSになる(self):
        entries = self._entries()
        messages: List[str] = []
        response = aws_cli_proxy.handle_jsonrpc_request(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": aws_cli_proxy.TOOL_NAME,
                    "arguments": {"name": "dev", "args": ["s3", "ls", "--prof", "other"]},
                },
            },
            entries, aws_cli_proxy.build_tools(entries), self._no_op_runner, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_cli_proxy.INVALID_PARAMS)

    def test_予期しない例外はINTERNAL_ERRORでダミー秘密がmessageに含まれずreportには含まれる(self):
        entries = self._entries()

        def failing_runner(command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
            raise RuntimeError(f"{DUMMY_ACCESS_KEY_ID} を含む")

        messages: List[str] = []
        response = aws_cli_proxy.handle_jsonrpc_request(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": aws_cli_proxy.TOOL_NAME,
                    "arguments": {"name": "dev", "args": ["s3", "ls"]},
                },
            },
            entries, aws_cli_proxy.build_tools(entries), failing_runner, messages.append,
        )
        self.assertEqual(response["error"]["code"], aws_cli_proxy.INTERNAL_ERROR)
        self.assertNotIn(DUMMY_ACCESS_KEY_ID, response["error"]["message"])
        self.assertTrue(any(DUMMY_ACCESS_KEY_ID in message for message in messages))


class ExtractHostNameTest(unittest.TestCase):
    """extract_host_name のテスト"""

    def test_ポートありのホスト名からポートを除く(self):
        self.assertEqual(aws_cli_proxy.extract_host_name("localhost:30722"), "localhost")

    def test_ポートなしのホスト名はそのまま返す(self):
        self.assertEqual(aws_cli_proxy.extract_host_name("localhost"), "localhost")

    def test_IPv6アドレスはポートだけを除いて角括弧付きで返す(self):
        self.assertEqual(aws_cli_proxy.extract_host_name("[::1]:30722"), "[::1]")

    def test_ポートなしのIPv6アドレスはそのまま返す(self):
        self.assertEqual(aws_cli_proxy.extract_host_name("[::1]"), "[::1]")

    def test_前後の空白を除去する(self):
        self.assertEqual(aws_cli_proxy.extract_host_name("  localhost:30722  "), "localhost")

    def test_大文字のホスト名は小文字化される(self):
        self.assertEqual(aws_cli_proxy.extract_host_name("LOCALHOST:30722"), "localhost")

    def test_大文字を含むIPv6アドレスは小文字化される(self):
        self.assertEqual(aws_cli_proxy.extract_host_name("[::FF]:30722"), "[::ff]")


class ExtractOriginHostTest(unittest.TestCase):
    """extract_origin_host のテスト"""

    def test_スキームとポートを含むOriginからホスト名を取り出す(self):
        self.assertEqual(aws_cli_proxy.extract_origin_host("http://localhost:30722"), "localhost")

    def test_ポートなしのOriginからホスト名を取り出す(self):
        self.assertEqual(aws_cli_proxy.extract_origin_host("https://localhost"), "localhost")

    def test_IPv6のOriginからホスト名を取り出す(self):
        self.assertEqual(aws_cli_proxy.extract_origin_host("http://[::1]:30722"), "[::1]")

    def test_netlocが空のOriginは空文字列を返す(self):
        self.assertEqual(aws_cli_proxy.extract_origin_host("not-a-url"), "")

    def test_nullという文字列は空文字列を返す(self):
        self.assertEqual(aws_cli_proxy.extract_origin_host("null"), "")


class WsgiApplicationTest(unittest.TestCase):
    """create_application が返す WSGI アプリケーションのテスト"""

    def _entries(self) -> List:
        return [aws_cli_proxy.ProfileEntry(name="dev", profile="dev-profile")]

    def _runner(self, command: Sequence[str]) -> "aws_cli_proxy.CommandResult":
        return aws_cli_proxy.CommandResult(stdout="bucket-a\n", stderr="", returncode=0, truncated=False)

    def _create_app(self, messages: List[str], allowed_hosts=None):
        return aws_cli_proxy.create_application(
            self._entries(), self._runner, messages.append,
            allowed_hosts if allowed_hosts is not None else aws_cli_proxy.DEFAULT_ALLOWED_HOSTS,
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

    def test_CONTENT_LENGTHが無くても例外にならず本文なしとして処理される(self):
        messages: List[str] = []
        app = self._create_app(messages)

        captured_status: List[str] = []

        def start_response(status: str, headers: List[Tuple[str, str]]) -> None:
            captured_status.append(status)

        environ: Dict[str, Any] = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": "application/json",
            "HTTP_HOST": "localhost:30722",
            "wsgi.input": io.BytesIO(b""),
        }
        result = app(environ, start_response)
        b"".join(result)
        self.assertTrue(captured_status[0].startswith("200"))

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
        self.assertEqual(parsed["error"]["code"], aws_cli_proxy.PARSE_ERROR)
        self.assertIsNone(parsed["id"])

    def test_JSON配列の本文はINVALID_REQUESTになる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, parsed = self._call_app(app, b"[1, 2, 3]")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(parsed["error"]["code"], aws_cli_proxy.INVALID_REQUEST)

    def test_正常なtools_callは200でstdoutを返す(self):
        messages: List[str] = []
        app = self._create_app(messages)
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": aws_cli_proxy.TOOL_NAME, "arguments": {"name": "dev", "args": ["s3", "ls"]}},
        }).encode("utf-8")
        status, parsed = self._call_app(app, body)
        self.assertTrue(status.startswith("200"))
        self.assertEqual(parsed["result"]["content"][0]["text"], "bucket-a\n")

    def test_argsにprofオプションを含むtools_callはINVALID_PARAMSになる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": aws_cli_proxy.TOOL_NAME,
                "arguments": {"name": "dev", "args": ["s3", "ls", "--prof", "other"]},
            },
        }).encode("utf-8")
        status, parsed = self._call_app(app, body)
        self.assertTrue(status.startswith("200"))
        self.assertEqual(parsed["error"]["code"], aws_cli_proxy.INVALID_PARAMS)

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

    def test_Hostヘッダが重複してカンマ結合されていると403になる(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, b"{}", host="localhost:30722, attacker.example:30722")
        self.assertTrue(status.startswith("403"))

    def test_大文字のHostヘッダでも通る(self):
        messages: List[str] = []
        app = self._create_app(messages)
        status, _parsed = self._call_app(app, b"{}", host="LOCALHOST:30722")
        self.assertTrue(status.startswith("200"))

    def test_環境変数に大文字を含むホストを追加しても小文字化されたリクエストが通る(self):
        messages: List[str] = []
        allowed_hosts = aws_cli_proxy.resolve_allowed_hosts(
            {aws_cli_proxy.ALLOWED_HOSTS_ENV: "Host.Docker.Internal"}
        )
        app = self._create_app(messages, allowed_hosts=allowed_hosts)
        status, _parsed = self._call_app(app, b"{}", host="host.docker.internal:30722")
        self.assertTrue(status.startswith("200"))

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
        allowed_hosts = aws_cli_proxy.resolve_allowed_hosts(
            {aws_cli_proxy.ALLOWED_HOSTS_ENV: "host.docker.internal"}
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
        self.assertEqual(aws_cli_proxy.resolve_bind({}), aws_cli_proxy.DEFAULT_BIND)

    def test_環境変数が設定されていればその値を使う(self):
        bind = aws_cli_proxy.resolve_bind({aws_cli_proxy.BIND_ENV: "0.0.0.0"})
        self.assertEqual(bind, "0.0.0.0")


class ResolvePortTest(unittest.TestCase):
    """resolve_port のテスト"""

    def test_環境変数が無ければ既定値を使う(self):
        self.assertEqual(aws_cli_proxy.resolve_port({}), aws_cli_proxy.DEFAULT_PORT)

    def test_環境変数が数値文字列であればその値を使う(self):
        self.assertEqual(aws_cli_proxy.resolve_port({aws_cli_proxy.PORT_ENV: "12345"}), 12345)

    def test_環境変数が数値でなければConfigErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.resolve_port({aws_cli_proxy.PORT_ENV: "not-a-number"})

    def test_ポートが範囲外だとConfigErrorになる(self):
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.resolve_port({aws_cli_proxy.PORT_ENV: "0"})
        with self.assertRaises(aws_cli_proxy.ConfigError):
            aws_cli_proxy.resolve_port({aws_cli_proxy.PORT_ENV: "65536"})


class ResolveAllowedHostsTest(unittest.TestCase):
    """resolve_allowed_hosts のテスト"""

    def test_環境変数が無ければ既定のホスト集合を使う(self):
        self.assertEqual(
            aws_cli_proxy.resolve_allowed_hosts({}),
            aws_cli_proxy.DEFAULT_ALLOWED_HOSTS,
        )

    def test_環境変数のホストを既定のホスト集合に追加する(self):
        allowed_hosts = aws_cli_proxy.resolve_allowed_hosts(
            {aws_cli_proxy.ALLOWED_HOSTS_ENV: "host.docker.internal, example.internal"}
        )
        self.assertIn("host.docker.internal", allowed_hosts)
        self.assertIn("example.internal", allowed_hosts)
        self.assertIn("localhost", allowed_hosts)

    def test_環境変数のホストは小文字化して追加される(self):
        allowed_hosts = aws_cli_proxy.resolve_allowed_hosts(
            {aws_cli_proxy.ALLOWED_HOSTS_ENV: "Host.Docker.Internal"}
        )
        self.assertIn("host.docker.internal", allowed_hosts)
        self.assertNotIn("Host.Docker.Internal", allowed_hosts)


if __name__ == "__main__":
    unittest.main()
