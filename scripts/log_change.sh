#!/bin/bash
# log_change.sh — Append an entry to CHANGELOG.md.
# Usage: bash scripts/log_change.sh "Brief description of what changed"
# Called by Claude (or you) after any modification to the project.

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
CHANGELOG="$PROJ/CHANGELOG.md"
DATE=$(date +%Y-%m-%d)
MSG="${*:-'No message provided'}"

if [ ! -f "$CHANGELOG" ]; then
    echo "# CHANGELOG" > "$CHANGELOG"
    echo "" >> "$CHANGELOG"
fi

# Prepend entry at top (after the header line)
TMP=$(mktemp)
head -2 "$CHANGELOG" > "$TMP"
echo "## $DATE" >> "$TMP"
echo "- $MSG" >> "$TMP"
echo "" >> "$TMP"
tail -n +3 "$CHANGELOG" >> "$TMP"
mv "$TMP" "$CHANGELOG"

echo "Logged to CHANGELOG.md: [$DATE] $MSG"
