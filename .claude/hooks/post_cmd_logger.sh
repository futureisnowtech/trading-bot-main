#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# post_cmd_logger.sh  —  LAYER C: Bash Command Logger
# PostToolUse/Bash  |  always exits 0 (never blocks)
# ─────────────────────────────────────────────────────────────────────────────
# Appends a timestamped record of every Bash command Claude runs to
# .claude/logs/commands.log — gitignored, so never committed.
# ─────────────────────────────────────────────────────────────────────────────

INPUT=$(cat)
CMD=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    cmd = d.get('tool_input', {}).get('command', '')
    # Collapse newlines for single-line log entry
    print(cmd.replace('\n', ' ↵ '))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$CMD" ]; then
    exit 0
fi

LOG_DIR="$(cd "$(dirname "$0")/../.." && pwd)/.claude/logs"
LOG_FILE="$LOG_DIR/commands.log"

# Ensure log dir exists (safe — never in logs/)
mkdir -p "$LOG_DIR"

printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$CMD" >> "$LOG_FILE"

exit 0
