#!/usr/bin/env bash
set -Eeuo pipefail

echo "=========================================================="
echo "   StrixHaloClusterDS41Kirei - Dual Node Health Check    "
echo "=========================================================="

REMOTE_NODE="02-evo-x3-tb"
REMOTE_IP="10.55.0.2"
LOCAL_IP="10.55.0.1"

echo "[1/5] Checking Local AMD Strix Halo GPU (gfx1151)..."
if [ -e /dev/kfd ] && [ -e /dev/dri/renderD128 ]; then
    echo "  [OK] /dev/kfd and /dev/dri/renderD128 are present."
else
    echo "  [ERROR] Local GPU render nodes missing!"
    exit 1
fi

LOCAL_RAM_GIB=$(awk '/MemTotal/ {printf "%.1f", $2/1024/1024}' /proc/meminfo)
LOCAL_AVAIL_GIB=$(awk '/MemAvailable/ {printf "%.1f", $2/1024/1024}' /proc/meminfo)
echo "  [OK] Local Unified RAM: ${LOCAL_AVAIL_GIB} GiB available / ${LOCAL_RAM_GIB} GiB total."

echo ""
echo "[2/5] Checking Thunderbolt / USB4 Network Link..."
if ip addr show thunderbolt0 | grep -q "$LOCAL_IP"; then
    echo "  [OK] Local thunderbolt0 configured with IP $LOCAL_IP."
else
    echo "  [WARNING] thunderbolt0 not configured with $LOCAL_IP!"
fi

echo "  Pinging remote node ($REMOTE_IP)..."
if ping -c 3 -W 2 "$REMOTE_IP" > /dev/null 2>&1; then
    RTT=$(ping -c 3 -W 2 "$REMOTE_IP" | tail -n 1 | awk -F '/' '{print $5}')
    echo "  [OK] Ping to $REMOTE_IP successful. RTT avg = ${RTT} ms."
else
    echo "  [ERROR] Cannot ping $REMOTE_IP over thunderbolt0."
fi

echo ""
echo "[3/5] Checking Remote Node SSH and Resources ($REMOTE_NODE)..."
if ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_NODE" "true" 2>/dev/null; then
    echo "  [OK] SSH connection to $REMOTE_NODE verified."
    REMOTE_INFO=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_NODE" "
        REM_RAM=\$(awk '/MemTotal/ {printf \"%.1f\", \$2/1024/1024}' /proc/meminfo)
        REM_AVAIL=\$(awk '/MemAvailable/ {printf \"%.1f\", \$2/1024/1024}' /proc/meminfo)
        echo \"RAM: \${REM_AVAIL} GiB avail / \${REM_RAM} GiB total\"
        if [ -e /dev/kfd ]; then echo \"GPU: /dev/kfd present\"; else echo \"GPU: missing\"; fi
    ")
    echo "  $REMOTE_INFO"
else
    echo "  [ERROR] Failed to connect to $REMOTE_NODE via SSH."
fi

echo ""
echo "[4/5] Checking Model Assets in /home/funboy/models/ds41/..."
BASE_MODEL="/home/funboy/models/ds41/ds41-abliterated-q2-imatrix/DeepSeek-V4.1-Flash-Abliterated-Q2-imatrix.gguf"
if [ -f "$BASE_MODEL" ]; then
    SIZE_GIB=$(du -h "$BASE_MODEL" | cut -f1)
    echo "  [OK] Base Q2 GGUF: $BASE_MODEL ($SIZE_GIB)"
else
    echo "  [WARNING] Base Q2 GGUF not found at $BASE_MODEL"
fi

if [ -f "/home/funboy/StrixHaloClusterDS41Kirei/download_chain.log" ]; then
    echo "  Download Chain Log (last 3 lines):"
    tail -n 3 /home/funboy/StrixHaloClusterDS41Kirei/download_chain.log | sed 's/^/    /'
fi

echo ""
echo "[5/5] Checking USB4 Stream Driver..."
if [ -e /dev/tbstream0 ]; then
    echo "  [OK] Native /dev/tbstream0 character device active (USB4 DMA enabled)."
else
    echo "  [INFO] Standard thunderbolt0 IP transport active; custom character device not loaded."
    echo "         Engine lab will automatically run via high-speed IP transport / socket fallback."
fi

echo ""
echo "=========================================================="
echo "   Cluster Health Check Completed Successfully!           "
echo "=========================================================="

