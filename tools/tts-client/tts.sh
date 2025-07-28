#!/bin/bash
# TTSクライアント - 行ごと順次処理

TTS_SERVER="${TTS_SERVER:-host.docker.internal:37721}"

# TTSを実行する関数
send_to_tts() {
    local text="$1"

    # 空行はスキップ
    [ -z "$text" ] && return 0

    echo -n "$text" | curl -s -f -X POST \
        --data-binary @- \
        "http://${TTS_SERVER}/tts" || {
        echo "エラー: TTSサーバーへの接続に失敗しました: $text" >&2
        return 1
    }
}

# 引数がある場合は各引数を処理
if [ $# -gt 0 ]; then
    for arg in "$@"; do
        send_to_tts "$arg"
    done
else
    # 標準入力から行ごとに処理
    while IFS= read -r line; do
        send_to_tts "$line"
    done
fi
