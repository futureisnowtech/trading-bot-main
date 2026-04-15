#!/bin/bash
# iphone.sh — Run this after SSH'ing in from iPhone.
# Creates (or re-attaches to) a screen session with 3 pre-loaded windows.
#
# Usage:  bash ~/Projects/algo_trading_final/scripts/iphone.sh
#
# Navigation inside screen:
#   Ctrl+A, 1  →  LOGS   (live bot log feed)
#   Ctrl+A, 2  →  DB     (sqlite3 — query trades)
#   Ctrl+A, 3  →  CLAUDE (Claude Code in project dir)
#   Ctrl+A, d  →  detach (session stays alive, reconnect anytime)

SESSION="trading"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$DIR/logs/trades.db"

# If session already exists, just attach to it
if screen -list | grep -q "$SESSION"; then
  echo "Re-attaching to existing session..."
  screen -x "$SESSION"
  exit 0
fi

echo "Starting trading session..."

# Window 1: LOGS
screen -dmS "$SESSION" -t LOGS bash -c "
  echo '=== LIVE BOT LOG ===';
  echo 'Waiting for log file...';
  while [ ! -f $DIR/logs/bot.log ]; do sleep 2; done;
  tail -f $DIR/logs/bot.log
"

# Window 2: DB — sqlite3 with a welcome query
screen -S "$SESSION" -X screen -t DB bash -c "
  echo '=== TRADE DATABASE ===';
  echo '';
  echo 'Recent trades:';
  sqlite3 $DB 'SELECT ts, symbol, action, printf(\"%.2f\", pnl_usd) as pnl FROM trades ORDER BY ts DESC LIMIT 10;' 2>/dev/null || echo '(no trades yet)';
  echo '';
  echo 'Type SQL queries below. Example:';
  echo '  SELECT * FROM trades WHERE date(ts)=date(\"now\");';
  echo '';
  sqlite3 $DB
"

# Window 3: CLAUDE — Claude Code in project dir
screen -S "$SESSION" -X screen -t CLAUDE bash -c "
  echo '=== CLAUDE CODE ===';
  cd $DIR;
  echo 'Project: algo_trading_final';
  echo 'Run: claude';
  echo '';
  exec \$SHELL
"

# Attach
screen -x "$SESSION"
