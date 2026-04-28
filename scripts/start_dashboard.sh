#!/bin/bash
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJ/logs/service"
PID_FILE="$LOG_DIR/dashboard.pid"
OUT_LOG="$LOG_DIR/dashboard.log"
ERR_LOG="$LOG_DIR/dashboard_error.log"

mkdir -p "$LOG_DIR"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    echo "Dashboard already running (pid=$PID)"
    exit 0
  fi
fi

cd "$PROJ"
nohup /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m streamlit run dashboard/app.py \
  --server.runOnSave false \
  >"$OUT_LOG" 2>"$ERR_LOG" &
echo $! > "$PID_FILE"
echo "Dashboard started (pid=$(cat "$PID_FILE"))"
