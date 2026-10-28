#!/usr/bin/env bash
# deploy-node02.sh - Deploy binaries, configs and required assets to 02-evo-x3-tb
set -Eeuo pipefail

REMOTE_NODE="02-evo-x3-tb"
LOCAL_DIR="/home/funboy/StrixHaloClusterDS41Kirei"
REMOTE_DIR="/home/funboy/StrixHaloClusterDS41Kirei"

echo "=== Syncing binaries to $REMOTE_NODE ==="
ssh -o BatchMode=yes "$REMOTE_NODE" "mkdir -p '$REMOTE_DIR/bin' '$REMOTE_DIR/scripts' /home/funboy/models/ds41/kirei"
rsync -avzP "$LOCAL_DIR/bin/" "$REMOTE_NODE:$REMOTE_DIR/bin/"
rsync -avzP "$LOCAL_DIR/config.json" "$LOCAL_DIR/model-profile.json" "$REMOTE_NODE:$REMOTE_DIR/"
rsync -avzP "$LOCAL_DIR/scripts/" "$REMOTE_NODE:$REMOTE_DIR/scripts/"

echo "=== Checking assets on $REMOTE_NODE ==="
ssh -o BatchMode=yes "$REMOTE_NODE" "
    if [ -f /home/funboy/models/ds41/ds41-abliterated-q2-imatrix/DeepSeek-V4.1-Flash-Abliterated-Q2-imatrix.gguf ]; then
        echo '  [OK] Base Q2 GGUF model already present on node02.'
    else
        echo '  [WARNING] Base model not found on node02! Transfer required.'
    fi
"

echo "=== Deployment to $REMOTE_NODE completed ==="
