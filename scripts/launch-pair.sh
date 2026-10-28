#!/usr/bin/env bash
# launch-pair.sh - Launch Dual Strix Halo (KireiStudios Custom HIP Engine)
# Targets: 70.6 tok/s (code/DSpark), ~50 tok/s single user, 42-45 prose, ~600 prefill, up to 113.9 aggregate.
set -Eeuo pipefail

ROOT_DIR="/home/funboy/StrixHaloClusterDS41Kirei"
BIN_DIR="$ROOT_DIR/bin"
ENGINE_DIR="$ROOT_DIR/vendor/strix-halo-engine-lab/build"
ROCM_LIB="/home/funboy/StrixHaloClusterGLM/.engine/venv/lib/python3.14/site-packages/_rocm_sdk_devel/lib"

REMOTE_NODE="02-evo-x3-tb"
LOCAL_IP="10.55.0.1"
REMOTE_IP="10.55.0.2"
COORDINATOR_PORT="9920"
API_PORT="18231"

KIREI_DIR="/home/funboy/models/ds41/kirei"
BASE_MODEL="$KIREI_DIR/DeepSeek-V4.1-Flash-1017-Q2.gguf"
DSPARK_MODEL="$KIREI_DIR/DeepSeek-V4.1-Flash-1017-DSpark-MXFP4.gguf"
OVERLAY_GGUF="$KIREI_DIR/c-u6.gguf"
VOCAB_DIR="$KIREI_DIR/vocab"
TRELLIS_K4="$KIREI_DIR/ours-K4"
TRELLIS_K5="$KIREI_DIR/ours-K5"

WORKER_LOG="/tmp/strix-kirei-worker.log"
COORD_LOG="/tmp/strix-kirei-coordinator.log"

export LD_LIBRARY_PATH="$ROCM_LIB:${LD_LIBRARY_PATH:-}"
export STRIX_DS41_CB4=1
export STRIX_DS41_PACKS="$KIREI_DIR"
if [ -f "$OVERLAY_GGUF" ]; then
    export STRIX_DS41_OVERLAY="$OVERLAY_GGUF"
fi

echo "=== Terminating previous runs ==="
pkill -9 -f 'strix-deepseek41' || true
pkill -9 -f 'ds4-server' || true
pkill -9 -f 'ds4 --rocm' || true
ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_NODE" "pkill -9 -f 'strix-deepseek41' || true; pkill -9 -f 'ds4-server' || true; pkill -9 -f 'ds4 --rocm' || true" || true
sleep 2

SERVE_BIN="$ROOT_DIR/bin/strix-deepseek41-serve"
if [ ! -f "$SERVE_BIN" ] && [ -f "$ENGINE_DIR/strix-deepseek41-serve" ]; then
    SERVE_BIN="$ENGINE_DIR/strix-deepseek41-serve"
fi

echo "=== Starting Coordinator (Rank 0 & Server) on $LOCAL_IP ==="
nohup "$SERVE_BIN" "$BASE_MODEL" \
  --rank 0 \
  --transport "tcp-listen:$COORDINATOR_PORT" \
  --connect-timeout 120 \
  --peer-timeout 120 \
  --http-bind 127.0.0.1 --http-port "$API_PORT" \
  --layers 40 --tp-attention --engram-direct \
  --prefill-path device --prefill-rows 2048 --slots 8 --context 4096 \
  $([ -f "$DSPARK_MODEL" ] && echo "--dspark $DSPARK_MODEL --dspark-vocab auto:$VOCAB_DIR" || true) \
  > "$COORD_LOG" 2>&1 < /dev/null &

sleep 2

echo "=== Starting Worker (Rank 1) on $REMOTE_NODE ==="
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_NODE" \
  "export LD_LIBRARY_PATH='$ROCM_LIB:\${LD_LIBRARY_PATH:-}' && \
   export STRIX_DS41_CB4=1 && \
   export STRIX_DS41_PACKS='$KIREI_DIR' && \
   [ -f '$OVERLAY_GGUF' ] && export STRIX_DS41_OVERLAY='$OVERLAY_GGUF' || true && \
   nohup '$SERVE_BIN' '$BASE_MODEL' \
     --rank 1 \
     --transport tcp-connect:$LOCAL_IP:$COORDINATOR_PORT \
     --connect-timeout 120 \
     --peer-timeout 120 \
     --layers 40 --tp-attention --engram-direct \
     --prefill-path device --prefill-rows 2048 --slots 8 --context 4096 \
     $([ -f "$DSPARK_MODEL" ] && echo "--dspark $DSPARK_MODEL --dspark-vocab auto:$VOCAB_DIR" || true) \
     > '$WORKER_LOG' 2>&1 < /dev/null & echo worker_online"

echo "Waiting for API endpoint to become READY on 127.0.0.1:$API_PORT..."
for i in $(seq 1 120); do
    if curl -fsS -m 2 "http://127.0.0.1:$API_PORT/v1/models" > /tmp/kirei-api-models.json 2>/dev/null; then
        echo "=== [READY] Kirei HIP Dual Engine is live on port $API_PORT ==="
        cat /tmp/kirei-api-models.json
        exit 0
    fi
    sleep 3
done

echo "[ERROR] Engine failed to start within timeout. Coordinator log tail:"
tail -n 50 "$COORD_LOG" || true
echo "Worker log tail:"
ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_NODE" "tail -n 50 '$WORKER_LOG'" 2>/dev/null || true
exit 1

