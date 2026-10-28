#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE=02-evo-x3-tb
DS4_ROOT=/home/funboy/StrixHaloClusterDS41/.vendor/ds4-v41-halo-7d0454b
VISION_LAUNCHER=/home/funboy/StrixHaloClusterDS41Kirei/scripts/launch-native-vision-pair.sh

wait_down() {
    local port=$1
    for _ in $(seq 1 60); do
        if ! ss -ltnH "sport = :$port" | grep -q .; then return 0; fi
        sleep 1
    done
    echo "Port $port did not close in time." >&2
    return 1
}

stop_ds4() {
    systemctl --user stop strix-ds41-kirei.service 2>/dev/null || true
    pkill -TERM -x ds4-server 2>/dev/null || true
    ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE" \
        'pkill -TERM -x ds4 2>/dev/null || true; pkill -TERM -x strix-deepseek4 2>/dev/null || true'
    wait_down 18231
    for _ in $(seq 1 60); do
        if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE" 'pgrep -x ds4 >/dev/null || pgrep -x strix-deepseek4 >/dev/null'; then
            return 0
        fi
        sleep 1
    done
    echo "NODE02 worker did not stop in time." >&2
    return 1
}

start_qwen() {
    systemctl --user start qwen-gufo.service
    for _ in $(seq 1 180); do
        if curl -fsS -m 3 http://127.0.0.1:18561/v1/models >/dev/null 2>&1; then
            echo "Qwen vision ready at http://127.0.0.1:18561"
            return 0
        fi
        sleep 2
    done
    journalctl --user -u qwen-gufo.service -n 40 --no-pager >&2
    return 1
}

status() {
    printf 'Qwen: '; systemctl --user is-active qwen-gufo.service 2>/dev/null || true
    printf 'DeepSeek coordinator: '; pgrep -x ds4-server >/dev/null && echo active || echo inactive
    printf 'DeepSeek worker NODE02: '; ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE" 'pgrep -x ds4 >/dev/null' && echo active || echo inactive
    curl -fsS -m 3 http://127.0.0.1:18561/v1/models >/dev/null 2>&1 && echo 'Qwen API: ready' || echo 'Qwen API: down'
    curl -fsS -m 3 http://127.0.0.1:18231/v1/models >/dev/null 2>&1 && echo 'DeepSeek API: ready' || echo 'DeepSeek API: down'
}

case "${1:-status}" in
    qwen)
        systemctl --user stop qwen-gufo.service 2>/dev/null || true
        stop_ds4
        start_qwen
        ;;
    deepseek)
        systemctl --user stop qwen-gufo.service 2>/dev/null || true
        systemctl --user stop strix-ds41-kirei.service 2>/dev/null || true
        "$VISION_LAUNCHER"
        ;;
    status)
        status
        ;;
    *)
        echo "usage: $0 status|qwen|deepseek" >&2
        exit 2
        ;;
esac