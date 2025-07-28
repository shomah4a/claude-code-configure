#!/usr/bin/env python3
import subprocess
import base64
import ipaddress
import threading
from wsgiref.simple_server import make_server

PORT = 37721

# グローバルロック
tts_lock = threading.Lock()

def is_allowed(environ):
    """最小限のバリデーション"""
    try:
        ip_addr = ipaddress.ip_address(environ.get('REMOTE_ADDR', ''))
        return (ip_addr.is_private or ip_addr.is_loopback) and \
               environ.get('PATH_INFO') == '/tts' and \
               environ.get('REQUEST_METHOD') == 'POST'
    except:
        return False

def execute_tts(text):
    """TTS実行（排他制御付き）"""
    with tts_lock:  # 一度に一つのTTSのみ実行
        encoded = base64.b64encode(text.encode('utf-8')).decode()

        powershell_cmd = ['powershell.exe', '-ExecutionPolicy', 'Bypass', '-Command']
        tts_script = (
            f"Add-Type -AssemblyName System.Speech;"
            f"$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.Speak([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')));"
            f"$s.Dispose()"
        )

        result = subprocess.run(powershell_cmd + [tts_script])
        return result.returncode == 0

def app(environ, start_response):
    if not is_allowed(environ):
        start_response('403 Forbidden', [])
        return [b'']

    try:
        length = int(environ.get('CONTENT_LENGTH', 0))
        text = environ['wsgi.input'].read(length).decode('utf-8')

        if execute_tts(text):
            start_response('200 OK', [])
            return [b'OK']
        else:
            start_response('500 Internal Server Error', [])
            return [b'TTS Failed']
    except Exception as e:
        print('Server Error:', e)
        start_response('500 Internal Server Error', [])
        return [b'Server Error']

def main():
    with make_server('0.0.0.0', PORT, app) as httpd:
        print('TTS Server on :', PORT)
        print('(Thread-safe with TTS synchronization)')
        httpd.serve_forever()

if __name__ == '__main__':
    main()
