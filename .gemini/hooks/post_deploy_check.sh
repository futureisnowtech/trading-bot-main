#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# post_deploy_check.sh  —  Deployment integrity gate
# PostToolUse/Bash  |  always exits 0 (never blocks)
# ─────────────────────────────────────────────────────────────────────────────
# Fires after every Bash command. Activates only when the command contained
# "git push". Checks:
#   1. Bot process exists
#   2. Bot started AFTER the latest commit (not running stale code)
#   3. Remote HEAD matches local HEAD (push landed)
# Prints a clear warning to stderr if anything is out of sync.
# The active live path is the controlled tiny-live launcher (`scripts/go_live.py`).
# ─────────────────────────────────────────────────────────────────────────────

INPUT=$(cat)
CMD=$(printf "%s" "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null)

# Only activate on git push commands
if ! echo "$CMD" | grep -q "git push"; then
    exit 0
fi

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)"
[ -z "$REPO_ROOT" ] && exit 0
cd "$REPO_ROOT" || exit 0

echo "" >&2
echo "╔══════════════════════════════════════════════════╗" >&2
echo "║         POST-PUSH DEPLOYMENT CHECK               ║" >&2
echo "╚══════════════════════════════════════════════════╝" >&2

FAIL=0

# ── 1. Check remote is in sync ───────────────────────────────────────────────
LOCAL=$(git rev-parse HEAD 2>/dev/null)
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
REMOTE=$(git rev-parse "origin/$BRANCH" 2>/dev/null)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "  ✅ Remote in sync: $(git log -1 --format='%h %s')" >&2
else
    echo "  ❌ REMOTE OUT OF SYNC — local=$LOCAL remote=${REMOTE:-UNKNOWN}" >&2
    FAIL=1
fi

# ── 2. Find bot process ───────────────────────────────────────────────────────
BOT_PID=$(pgrep -f "boot.py" | head -1)
if [ -z "$BOT_PID" ]; then
    echo "  ⚠️  NO BOT PROCESS FOUND — start with the controlled launcher for the target mode" >&2
    echo "     tiny live: python3 scripts/go_live.py" >&2
    echo "     paper:     python3 scripts/go_paper.py" >&2
    FAIL=1
else
    # ── 3. Compare bot start time to latest commit time ───────────────────────
    # Bot start time in epoch seconds
    BOT_START=$(ps -p "$BOT_PID" -o lstart= 2>/dev/null)
    BOT_EPOCH=$(date -j -f "%a %b %d %T %Y" "$BOT_START" "+%s" 2>/dev/null || \
                date -d "$BOT_START" "+%s" 2>/dev/null)

    # Latest commit time in epoch seconds
    COMMIT_EPOCH=$(git log -1 --format="%ct" 2>/dev/null)

    if [ -n "$BOT_EPOCH" ] && [ -n "$COMMIT_EPOCH" ]; then
        if [ "$BOT_EPOCH" -ge "$COMMIT_EPOCH" ]; then
            COMMIT_HASH=$(git log -1 --format="%h")
            echo "  ✅ Bot (PID $BOT_PID) started AFTER commit $COMMIT_HASH — running latest code" >&2
        else
            COMMIT_HASH=$(git log -1 --format="%h %s")
            echo "  ❌ STALE BOT — process started before latest commit" >&2
            echo "     Latest commit: $COMMIT_HASH" >&2
            echo "     Commit time:   $(date -r "$COMMIT_EPOCH" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo epoch=$COMMIT_EPOCH)" >&2
            echo "     Bot started:   $BOT_START" >&2
            echo "     ACTION NEEDED: restart with the controlled launcher for the target mode" >&2
            FAIL=1
        fi
    else
        echo "  ⚠️  Could not compare timestamps — verify bot is running latest code manually" >&2
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo "" >&2
if [ "$FAIL" -eq 0 ]; then
    echo "  ✅ All deployment checks passed" >&2
else
    echo "  ❌ DEPLOYMENT INCOMPLETE — resolve issues above before this session ends" >&2
fi
echo "" >&2

exit 0
