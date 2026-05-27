#!/bin/bash
# rotate_logs.sh — Safe log rotation for the trading bot.
# Run via launchd daily or manually.  Safe to call while bot is running.
# Strategy: copy → compress → truncate-in-place for active logs.
#           compress-and-leave for inactive logs.
# Keeps up to 7 compressed rotations per log file.

set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$PROJ/logs"
SVC_DIR="$LOG_DIR/service"
MAX_SIZE_MB=50   # rotate if log exceeds this size
KEEP_ROTATIONS=7

log() { echo "$(date '+%Y-%m-%d %H:%M:%S'): $*"; }

rotate_active() {
    local f="$1"
    local base="${f%.log}"
    local size_mb
    size_mb=$(du -m "$f" 2>/dev/null | cut -f1)
    if [ "${size_mb:-0}" -lt "$MAX_SIZE_MB" ]; then
        return 0
    fi
    local stamp
    stamp=$(date +%Y%m%d_%H%M%S)
    local archive="${base}_${stamp}.log.gz"
    # Copy → compress → truncate (fd stays open for the writing process)
    cp "$f" "${base}_tmp_$stamp.log"
    gzip -f "${base}_tmp_$stamp.log"
    mv "${base}_tmp_${stamp}.log.gz" "$archive"
    truncate -s 0 "$f"
    log "Rotated $(basename "$f") (${size_mb}MB) → $(basename "$archive")"

    # Prune oldest rotations beyond KEEP_ROTATIONS
    # shellcheck disable=SC2012
    ls -t "${base}_"*.log.gz 2>/dev/null | tail -n "+$((KEEP_ROTATIONS+1))" | xargs rm -f
}

compress_inactive() {
    local f="$1"
    local size_mb
    size_mb=$(du -m "$f" 2>/dev/null | cut -f1)
    if [ "${size_mb:-0}" -lt 1 ]; then
        return 0
    fi
    gzip -f "$f"
    log "Compressed inactive $(basename "$f") (${size_mb}MB)"
}

log "=== Log rotation start ==="

# Active log written by the live bot process
if [ -f "$SVC_DIR/manual_live_bot.log" ]; then
    rotate_active "$SVC_DIR/manual_live_bot.log"
fi

# Root-level bot.log (written by paper launchd service when live bot not running)
if [ -f "$LOG_DIR/bot.log" ]; then
    rotate_active "$LOG_DIR/bot.log"
fi

# Service bot.log (paper launchd)
if [ -f "$SVC_DIR/bot.log" ]; then
    rotate_active "$SVC_DIR/bot.log"
fi

# Streamlit log — compress if large
if [ -f "$SVC_DIR/streamlit.log" ]; then
    rotate_active "$SVC_DIR/streamlit.log"
fi

# Inactive/historical logs — compress if uncompressed and >1MB
for f in "$SVC_DIR/bot_error.log" "$SVC_DIR/dashboard.log" \
          "$SVC_DIR/dashboard_output.log" "$SVC_DIR/dashboard_stdout.log"; do
    if [ -f "$f" ]; then
        compress_inactive "$f"
    fi
done

log "=== Log rotation complete ==="
log "Disk usage logs/: $(du -sh "$LOG_DIR" 2>/dev/null | cut -f1)"
