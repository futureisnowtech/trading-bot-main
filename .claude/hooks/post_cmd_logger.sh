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

# POST_CMD_LOG_OVERRIDE: env var for test harness isolation — lets test_hooks.sh
# redirect output to a tmp path without depending on .claude/logs/ existence.
if [ -n "$POST_CMD_LOG_OVERRIDE" ]; then
    LOG_FILE="$POST_CMD_LOG_OVERRIDE"
    LOG_DIR="$(dirname "$LOG_FILE")"
else
    # Prefer $CLAUDE_PROJECT_DIR (set by Claude Code for project hooks) for a
    # stable absolute path, then fall back to script-relative resolution.
    if [ -n "$CLAUDE_PROJECT_DIR" ]; then
        LOG_DIR="$CLAUDE_PROJECT_DIR/.claude/logs"
    else
        LOG_DIR="$(cd "$(dirname "$0")/../.." && pwd)/.claude/logs"
    fi
    LOG_FILE="$LOG_DIR/commands.log"
fi

# Ensure log dir exists (safe — never in logs/)
mkdir -p "$LOG_DIR"

printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$CMD" >> "$LOG_FILE"

exit 0
