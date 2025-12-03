#!/usr/bin/env python3
import atexit
import dataclasses
import os
import signal
import subprocess
import sys
import threading
import traceback
import typing


@dataclasses.dataclass
class Tool:
    name: str
    command: typing.List[str]


# このスクリプトのディレクトリを基準にツールのパスを計算
_script_dir = os.path.dirname(os.path.abspath(__file__))
_tts_server_path = os.path.normpath(os.path.join(_script_dir, "..", "tts-server", "tts-server.py"))
_gh_proxy_path = os.path.normpath(os.path.join(_script_dir, "..", "gh-proxy", "gh-proxy.py"))

TOOLS = [
    Tool(name="tts-server", command=["python3", _tts_server_path]),
    Tool(name="gh-proxy", command=["python3", _gh_proxy_path]),
]


# ANSI カラーコード（明るい色のみ使用して可読性を確保）
# stdout 用の色（緑・青・シアン系）
STDOUT_COLORS = [
    "\033[1;32m",  # 明るい緑
    "\033[1;34m",  # 明るい青
    "\033[1;36m",  # 明るいシアン
    "\033[1;92m",  # 明るい緑（別の色相）
    "\033[1;94m",  # 明るい青（別の色相）
    "\033[1;96m",  # 明るいシアン（別の色相）
]

# stderr 用の色（赤・黄・マゼンタ系）
STDERR_COLORS = [
    "\033[1;91m",  # 明るい赤
    "\033[1;93m",  # 明るい黄
    "\033[1;95m",  # 明るいマゼンタ
    "\033[1;31m",  # 赤
    "\033[1;33m",  # 黄
    "\033[1;35m",  # マゼンタ
]

RESET = "\033[0m"


class ToolLauncher:
    def __init__(self, tools: typing.List[Tool]):
        self.tools = tools
        self.processes: typing.List[subprocess.Popen] = []
        self.threads: typing.List[threading.Thread] = []
        self.shutdown_flag = threading.Event()
        self.print_lock = threading.Lock()

    def _assign_stdout_color(self, index: int) -> str:
        return STDOUT_COLORS[index % len(STDOUT_COLORS)]

    def _assign_stderr_color(self, index: int) -> str:
        return STDERR_COLORS[index % len(STDERR_COLORS)]

    def _read_output(self, stream, output_file, prefix: str, color: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                if self.shutdown_flag.is_set():
                    break
                if line:
                    with self.print_lock:
                        print(f"{color}{prefix}: {line.rstrip()}{RESET}", file=output_file, flush=True)
        except ValueError as e:
            if not self.shutdown_flag.is_set():
                with self.print_lock:
                    print(f"{RESET}エラー: {prefix} の出力読み取り中に ValueError が発生しました: {e}", file=sys.stderr, flush=True)
                    traceback.print_exc(file=sys.stderr)
        finally:
            stream.close()

    def _terminate_process(self, proc: subprocess.Popen, timeout: int = 5) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def _signal_handler(self, signum: int, frame) -> None:
        with self.print_lock:
            print(f"\n{RESET}シグナルを受信しました。全プロセスを終了します...", flush=True)
        self.shutdown()

    def start(self) -> bool:
        if not self.tools:
            with self.print_lock:
                print("エラー: TOOLS リストが空です", file=sys.stderr)
            return False

        with self.print_lock:
            print("ツールを起動しています...\n", flush=True)

        for i, tool in enumerate(self.tools):
            stdout_color = self._assign_stdout_color(i)
            stderr_color = self._assign_stderr_color(i)
            try:
                proc = subprocess.Popen(
                    tool.command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
                )
                self.processes.append(proc)
                with self.print_lock:
                    print(f"{stdout_color}{tool.name}: 起動しました (PID: {proc.pid}){RESET}", flush=True)

                stdout_thread = threading.Thread(
                    target=self._read_output,
                    args=(proc.stdout, sys.stdout, tool.name, stdout_color),
                    daemon=True
                )
                stderr_thread = threading.Thread(
                    target=self._read_output,
                    args=(proc.stderr, sys.stderr, tool.name, stderr_color),
                    daemon=True
                )

                stdout_thread.start()
                stderr_thread.start()
                self.threads.extend([stdout_thread, stderr_thread])

            except FileNotFoundError:
                with self.print_lock:
                    print(f"エラー: コマンドが見つかりません: {' '.join(tool.command)}", file=sys.stderr)
                self.shutdown()
                return False
            except Exception as e:
                with self.print_lock:
                    print(f"エラー: {tool.name} の起動に失敗しました: {e}", file=sys.stderr)
                self.shutdown()
                return False

        with self.print_lock:
            print(f"\n全ツールが起動しました。Ctrl-C で終了します。\n", flush=True)
        return True

    def shutdown(self) -> None:
        self.shutdown_flag.set()

        with self.print_lock:
            print(f"{RESET}\nプロセスを終了しています...", flush=True)
        for proc in self.processes:
            self._terminate_process(proc)

        for proc in self.processes:
            proc.wait()

        with self.print_lock:
            print("全プロセスが終了しました。", flush=True)

    def wait(self) -> None:
        while not self.shutdown_flag.is_set():
            all_done = True
            for proc in self.processes:
                if proc.poll() is None:
                    all_done = False
                    break
            if all_done:
                break
            self.shutdown_flag.wait(timeout=0.1)


def main() -> int:
    launcher = ToolLauncher(TOOLS)

    signal.signal(signal.SIGINT, launcher._signal_handler)
    signal.signal(signal.SIGTERM, launcher._signal_handler)
    atexit.register(launcher.shutdown)

    if not launcher.start():
        return 1

    try:
        launcher.wait()
    except KeyboardInterrupt:
        pass

    launcher.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
