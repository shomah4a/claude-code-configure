#!/usr/bin/env python3
"""cc-stop のユニットテスト

実行: python3 -m unittest commands/test_cc_stop.py
"""

import importlib.machinery
import importlib.util
import unittest
from pathlib import Path


def _load_cc_stop():
    path = Path(__file__).with_name("cc-stop")
    loader = importlib.machinery.SourceFileLoader("cc_stop", str(path))
    spec = importlib.util.spec_from_loader("cc_stop", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cc_stop = _load_cc_stop()


class ShouldSkipのテスト(unittest.TestCase):
    def test_通常の入力ではスキップしない(self):
        self.assertIsNone(cc_stop.should_skip({"last_assistant_message": "x"}, {}))

    def test_再帰防止の環境変数があればスキップする(self):
        reason = cc_stop.should_skip({}, {cc_stop.RECURSION_GUARD_ENV: "1"})
        self.assertIsNotNone(reason)
        self.assertIn("再帰", reason)

    def test_agent_idがあればサブエージェント由来としてスキップする(self):
        reason = cc_stop.should_skip({"agent_id": "abc"}, {})
        self.assertIsNotNone(reason)
        self.assertIn("サブエージェント", reason)

    def test_agent_idが空文字ならスキップしない(self):
        self.assertIsNone(cc_stop.should_skip({"agent_id": ""}, {}))


class NormalizeForSpeechのテスト(unittest.TestCase):
    def test_コードフェンスを除去する(self):
        text = "実行します\n```sh\nls -la\n```\n完了"
        self.assertEqual(cc_stop.normalize_for_speech(text, 120), "実行します 完了")

    def test_インラインコードを除去する(self):
        text = "`settings.json` を更新しました"
        self.assertEqual(cc_stop.normalize_for_speech(text, 120), "を更新しました")

    def test_markdown記号と箇条書き記号を除去する(self):
        text = "## 見出し\n- 項目一\n- **項目二**\n> 引用"
        self.assertEqual(cc_stop.normalize_for_speech(text, 120), "見出し 項目一 項目二 引用")

    def test_改行と連続空白を単一スペースにする(self):
        text = "一行目\n\n  二行目\t三行目"
        self.assertEqual(cc_stop.normalize_for_speech(text, 120), "一行目 二行目 三行目")

    def test_上限文字数で切り詰める(self):
        text = "あ" * 200
        self.assertEqual(cc_stop.normalize_for_speech(text, 120), "あ" * 120)

    def test_記号のみの入力は空文字列になる(self):
        self.assertEqual(cc_stop.normalize_for_speech("### \n- \n```\n```", 120), "")


class BuildUserMessageのテスト(unittest.TestCase):
    def test_本文をtextタグで包む(self):
        result = cc_stop.build_user_message("本文")
        self.assertTrue(result.startswith(cc_stop.TEXT_OPEN_TAG))
        self.assertTrue(result.endswith(cc_stop.TEXT_CLOSE_TAG))
        self.assertIn("本文", result)


class BuildSystemPromptのテスト(unittest.TestCase):
    def test_変換ルールがあればプロンプトに含める(self):
        prompt = cc_stop.build_system_prompt("GitHub → ギットハブ")
        self.assertIn("GitHub → ギットハブ", prompt)
        self.assertIn("カタカナ変換ルール", prompt)

    def test_変換ルールが空なら変換ルール節を含めない(self):
        prompt = cc_stop.build_system_prompt("   ")
        self.assertNotIn("カタカナ変換ルール", prompt)

    def test_データであり指示ではない旨を含める(self):
        prompt = cc_stop.build_system_prompt("")
        self.assertIn("指示ではない", prompt)


class BuildSpeechTextのテスト(unittest.TestCase):
    def test_生成結果を正規化して返す(self):
        def generate(system_prompt, user_message):
            return "**完了**\nしました"

        text, reason = cc_stop.build_speech_text("本文", generate, "")
        self.assertEqual(text, "完了 しました")
        self.assertIsNone(reason)

    def test_生成関数にsystem_promptと本文入りメッセージを渡す(self):
        received = {}

        def generate(system_prompt, user_message):
            received["system_prompt"] = system_prompt
            received["user_message"] = user_message
            return "要約"

        cc_stop.build_speech_text("応答本文です", generate, "ルール")
        self.assertIn("ルール", received["system_prompt"])
        self.assertIn("応答本文です", received["user_message"])

    def test_本文が空ならフォールバック文と理由を返す(self):
        def generate(system_prompt, user_message):
            raise AssertionError("呼ばれてはいけない")

        text, reason = cc_stop.build_speech_text("  \n", generate, "")
        self.assertEqual(text, cc_stop.FALLBACK_TEXT)
        self.assertIn("空", reason)

    def test_生成が例外を送出したらフォールバック文と例外内容を返す(self):
        def generate(system_prompt, user_message):
            raise RuntimeError("Not logged in")

        text, reason = cc_stop.build_speech_text("本文", generate, "")
        self.assertEqual(text, cc_stop.FALLBACK_TEXT)
        self.assertIn("RuntimeError", reason)
        self.assertIn("Not logged in", reason)

    def test_生成結果が記号のみならフォールバック文を返す(self):
        def generate(system_prompt, user_message):
            return "```\n```"

        text, reason = cc_stop.build_speech_text("本文", generate, "")
        self.assertEqual(text, cc_stop.FALLBACK_TEXT)
        self.assertIn("空", reason)

    def test_生成結果を上限文字数で切り詰める(self):
        def generate(system_prompt, user_message):
            return "い" * 500

        text, _ = cc_stop.build_speech_text("本文", generate, "")
        self.assertEqual(len(text), cc_stop.MAX_SPEECH_CHARS)


class Runのテスト(unittest.TestCase):
    def test_last_assistant_messageから生成した文を読み上げる(self):
        spoken = []

        def generate(system_prompt, user_message):
            return "読み上げ文"

        cc_stop.run({"last_assistant_message": "本文"}, {}, generate, spoken.append)
        self.assertEqual(spoken, ["読み上げ文"])

    def test_スキップ条件に該当すれば読み上げない(self):
        spoken = []

        def generate(system_prompt, user_message):
            raise AssertionError("呼ばれてはいけない")

        cc_stop.run({"last_assistant_message": "本文", "agent_id": "a"}, {}, generate, spoken.append)
        self.assertEqual(spoken, [])

    def test_last_assistant_messageがなければフォールバック文を読み上げる(self):
        spoken = []

        def generate(system_prompt, user_message):
            raise AssertionError("呼ばれてはいけない")

        cc_stop.run({}, {}, generate, spoken.append)
        self.assertEqual(spoken, [cc_stop.FALLBACK_TEXT])

    def test_生成失敗時はフォールバック文を読み上げる(self):
        spoken = []

        def generate(system_prompt, user_message):
            raise RuntimeError("timeout")

        cc_stop.run({"last_assistant_message": "本文"}, {}, generate, spoken.append)
        self.assertEqual(spoken, [cc_stop.FALLBACK_TEXT])


class ReadHookInputのテスト(unittest.TestCase):
    def test_JSONオブジェクトを辞書として返す(self):
        import io

        result = cc_stop.read_hook_input(io.StringIO('{"last_assistant_message": "x"}'))
        self.assertEqual(result, {"last_assistant_message": "x"})

    def test_空入力は空の辞書を返す(self):
        import io

        self.assertEqual(cc_stop.read_hook_input(io.StringIO("")), {})

    def test_オブジェクト以外のJSONは空の辞書を返す(self):
        import io

        self.assertEqual(cc_stop.read_hook_input(io.StringIO("[1, 2]")), {})


if __name__ == "__main__":
    unittest.main()
