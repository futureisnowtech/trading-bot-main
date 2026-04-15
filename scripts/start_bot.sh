#!/bin/bash
# start_bot.sh — Wrapper used by launchd to start the trading bot.
# Always starts in PAPER mode so it's safe to auto-restart.
# For live trading, start main.py manually in a terminal.

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
LOG_DIR="$PROJ/logs/service"

mkdir -p "$LOG_DIR"

cd "$PROJ" || exit 1

# Do NOT use exec — keep bash as parent process to break launchd's file lock
# inheritance (Python 3.14 EDEADLK bug in daemon context on macOS).
export PYTHONDONTWRITEBYTECODE=1
"$PYTHON" -B main.py --mode paper
