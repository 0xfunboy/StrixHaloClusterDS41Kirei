#!/usr/bin/env bash
# supervise-cluster.sh - Continuous supervisor for Dual Strix Halo Kirei Cluster
set -Eeuo pipefail

ROOT_DIR="/home/funboy/StrixHaloClusterDS41Kirei"
REMOTE_NODE="02-evo-x3-tb"
API_PORT="18231"

cleanup() {
    echo "[SUPERVISOR] Cleaning up cluster processes..."
    pkill -9 -f 'strix-deepseek41' 2>/dev/null || true
    ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_NODE" "pkill -9 -f 'strix-deepseek41' 2>/dev/null || true" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Run launch-pair
"$ROOT_DIR/scripts/launch-pair.sh"

echo "[SUPERVISOR] Cluster launched and healthy. Entering monitor loop..."
consecutive_failures=0
while true; do
    sleep 3
    if ! pgrep -f 'strix-deepseek41-serve' >/dev/null 2>&1; then
        echo "[SUPERVISOR] Local coordinator process strix-deepseek41-serve is no longer running!"
        exit 1
    fi
    if ! curl -fsS -m 3 "http://127.0.0.1:$API_PORT/v1/models" >/dev/null 2>&1; then
        consecutive_failures=$((consecutive_failures + 1))
        echo "[SUPERVISOR] Health check failure ($consecutive_failures/3)"
        if (( consecutive_failures >= 3 )); then
            echo "[SUPERVISOR] API endpoint unresponsive, exiting to trigger restart..."
            exit 1
        fi
    else
        consecutive_failures=0
    fi
done

