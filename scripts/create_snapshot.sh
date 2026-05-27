#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="$REPO_ROOT/reports/state_snapshots/latest_architecture_snapshot.txt"

mkdir -p "$(dirname "$OUTPUT")"

echo "# LLM Context Snapshot" > "$OUTPUT"
echo "# Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')" >> "$OUTPUT"
echo "" >> "$OUTPUT"

TARGET_PATHS=(
    "GEMINI.md"
    "AGENTS.md"
    "brain"
    "runtime/spot_strategy.py"
    "data/edge_monitor.py"
    "notifications/telegram_bot.py"
    "notifications/ai_agent.py"
    "notifications/notification_engine.py"
    "spot_engine.py"
)

for p in "${TARGET_PATHS[@]}"; do
    abspath="$REPO_ROOT/$p"
    if [[ -f "$abspath" ]]; then
        echo "### FILE: $p ###" >> "$OUTPUT"
        echo '```' >> "$OUTPUT"
        cat "$abspath" >> "$OUTPUT"
        echo '```' >> "$OUTPUT"
        echo "" >> "$OUTPUT"
    elif [[ -d "$abspath" ]]; then
        echo "### DIRECTORY: $p ###" >> "$OUTPUT"
        find "$abspath" -maxdepth 2 -not -path '*/.*' -type f | while read -r subfile; do
            relpath="${subfile#$REPO_ROOT/}"
            echo "#### File: $relpath ####" >> "$OUTPUT"
            echo '```' >> "$OUTPUT"
            cat "$subfile" >> "$OUTPUT"
            echo '```' >> "$OUTPUT"
            echo "" >> "$OUTPUT"
        done
    else
        echo "### PATH: $p — NOT FOUND ###" >> "$OUTPUT"
        echo "" >> "$OUTPUT"
    fi
done

echo "### DATABASE SCHEMA: logs/trades.db ###" >> "$OUTPUT"
echo '```sql' >> "$OUTPUT"
sqlite3 "$REPO_ROOT/logs/trades.db" ".schema" >> "$OUTPUT"
echo '```' >> "$OUTPUT"
echo "" >> "$OUTPUT"

echo "### SYSTEM PULSE: LIVE DATA ###" >> "$OUTPUT"
echo '#### Active Exposure ####' >> "$OUTPUT"
echo '```' >> "$OUTPUT"
sqlite3 "$REPO_ROOT/logs/trades.db" "SELECT symbol, strategy, qty, entry, stop, target FROM open_positions WHERE paper=0;" >> "$OUTPUT" || echo "No live positions." >> "$OUTPUT"
echo '```' >> "$OUTPUT"

echo '#### 24h Performance (Live) ####' >> "$OUTPUT"
echo '```' >> "$OUTPUT"
sqlite3 "$REPO_ROOT/logs/trades.db" "SELECT COUNT(*) as trades, SUM(pnl_usd) as pnl FROM trades WHERE paper=0 AND ts > datetime('now', '-1 day');" >> "$OUTPUT" || echo "No recent trades." >> "$OUTPUT"
echo '```' >> "$OUTPUT"

echo '#### Recent System Events ####' >> "$OUTPUT"
echo '```' >> "$OUTPUT"
sqlite3 "$REPO_ROOT/logs/trades.db" "SELECT ts, source, level, message FROM system_events ORDER BY ts DESC LIMIT 10;" >> "$OUTPUT"
echo '```' >> "$OUTPUT"
echo "" >> "$OUTPUT"

echo "### PERIPHERAL ASSETS ###" >> "$OUTPUT"
echo '#### Project Structure (Root) ####' >> "$OUTPUT"
echo '```' >> "$OUTPUT"
ls -F "$REPO_ROOT" >> "$OUTPUT"
echo '```' >> "$OUTPUT"

if [[ -f "$REPO_ROOT/algo_bot.tar.gz" ]]; then
    echo '#### Archive Manifest (algo_bot.tar.gz) ####' >> "$OUTPUT"
    echo '```' >> "$OUTPUT"
    tar -tf "$REPO_ROOT/algo_bot.tar.gz" | head -n 50 >> "$OUTPUT"
    echo '```' >> "$OUTPUT"
fi
echo "" >> "$OUTPUT"

echo "### LOG ANALYTICS: ERROR FINGERPRINTS (Last 1000 Lines) ###" >> "$OUTPUT"
echo '```' >> "$OUTPUT"
if [[ -f "$REPO_ROOT/logs/bot.log" ]]; then
    # Filter for ERROR, extract source/module (usually 2nd or 3rd column), and count
    tail -n 1000 "$REPO_ROOT/logs/bot.log" | grep "ERROR" | cut -d' ' -f2-4 | sort | uniq -c | sort -nr >> "$OUTPUT" || echo "No recent errors." >> "$OUTPUT"
else
    echo "bot.log not found." >> "$OUTPUT"
fi
echo '```' >> "$OUTPUT"
echo "" >> "$OUTPUT"

echo "Snapshot written to: $OUTPUT"
wc -l "$OUTPUT" | awk '{print "Lines: " $1}'
