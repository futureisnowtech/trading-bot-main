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

echo "Snapshot written to: $OUTPUT"
wc -l "$OUTPUT" | awk '{print "Lines: " $1}'
