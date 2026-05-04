#!/bin/bash
# reload_on_change.sh
# Called by Gemini Code PostToolUse hook after every Edit/Write on .py files.
#
# Three-layer safety system:
#   1. DEBOUNCE — only the last edit in a rapid batch triggers a restart (8s window)
#   2. POSITION GUARD — waits up to 90s for open positions to close before killing the bot
#   3. LOCK WAIT — 6s pause for Python 3.14 importlib file lock release (prevents EDEADLK)

INPUT=$(cat)
_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -z "$_REPO_ROOT" ] && _REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Extract file path from hook JSON payload
FILE_PATH=$(echo "$INPUT" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('file_path', ''))
except Exception:
    print('')
" 2>/dev/null)

# Only act on .py files inside this project
if [[ "$FILE_PATH" != *.py ]] || [[ "$FILE_PATH" != */algo_trading_final/* ]]; then
    exit 0
fi

DB_PATH="$_REPO_ROOT/logs/trades.db"
LOG="/tmp/.algo_reload_log"
SENTINEL="/tmp/.algo_reload_pending"
PAPER=1  # 1 = paper trading

# Return count of currently open positions in SQLite
open_position_count() {
    if [ ! -f "$DB_PATH" ]; then
        echo "0"
        return
    fi
    sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM open_positions WHERE paper=$PAPER;" 2>/dev/null || echo "0"
}

# Stamp this trigger with a unique ID (nanosecond timestamp)
TRIGGER_ID="$(date +%s%N)"
echo "$TRIGGER_ID" > "$SENTINEL"

# Background debounce: wait 8s for any further rapid edits to settle.
# If another Edit fires within 8s, it overwrites the sentinel → this one exits silently.
(
    sleep 8
    CURRENT_ID=$(cat "$SENTINEL" 2>/dev/null || echo "0")
    if [ "$CURRENT_ID" != "$TRIGGER_ID" ]; then
        exit 0  # A later edit took over — bail out
    fi

    echo "$(date +%H:%M:%S): Restart queued for $FILE_PATH" >> "$LOG"

    # ── POSITION GUARD ─────────────────────────────────────────────────────────
    # If the bot has open positions, wait up to 90 seconds for them to close
    # naturally (stop/target hit) before killing the process. This prevents the
    # kill-window bug: log_trade written but delete_position not yet called.
    MAX_WAIT=90
    WAITED=0
    OPEN=$(open_position_count)

    while [ "$OPEN" -gt 0 ] && [ "$WAITED" -lt "$MAX_WAIT" ]; do
        echo "$(date +%H:%M:%S): $OPEN open position(s) — holding restart (${WAITED}s/${MAX_WAIT}s)" >> "$LOG"
        sleep 3
        WAITED=$((WAITED + 3))
        OPEN=$(open_position_count)
    done

    if [ "$OPEN" -gt 0 ]; then
        echo "$(date +%H:%M:%S): Position guard timeout — proceeding with restart anyway ($OPEN still open)" >> "$LOG"
    else
        echo "$(date +%H:%M:%S): Positions clear — proceeding with restart" >> "$LOG"
    fi

    # ── KILL + LOCK WAIT ───────────────────────────────────────────────────────
    pkill -SIGTERM -f "main.py" 2>/dev/null || true
    sleep 6  # Python 3.14 importlib needs ~6s to release .pyc file locks (prevents EDEADLK)

    # ── RESTART ────────────────────────────────────────────────────────────────
    PYTHONDONTWRITEBYTECODE=1 TQDM_DISABLE=1 TOKENIZERS_PARALLELISM=false \
      nohup /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
      "$_REPO_ROOT/main.py" --mode paper \
      >> "$_REPO_ROOT/logs/service/bot.log" 2>&1 &

    echo "$(date +%H:%M:%S): Bot restarted" >> "$LOG"
) &

# Exit immediately — background job handles everything, Gemini isn't blocked
exit 0
