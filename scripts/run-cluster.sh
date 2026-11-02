#!/usr/bin/env bash
# run-cluster.sh - Foreground runner for Dual AMD Strix Halo Kirei HIP Cluster
set -Eeuo pipefail

ROOT_DIR="/home/funboy/StrixHaloClusterDS41Kirei"
REMOTE_NODE="02-evo-x3-tb"
COORDINATOR_PORT="9920"
API_PORT="18231"
ROCM_LIB="/home/funboy/StrixHaloClusterDS41Kirei/.venv-rocm/lib/python3.14/site-packages/_rocm_sdk_devel/lib"

KIREI_DIR="/home/funboy/models/ds41/kirei"
BASE_MODEL="/home/funboy/models/ds41/ds41-abliterated-q2-imatrix/DeepSeek-V4.1-Flash-Abliterated-Q2-imatrix.gguf"
DSPARK_MODEL="$KIREI_DIR/DeepSeek-V4.1-Flash-1017-DSpark-MXFP4.gguf"
OVERLAY_GGUF="$KIREI_DIR/c-u6.gguf"
VOCAB_DIR="$KIREI_DIR/vocab"
COORD_LOG="/tmp/strix-kirei-coordinator.log"
SERVE_BIN="$ROOT_DIR/bin/strix-deepseek41-serve"

export LD_LIBRARY_PATH="$ROCM_LIB:${LD_LIBRARY_PATH:-}"
export STRIX_DS41_CB4=1
export STRIX_DS41_PACKS="$KIREI_DIR"
if [ -f "$OVERLAY_GGUF" ]; then
    export STRIX_DS41_OVERLAY="$OVERLAY_GGUF"
fi

cleanup() {
    echo "[RUNNER] Cleaning up cluster processes..."
    pkill -9 -f 'strix-deepseek41' 2>/dev/null || true
    ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_NODE" "pkill -9 -f 'strix-deepseek41' 2>/dev/null || true" 2>/dev/null || true
}
trap cleanup INT TERM

echo "=== Terminating previous runs on all nodes ==="
pkill -9 -f 'strix-deepseek41' 2>/dev/null || true
pkill -9 -f 'ds4-server' 2>/dev/null || true
pkill -9 -f 'ds4 --rocm' 2>/dev/null || true
ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_NODE" "pkill -9 -f 'strix-deepseek41' 2>/dev/null || true; pkill -9 -f 'ds4-server' 2>/dev/null || true; pkill -9 -f 'ds4 --rocm' 2>/dev/null || true" 2>/dev/null || true
sleep 1

echo "=== Starting Worker (Rank 1) on $REMOTE_NODE ==="
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_NODE" "$ROOT_DIR/scripts/launch-worker.sh"
sleep 1

echo "=== Executing Coordinator (Rank 0) in foreground ==="
exec "$SERVE_BIN" "$BASE_MODEL" \
  --rank 0 \
  --transport "tcp-listen:$COORDINATOR_PORT" \
  --connect-timeout 120 \
  --peer-timeout 120 \
  --http-bind 127.0.0.1 --http-port "$API_PORT" \
  --layers 40 --tp-attention --engram-direct \
  --prefill-path device --prefill-rows 2048 --slots 1 --context 131072 \
  $([ -f "$DSPARK_MODEL" ] && echo "--dspark $DSPARK_MODEL --dspark-vocab auto:$VOCAB_DIR" || true)
