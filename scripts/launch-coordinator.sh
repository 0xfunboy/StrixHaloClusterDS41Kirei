#!/usr/bin/env bash
# launch-coordinator.sh - Launch Rank 0 Coordinator on 01-EVO-X3
set -Eeuo pipefail

ROOT_DIR="/home/funboy/StrixHaloClusterDS41Kirei"
ROCM_LIB="/home/funboy/StrixHaloClusterGLM/.engine/venv/lib/python3.14/site-packages/_rocm_sdk_devel/lib"
COORDINATOR_PORT="9920"
API_PORT="18231"

KIREI_DIR="/home/funboy/models/ds41/kirei"
BASE_MODEL="$KIREI_DIR/DeepSeek-V4.1-Flash-1017-Q2.gguf"
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

pkill -9 -f 'strix-deepseek41' || true
pkill -9 -f 'ds4-server' || true
pkill -9 -f 'ds4 --rocm' || true
sleep 1

setsid "$SERVE_BIN" "$BASE_MODEL" \
  --rank 0 \
  --transport "tcp-listen:$COORDINATOR_PORT" \
  --connect-timeout 120 \
  --peer-timeout 120 \
  --http-bind 127.0.0.1 --http-port "$API_PORT" \
  --layers 40 --tp-attention --engram-direct \
  --prefill-path device --prefill-rows 2048 --slots 2 --context 32768 \
  $([ -f "$DSPARK_MODEL" ] && echo "--dspark $DSPARK_MODEL --dspark-vocab auto:$VOCAB_DIR" || true) \
  > "$COORD_LOG" 2>&1 < /dev/null &

disown $!
echo "Coordinator (Rank 0) launched in background (PID $!)."

