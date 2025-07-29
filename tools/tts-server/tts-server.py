#!/usr/bin/env python3
import atexit
import subprocess
import base64
import ipaddress
import threading
from wsgiref.simple_server import make_server

import traceback

PORT = 37721

# デフォルトの読み上げ速度（-10から10、0が標準）
SPEED = 5  # 少し速めに設定

# グローバルロック
tts_lock = threading.Lock()


class TTSEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self._start_process()
        atexit.register(self._cleanup)

    def _start_process(self):
        """PowerShellプロセスを起動して保持"""
        self.process = subprocess.Popen(
            ['powershell.exe', '-ExecutionPolicy', 'Bypass', '-NoLogo', '-Command', '-'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0
        )
        # 初期化
        self.process.stdin.write("Add-Type -AssemblyName System.Speech\n")
        self.process.stdin.write("$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n")
        self.process.stdin.flush()

    def speak(self, text, rate=SPEED):
        """テキストを読み上げ"""
        with self.lock:
            try:
                encoded = base64.b64encode(text.encode('utf-8')).decode()
                commands = [
                    f"$s.Rate = {rate}",
                    f"$bytes = [Convert]::FromBase64String('{encoded}')",
                    "$text = [System.Text.Encoding]::UTF8.GetString($bytes)",
                    "$s.Speak($text)",
                    "Write-Output 'DONE'"
                ]

                for cmd in commands:
                    self.process.stdin.write(cmd + "\n")
                self.process.stdin.flush()

                # 完了を待つ
                while True:
                    line = self.process.stdout.readline()
                    if 'DONE' in line:
                        break

                return True
            except:
                # プロセスが死んでいたら再起動
                self._cleanup()
                self._start_process()
                return False

    def speak(self, text, rate=SPEED):
        """テキストを読み上げ"""
        with self.lock:
            try:
                encoded = base64.b64encode(text.encode('utf-8')).decode()
                commands = [
                    f"$s.Rate = {rate}",
                    f"$bytes = [Convert]::FromBase64String('{encoded}')",
                    "$text = [System.Text.Encoding]::UTF8.GetString($bytes)",
                    "$s.Speak($text)",
                    "Write-Output 'DONE'"
                ]

                for cmd in commands:
                    self.process.stdin.write(cmd + "\n")
                self.process.stdin.flush()

                # 完了を待つ
                while True:
                    line = self.process.stdout.readline()
                    if 'DONE' in line:
                        break

                return True
            except:
                # プロセスが死んでいたら再起動
                self._cleanup()
                self._start_process()
                return False

    def _cleanup(self):
        """クリーンアップ"""
        if self.process:
            try:
                self.process.stdin.write("$s.Dispose()\nexit\n")
                self.process.stdin.flush()
                self.process.terminate()
                self.process.wait(timeout=2)
            except:
                self.process.kill()

# グローバルTTSエンジン
tts_engine = TTSEngine()

def is_allowed(environ):
    """最小限のバリデーション"""
    try:
        ip_addr = ipaddress.ip_address(environ.get('REMOTE_ADDR', ''))
        return (ip_addr.is_private or ip_addr.is_loopback) and \
               environ.get('PATH_INFO') == '/tts' and \
               environ.get('REQUEST_METHOD') == 'POST'
    except:
        return False

def app(environ, start_response):
    if not is_allowed(environ):
        start_response('403 Forbidden', [])
        return [b'']

    length = int(environ.get('CONTENT_LENGTH', 0))
    text = environ['wsgi.input'].read(length).decode('utf-8')

    # 速度パラメータ取得
    query_string = environ.get('QUERY_STRING', '')
    rate = SPEED
    if query_string:
        params = dict(param.split('=') for param in query_string.split('&') if '=' in param)
        try:
            rate = int(params.get('rate', SPEED))
            rate = max(-10, min(10, rate))
        except:
            traceback.print_exc()

    if tts_engine.speak(text, rate):
        start_response('200 OK', [])
        return [b'OK']
    else:
        start_response('500 Internal Server Error', [])
        return [b'TTS Failed']

def main():
    with make_server('0.0.0.0', PORT, app) as httpd:
        print('TTS Server on :', PORT)
        print('(Thread-safe with TTS synchronization)')
        httpd.serve_forever()

if __name__ == '__main__':
    main()
