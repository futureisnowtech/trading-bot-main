#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# session_start_intelligence.sh — RBI/ML Scientist Briefing
# SessionStart | Injects actionable intelligence into Gemini's context
# ─────────────────────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DB_PATH="$REPO_ROOT/logs/trades.db"

if [ ! -f "$DB_PATH" ]; then
    echo "{\"context\": \"\"}"
    exit 0
fi

# 1. Query for promoted RBI signals in the last 24 hours
RBI_SIGNALS=$(python3 -c "
import sqlite3, json, time
try:
    conn = sqlite3.connect('$DB_PATH')
    conn.row_factory = sqlite3.Row
    cutoff = time.time() - 86400
    rows = conn.execute('SELECT symbol, feature_combo, win_rate, profit_factor FROM rbi_research WHERE ts > ? AND status=\"promoted\" ORDER BY win_rate DESC LIMIT 3', (cutoff,)).fetchall()
    if rows:
        print('### RECENT RBI RESEARCH ###')
        for r in rows:
            print(f'- {r[\"symbol\"]}: {r[\"feature_combo\"]} | WR: {r[\"win_rate\"]:.1%} | PF: {r[\"profit_factor\"]}')
    else:
        print('No new RBI signals promoted in last 24h.')
    conn.close()
except Exception:
    pass
" 2>/dev/null)

# 2. Query for ML Retrain Queue
ML_QUEUE=$(python3 -c "
import sqlite3, json
try:
    conn = sqlite3.connect('$DB_PATH')
    conn.row_factory = sqlite3.Row
    rows = conn.execute('SELECT pair_key, direction, pending FROM ml_retrain_queue WHERE pending > 0 ORDER BY pending DESC').fetchall()
    if rows:
        print('\n### ML RETRAIN QUEUE ###')
        for r in rows:
            print(f'- {r[\"pair_key\"]} {r[\"direction\"]}: {r[\"pending\"]} pending trades')
    else:
        print('\nML Retrain Queue is empty.')
    conn.close()
except Exception:
    pass
" 2>/dev/null)

FINAL_CONTEXT=$(printf "SCIENTIST BRIEFING:\n%s\n%s" "$RBI_SIGNALS" "$ML_QUEUE")

# Return JSON for Gemini CLI
echo "$FINAL_CONTEXT" | python3 -c "
import sys, json
text = sys.stdin.read()
print(json.dumps({\"context\": text}))
"
