#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="$REPO_ROOT/reports/state_snapshots/latest_architecture_snapshot.txt"

mkdir -p "$(dirname "$OUTPUT")"

echo "# LLM Context Snapshot" > "$OUTPUT"
echo "# Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')" >> "$OUTPUT"
echo "" >> "$OUTPUT"

TARGET_FILES=(
    "CLAUDE.md"
    "AGENTS.md"
    "runtime/spot_strategy.py"
    "spot_engine.py"
)

for f in "${TARGET_FILES[@]}"; do
    filepath="$REPO_ROOT/$f"
    if [[ -f "$filepath" ]]; then
        echo "### FILE: $f ###" >> "$OUTPUT"
        echo '```' >> "$OUTPUT"
        cat "$filepath" >> "$OUTPUT"
        echo '```' >> "$OUTPUT"
        echo "" >> "$OUTPUT"
    else
        echo "### FILE: $f — NOT FOUND ###" >> "$OUTPUT"
        echo "" >> "$OUTPUT"
    fi
done

echo "Snapshot written to: $OUTPUT"
wc -l "$OUTPUT" | awk '{print "Lines: " $1}'
