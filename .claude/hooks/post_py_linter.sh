#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# post_py_linter.sh  —  LAYER B: Python Linter + Syntax Check
# PostToolUse/Edit|Write  |  always exits 0 (informational only)
# ─────────────────────────────────────────────────────────────────────────────
# Runs after every Edit/Write to a Python file:
#   1. py_compile  — catches syntax errors before the bot reload fires
#   2. ruff check  — static analysis (no auto-fix on core trading files)
#
# Core trading DO-NOT-TOUCH files get syntax check only.
# Other files get full ruff check output.
#
# Never auto-fixes. Never blocks. Output is informational to Claude.
# ─────────────────────────────────────────────────────────────────────────────

INPUT=$(cat)
FILE=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('file_path', ''))
except Exception:
    print('')
" 2>/dev/null)

# Only lint .py files
if [[ "$FILE" != *.py ]] || [ ! -f "$FILE" ]; then
    exit 0
fi

RUFF="/Library/Frameworks/Python.framework/Versions/3.14/bin/ruff"

# ── CORE TRADING FILES (syntax check only — never ruff-fix these) ──────────
CORE_PATTERN='(scanner\.py|signal_engine\.py|v10_runner\.py|position_manager\.py|perps_engine\.py|economics_gate\.py|unified_sizer\.py|trade_logger\.py|health_check\.py|notification_engine\.py|app\.py|post_trade_analyzer\.py|signal_performance\.py|dynamic_weights\.py|indicators\.py|feature_builder\.py|walk_forward_trainer\.py|model_store\.py)$'

IS_CORE=0
if echo "$FILE" | grep -qE "$CORE_PATTERN"; then
    IS_CORE=1
fi

# ── STEP 1: SYNTAX CHECK (all files) ───────────────────────────────────────
SYNTAX_RESULT=$(python3 -m py_compile "$FILE" 2>&1)
if [ $? -ne 0 ]; then
    echo "" >&2
    echo "══ SYNTAX ERROR ══════════════════════════════════════════════════" >&2
    echo "File: $FILE" >&2
    echo "$SYNTAX_RESULT" >&2
    echo "Fix this before the bot reloads — a syntax error will crash on import." >&2
    echo "══════════════════════════════════════════════════════════════════" >&2
    exit 0
fi

# ── STEP 2: RUFF CHECK (informational, no auto-fix) ────────────────────────
if [ ! -x "$RUFF" ]; then
    exit 0  # ruff not available — py_compile was enough
fi

if [ "$IS_CORE" -eq 1 ]; then
    # Core file: light check — only errors/critical (E, F), skip style warnings
    RUFF_OUT=$("$RUFF" check --select=E,F --quiet "$FILE" 2>&1)
    if [ -n "$RUFF_OUT" ]; then
        echo "" >&2
        echo "── ruff (core file — errors only) ────────────────────────────────" >&2
        echo "$RUFF_OUT" | head -10 >&2
        echo "──────────────────────────────────────────────────────────────────" >&2
    fi
else
    # Non-core file: full ruff check, informational
    RUFF_OUT=$("$RUFF" check --quiet "$FILE" 2>&1)
    if [ -n "$RUFF_OUT" ]; then
        echo "" >&2
        echo "── ruff ──────────────────────────────────────────────────────────" >&2
        echo "$RUFF_OUT" | head -20 >&2
        echo "──────────────────────────────────────────────────────────────────" >&2
    fi
fi

exit 0
