#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# reload_on_change.sh  —  Live-bot restart reminder
# PostToolUse/Edit|Write  |  always exits 0 (never blocks)
# ─────────────────────────────────────────────────────────────────────────────
# Fires after every file edit. If the changed file is in the live-bot critical
# set, prints a reminder that the bot must be restarted + pushed to take effect.
# Does NOT auto-restart — live restarts require explicit user confirmation.
# ─────────────────────────────────────────────────────────────────────────────

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null)"
[ -z "$REPO_ROOT" ] && exit 0
cd "$REPO_ROOT" || exit 0

# Files that, when changed, require a bot restart to take effect
HOT_FILES=(
    "config.py"
    "scheduler/v10_runner.py"
    "execution/coinbase_spot_broker.py"
    "runtime/spot_strategy.py"
    "runtime/spot_momentum.py"
    "runtime/spot_regime.py"
    "runtime/spot_execution_policy.py"
    "runtime/crypto_tradeability.py"
    "runtime/spot_kill_switch.py"
    "risk/economics_gate.py"
    "position_manager.py"
    "perps_engine.py"
    "spot_engine.py"
    "kill_switch.py"
    "monitoring/health_check.py"
    "signal_engine.py"
    "execution/coinbase_broker.py"
    "execution/coinbase_spot_broker.py"
    "dashboard/data/positions.py"
    "dashboard/data/control_tower.py"
    "main.py"
    "scripts/go_live.py"
    "scripts/check_readiness.py"
    "scripts/live_runtime_audit.py"
    "scripts/boot.py"
)

# Get files changed since last commit (staged + unstaged)
CHANGED=$(git diff --name-only HEAD 2>/dev/null)
STAGED=$(git diff --cached --name-only 2>/dev/null)
ALL_CHANGED="$CHANGED $STAGED"

NEEDS_RESTART=0
CHANGED_HOT=""
for hot in "${HOT_FILES[@]}"; do
    if echo "$ALL_CHANGED" | grep -q "$hot"; then
        NEEDS_RESTART=1
        CHANGED_HOT="$CHANGED_HOT $hot"
    fi
done

# ── Auto-log config.py parameter changes to brain/parameter_changelog.md ─────
if echo "$ALL_CHANGED" | grep -q "config.py"; then
    CHANGELOG="$REPO_ROOT/brain/parameter_changelog.md"
    mkdir -p "$(dirname "$CHANGELOG")"
    DIFF=$(git diff HEAD config.py 2>/dev/null | grep '^[-+]' | grep -v '^---\|^+++' | grep -v '^[-+]#' | head -8 | tr '\n' ' ')
    if [ -n "$DIFF" ]; then
        TS=$(date '+%Y-%m-%d %H:%M')
        echo "| $TS | config.py | $DIFF |" >> "$CHANGELOG"
    fi
fi

if [ "$NEEDS_RESTART" -eq 0 ]; then
    exit 0
fi

BOT_PID=$(pgrep -f "boot.py" | head -1)

echo "" >&2
echo "⚠️  RUNTIME-TRUTH FILES CHANGED — controlled restart required to take effect:" >&2
for f in $CHANGED_HOT; do
    echo "   • $f" >&2
done
echo "" >&2
echo "   Steps:" >&2
echo "   1. git add → git commit → git push" >&2
echo "   2. use the controlled launcher that matches target mode:" >&2
echo "      python3 scripts/go_paper.py" >&2
echo "      python3 scripts/go_live.py" >&2
if [ -n "$BOT_PID" ]; then
    echo "   (Bot PID $BOT_PID is currently running OLD code)" >&2
else
    echo "   ⚠️  No bot process found — bot may already be stopped" >&2
fi
echo "" >&2

exit 0
