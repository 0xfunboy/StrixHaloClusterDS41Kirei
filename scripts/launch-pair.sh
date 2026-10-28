#!/usr/bin/env bash
# launch-pair.sh - Launch Dual Strix Halo (KireiStudios Custom HIP Engine)
set -Eeuo pipefail

ROOT_DIR="/home/funboy/StrixHaloClusterDS41Kirei"
REMOTE_NODE="02-evo-x3-tb"
API_PORT="18231"
COORD_LOG="/tmp/strix-kirei-coordinator.log"
WORKER_LOG="/tmp/strix-kirei-worker.log"

echo "=== Terminating previous runs on all nodes ==="
pkill -9 -f 'strix-deepseek41' || true
pkill -9 -f 'ds4-server' || true
pkill -9 -f 'ds4 --rocm' || true
ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_NODE" "pkill -9 -f 'strix-deepseek41' || true; pkill -9 -f 'ds4-server' || true; pkill -9 -f 'ds4 --rocm' || true" || true
sleep 2

echo "=== Starting Coordinator (Rank 0) on local node ==="
"$ROOT_DIR/scripts/launch-coordinator.sh"
sleep 2

echo "=== Starting Worker (Rank 1) on $REMOTE_NODE ==="
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_NODE" "$ROOT_DIR/scripts/launch-worker.sh"

echo "Waiting for API endpoint to become READY on 127.0.0.1:$API_PORT..."
for i in $(seq 1 60); do
    if curl -fsS -m 2 "http://127.0.0.1:$API_PORT/v1/models" > /tmp/kirei-api-models.json 2>/dev/null; then
        echo ""
        echo "=== [READY] Kirei HIP Dual Engine is live on port $API_PORT ==="
        cat /tmp/kirei-api-models.json
        echo ""
        if systemctl --user is-active strix-ds41-workbench.service >/dev/null 2>&1; then
            echo "HaloClu Workbench is active via systemd service."
        elif ! pgrep -f 'workbench/app.py' >/dev/null 2>&1; then
            systemctl --user start strix-ds41-workbench.service 2>/dev/null || setsid /home/funboy/StrixHaloClusterGLM/.engine/venv/bin/python "$ROOT_DIR/workbench/app.py" > /tmp/strix-kirei-workbench.log 2>&1 < /dev/null &
        fi
        echo "HaloClu Workbench live on http://127.0.0.1:18232"
        exit 0
    fi
    sleep 2
done

echo "[ERROR] Engine failed to start within timeout. Coordinator log tail:"
tail -n 40 "$COORD_LOG" || true
echo "Worker log tail:"
ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_NODE" "tail -n 40 '$WORKER_LOG'" 2>/dev/null || true
exit 1
