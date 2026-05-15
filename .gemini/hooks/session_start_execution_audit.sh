#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# session_start_execution_audit.sh — Self-Healing Execution Gate
# SessionStart | Injects mandatory bug-fix directives for trade errors
# ─────────────────────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DB_PATH="$REPO_ROOT/logs/trades.db"
LOG_PATH="$REPO_ROOT/logs/bot.log"

ERROR_REPORT=""

# 1. Check system_events for execution criticals in last 24h
if [ -f "$DB_PATH" ]; then
    SQL_ERRORS=$(python3 -c "
import sqlite3, time
try:
    conn = sqlite3.connect('$DB_PATH')
    cutoff = time.time() - 86400
    rows = conn.execute('SELECT level, source, message FROM system_events WHERE ts > ? AND level IN (\"ERROR\", \"CRITICAL\") AND source IN (\"spot_engine\", \"kill_switch\", \"position_manager\", \"broker\") ORDER BY ts DESC LIMIT 5', (cutoff,)).fetchall()
    if rows:
        print('--- CRITICAL SYSTEM EVENTS ---')
        for r in rows:
            print(f'[{r[0]}] {r[1]}: {r[2]}')
    conn.close()
except Exception:
    pass
" 2>/dev/null)
    ERROR_REPORT="$SQL_ERRORS"
fi

# 2. Check bot.log for recent tracebacks or execution failures
if [ -f "$LOG_PATH" ]; then
    LOG_ERRORS=$(tail -n 1000 "$LOG_PATH" | grep -iE "Traceback|Exception|maker_first failed|rejection" | tail -n 10)
    if [ -n "$LOG_ERRORS" ]; then
        ERROR_REPORT="$ERROR_REPORT\n\n--- RECENT LOG ANOMALIES ---\n$LOG_ERRORS"
    fi
fi

if [ -z "$ERROR_REPORT" ]; then
    echo "{\"context\": \"\"}"
    exit 0
fi

# 3. Format mandatory directive
MANDATORY_DIRECTIVE=$(printf "CRITICAL EXECUTION ERRORS DETECTED:\n%b\n\nMANDATORY DIRECTIVE: As a Lead Engineer, you MUST prioritize fixing these execution bugs. Analyze the tracebacks, identify the root cause in the code, and apply a permanent fix before performing any other tasks." "$ERROR_REPORT")

# Return JSON
echo "$MANDATORY_DIRECTIVE" | python3 -c "
import sys, json
text = sys.stdin.read()
print(json.dumps({\"context\": text}))
"
