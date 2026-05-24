#!/bin/bash
# TDX 每日同步脚本
# 功能：检查SMB挂载 → 自动重连 → 运行增量更新
# 由 macOS LaunchAgent 每天 18:00 自动调用

set -euo pipefail

MOUNT_POINT="${TDX_MOUNT_POINT:-/Volumes/tdx_vipdoc}"
SMB_USER="${TDX_SMB_USER:?请设置 TDX_SMB_USER}"
SMB_PASS="${TDX_SMB_PASS:?请设置 TDX_SMB_PASS}"
SMB_HOST="${TDX_SMB_HOST:?请设置 TDX_SMB_HOST}"
SMB_SHARE="${TDX_SMB_SHARE:-vipdoc}"

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROJECT_DIR/venv/bin/python"
LOG="$PROJECT_DIR/logs/tdx_sync.log"

mkdir -p "$PROJECT_DIR/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

# ── 1. 检查并挂载 SMB ──────────────────────────────────────────────────────────
if mount | grep -q "$MOUNT_POINT"; then
    log "SMB 已挂载：$MOUNT_POINT"
else
    log "SMB 未挂载，正在连接 $SMB_HOST..."
    sudo mkdir -p "$MOUNT_POINT"
    if sudo mount_smbfs "//${SMB_USER}:${SMB_PASS}@${SMB_HOST}/${SMB_SHARE}" "$MOUNT_POINT"; then
        log "✅ 挂载成功"
    else
        log "❌ 挂载失败，请检查网络或 Windows 是否开机"
        exit 1
    fi
fi

# ── 2. 运行盘后完整链路 ────────────────────────────────────────────────────────
log "开始盘后 TDX 数据链路..."
"$PYTHON" -m server.scripts.run_tdx_postmarket_sync --vipdoc "$MOUNT_POINT" 2>&1 | tee -a "$LOG"
log "更新完成"
