#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE_NODE=02-evo-x3-tb
ROOT=/home/funboy/StrixHaloClusterDS41Kirei
DS4_ROOT=/home/funboy/StrixHaloClusterDS41/.vendor/ds4-v41-halo-7d0454b
ROCM_LIB=/home/funboy/StrixHaloClusterGLM/.engine/venv/lib/python3.14/site-packages/_rocm_sdk_devel/lib
MODEL=/home/funboy/models/ds41/ds41-abliterated-q2-imatrix/DeepSeek-V4.1-Flash-Abliterated-Q2-imatrix.gguf
VISION=/home/funboy/models/ds41/vision/DeepSeek-V4.1-Flash-1017-Vision.gguf
COORD=10.55.0.1
TP_PORT=9911
API_PORT=18231
CTX=32768
KIREI_BIN="$ROOT/bin/strix-deepseek41-serve"
KIREI_UNIT=strix-ds41-kirei.service
COORD_LOG=/tmp/ds41-native-vision-coordinator.log
WORKER_LOG=/tmp/ds41-native-vision-worker.log
TP_GATE_TIMEOUT_MS=${DS4_TP_GATE_TIMEOUT_MS:-5000}

[[ -f $MODEL && -f $VISION && -x $DS4_ROOT/ds4-server && -x $DS4_ROOT/ds4 ]] || {
    echo "Missing DS4 model, vision sidecar, or executable." >&2
    exit 2
}
if systemctl --user is-active --quiet qwen-gufo.service; then
    echo "Qwen owns the GPU pair; stop it before switching to DeepSeek vision." >&2
    exit 3
fi
ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_NODE" \
    "test -f '$MODEL' && test -f '$VISION'" || {
    echo "Model or vision sidecar missing on NODE02." >&2
    exit 4
}

if systemctl --user is-active --quiet "$KIREI_UNIT"; then
    systemctl --user stop "$KIREI_UNIT"
fi
pkill -TERM -f "^$KIREI_BIN " 2>/dev/null || true
ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_NODE" \
    "pkill -TERM -f '^$KIREI_BIN ' 2>/dev/null || true"
pkill -TERM -x ds4-server 2>/dev/null || true
ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_NODE" \
    'pkill -TERM -x ds4 2>/dev/null || true'

for _ in $(seq 1 30); do
    if ! ss -ltnH "sport = :$API_PORT" | grep -q .; then break; fi
    sleep 1
done
if ss -ltnH "sport = :$API_PORT" | grep -q .; then
    echo "Port $API_PORT is still owned after stopping Kirei; refusing to start over it." >&2
    exit 5
fi

nohup env LD_LIBRARY_PATH="$ROCM_LIB:${LD_LIBRARY_PATH:-}" DS4_TP_GATE_TIMEOUT_MS="$TP_GATE_TIMEOUT_MS" \
    "$DS4_ROOT/ds4-server" --rocm -m "$MODEL" --vision "$VISION" --ctx "$CTX" \
    --tensor-parallel --role coordinator --listen "$COORD" "$TP_PORT" --transport tcp \
    --batched-session 1 --host 127.0.0.1 --port "$API_PORT" \
    > "$COORD_LOG" 2>&1 < /dev/null &
echo "Coordinator PID $!"

ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_NODE" \
    "nohup env LD_LIBRARY_PATH='$ROCM_LIB' DS4_TP_GATE_TIMEOUT_MS='$TP_GATE_TIMEOUT_MS' '$DS4_ROOT/ds4' --rocm -m '$MODEL' --vision '$VISION' --ctx '$CTX' --tensor-parallel --role worker --coordinator '$COORD' '$TP_PORT' --transport tcp > '$WORKER_LOG' 2>&1 < /dev/null & echo worker-started"

for _ in $(seq 1 180); do
    if pgrep -f "^$DS4_ROOT/ds4-server " >/dev/null &&
        curl -fsS -m 3 "http://127.0.0.1:$API_PORT/v1/models" >/tmp/ds41-native-vision-models.json 2>/dev/null; then
        cat /tmp/ds41-native-vision-models.json
        echo
        curl -fsS -m 3 "http://127.0.0.1:$API_PORT/v1/capabilities" | grep -o '"image_input":[^,}]*' || true
        echo "Vision coordinator ready on 127.0.0.1:$API_PORT; logs: $COORD_LOG and NODE02:$WORKER_LOG"
        exit 0
    fi
    sleep 2
done

echo "DS4 vision server did not become ready; coordinator log:" >&2
tail -n 50 "$COORD_LOG" >&2 || true
echo "worker log:" >&2
ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE_NODE" "tail -n 50 '$WORKER_LOG'" >&2 || true
exit 1