#!/bin/bash
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$PROJ/logs/service/dashboard.pid"

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "Dashboard stopped (pid=$PID)"
  fi
  rm -f "$PID_FILE"
  exit 0
fi

pkill -f "streamlit run dashboard/app.py" 2>/dev/null || true
echo "Dashboard stop command sent"
