#!/usr/bin/env bash
# launch-worker.sh - Launch Rank 1 Worker on 02-evo-x3-tb
set -Eeuo pipefail

ROOT_DIR="/home/funboy/StrixHaloClusterDS41Kirei"
ROCM_LIB="/home/funboy/StrixHaloClusterGLM/.engine/venv/lib/python3.14/site-packages/_rocm_sdk_devel/lib"
LOCAL_IP="10.55.0.1"
COORDINATOR_PORT="9920"

KIREI_DIR="/home/funboy/models/ds41/kirei"
BASE_MODEL="$KIREI_DIR/DeepSeek-V4.1-Flash-1017-Q2.gguf"
DSPARK_MODEL="$KIREI_DIR/DeepSeek-V4.1-Flash-1017-DSpark-MXFP4.gguf"
OVERLAY_GGUF="$KIREI_DIR/c-u6.gguf"
VOCAB_DIR="$KIREI_DIR/vocab"
WORKER_LOG="/tmp/strix-kirei-worker.log"

SERVE_BIN="$ROOT_DIR/bin/strix-deepseek41-serve"

export LD_LIBRARY_PATH="$ROCM_LIB:${LD_LIBRARY_PATH:-}"
export STRIX_DS41_CB4=1
export STRIX_DS41_PACKS="$KIREI_DIR"
if [ -f "$OVERLAY_GGUF" ]; then
    export STRIX_DS41_OVERLAY="$OVERLAY_GGUF"
fi

pkill -9 -f 'strix-deepseek41' || true
pkill -9 -f 'ds4-server' || true
pkill -9 -f 'ds4 --rocm' || true
sleep 1

setsid "$SERVE_BIN" "$BASE_MODEL" \
  --rank 1 \
  --transport "tcp-connect:$LOCAL_IP:$COORDINATOR_PORT" \
  --connect-timeout 120 \
  --peer-timeout 120 \
  --layers 40 --tp-attention --engram-direct \
  --prefill-path device --prefill-rows 2048 --slots 2 --context 32768 \
  $([ -f "$DSPARK_MODEL" ] && echo "--dspark $DSPARK_MODEL --dspark-vocab auto:$VOCAB_DIR" || true) \
  > "$WORKER_LOG" 2>&1 < /dev/null &

disown $!
echo "Worker (Rank 1) launched in background (PID $!)."

