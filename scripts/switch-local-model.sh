#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE=02-evo-x3-tb
VISION_LAUNCHER=/home/funboy/StrixHaloClusterDS41Kirei/scripts/launch-native-vision-pair.sh
QWEN_UNIT=qwen-gufo.service
KIREI_UNIT=strix-ds41-kirei.service

wait_api() {
    local port=$1
    for _ in $(seq 1 180); do
        if curl -fsS -m 3 "http://127.0.0.1:$port/v1/models" >/dev/null 2>&1; then
            echo "API ready on 127.0.0.1:$port"
            return 0
        fi
        sleep 2
    done
    echo "API on port $port did not become ready." >&2
    return 1
}

stop_deepseek() {
    systemctl --user stop "$KIREI_UNIT" 2>/dev/null || true
    pkill -TERM -x ds4-server 2>/dev/null || true
    pkill -TERM -f '^/home/funboy/StrixHaloClusterDS41Kirei/bin/strix-deepseek41-serve ' 2>/dev/null || true
    ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE" \
        'pkill -TERM -x ds4 2>/dev/null || true; pkill -TERM -f "^/home/funboy/StrixHaloClusterDS41Kirei/bin/strix-deepseek41-serve " 2>/dev/null || true' \
        || true
    for _ in $(seq 1 30); do
        if ! ss -ltnH "sport = :18231" | grep -q .; then return 0; fi
        sleep 1
    done
    echo "DeepSeek API port 18231 is still occupied." >&2
    return 1
}

status() {
    if systemctl --user is-active --quiet "$QWEN_UNIT"; then
        echo "Qwen: active"; curl -fsS -m 3 http://127.0.0.1:18561/v1/models || true
    else
        echo "Qwen: inactive"
    fi
    if pgrep -x ds4-server >/dev/null && curl -fsS -m 3 http://127.0.0.1:18231/v1/models >/dev/null 2>&1; then
        echo "DeepSeek vision: active"; curl -fsS -m 3 http://127.0.0.1:18231/v1/models
    else
        echo "DeepSeek vision: inactive"
    fi
}

case "${1:-status}" in
    qwen)
        systemctl --user stop "$KIREI_UNIT" 2>/dev/null || true
        stop_deepseek
        systemctl --user start "$QWEN_UNIT"
        wait_api 18561
        ;;
    deepseek)
        systemctl --user stop "$QWEN_UNIT" 2>/dev/null || true
        systemctl --user stop "$KIREI_UNIT" 2>/dev/null || true
        "$VISION_LAUNCHER"
        wait_api 18231
        ;;
    stop)
        systemctl --user stop "$QWEN_UNIT" "$KIREI_UNIT" 2>/dev/null || true
        stop_deepseek
        echo "Qwen and DeepSeek stopped."
        ;;
    status)
        status
        ;;
    *)
        echo "usage: $0 qwen|deepseek|stop|status" >&2
        exit 2
        ;;
esac