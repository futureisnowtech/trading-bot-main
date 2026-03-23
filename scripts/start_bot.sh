#!/bin/bash
# start_bot.sh — Wrapper used by launchd to start the trading bot.
# Always starts in PAPER mode so it's safe to auto-restart.
# For live trading, start main.py manually in a terminal.

PROJ="/Users/joshmacbookair2020/Desktop/algo_trading_final"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
LOG_DIR="$PROJ/logs/service"

mkdir -p "$LOG_DIR"

cd "$PROJ" || exit 1

exec "$PYTHON" main.py --mode paper >> "$LOG_DIR/bot.log" 2>> "$LOG_DIR/bot_error.log"
