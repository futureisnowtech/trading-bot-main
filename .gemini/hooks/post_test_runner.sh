#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# post_test_runner.sh  —  LAYER B: Targeted Test Runner
# PostToolUse/Edit|Write  |  always exits 0 (informational only)
# ─────────────────────────────────────────────────────────────────────────────
# Maps the edited file to its most relevant test module and runs it.
# - 60-second timeout per test run
# - Skips broker tests (test_broker_paper.py) — may need live connections
# - Skips sprint2 integration tests — heavy, use manually
# - Never touches runtime DB/state (tests must be self-contained)
# ─────────────────────────────────────────────────────────────────────────────

INPUT=$(cat)
FILE=$(printf "%s" "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('file_path', ''))
except Exception:
    print('')
" 2>/dev/null)

# Only act on .py files
if [[ "$FILE" != *.py ]] || [ ! -f "$FILE" ]; then
    exit 0
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTEST="python3 -m pytest"

BASENAME=$(basename "$FILE" .py)
DIRPART=$(dirname "$FILE")
TEST_FILE=""

# ── DASHBOARD FILE DETECTION ─────────────────────────────────────────────────
# Any edit under dashboard/ (app.py, data/*, widgets/**) runs the import smoke
# suite + sys.path collision guard.  This catches the data/ namespace collision
# (ModuleNotFoundError: No module named 'data.control_tower') before it reaches
# the browser.
if echo "$FILE" | grep -q "/dashboard/"; then
    TEST_FILE="tests/proof/test_dashboard_imports.py tests/proof/test_dashboard_sys_path.py"
    echo "" >&2
    echo "── dashboard import guard ─────────────────────────────────────────────" >&2
    echo "File touched: $FILE" >&2
    echo "Running: $TEST_FILE" >&2
    echo "──────────────────────────────────────────────────────────────────────" >&2
    cd "$REPO_ROOT"
    timeout 60 $PYTEST $TEST_FILE -q --tb=short --no-header -p no:warnings 2>&1 | tail -20 >&2
    exit 0
fi

# ── FILE → TEST MAPPING ──────────────────────────────────────────────────────
case "$BASENAME" in
    # Indicator stack
    indicators|atr_regime|cvd|funding_rate|liquidation_levels|macd_advanced|microstructure|open_interest|orderbook|orderflow|rsi_advanced|vwap_mtf|williams_r)
        TEST_FILE="tests/test_indicators.py" ;;

    # Exit logic / position management
    position_manager|stop_loss_manager|exit_*|*exit*)
        TEST_FILE="tests/test_exit_logic.py" ;;

    # Risk management
    risk_manager|drawdown_controller|risk_limits|var_calculator|volatility_regime)
        TEST_FILE="tests/test_risk_manager.py" ;;

    # ML / feature stack
    feature_builder|walk_forward_trainer|model_store|calibration|online_learner|regime_classifier)
        TEST_FILE="tests/test_ml_consistency.py" ;;

    # Perp momentum / entry
    perps_engine|v10_runner|scanner)
        TEST_FILE="tests/test_perp_momentum.py" ;;

    # Broker/execution — skip: may need live connections
    binance_broker|ibkr_broker)
        echo "Skipping broker test (may need live connections) — run 'pytest tests/test_broker_paper.py' manually." >&2
        exit 0 ;;

    # Everything else — run indicator + exit sanity smoke
    *)
        TEST_FILE="" ;;
esac

# If no mapping, run the fast smoke subset
if [ -z "$TEST_FILE" ]; then
    TEST_FILE="tests/test_indicators.py tests/test_exit_logic.py"
fi

# ── VERIFY TEST FILE EXISTS ─────────────────────────────────────────────────
for tf in $TEST_FILE; do
    if [ ! -f "$REPO_ROOT/$tf" ]; then
        TEST_FILE=$(echo "$TEST_FILE" | sed "s|$tf||")
    fi
done
TEST_FILE=$(echo "$TEST_FILE" | xargs)  # trim whitespace

if [ -z "$TEST_FILE" ]; then
    exit 0
fi

# ── RUN TESTS ────────────────────────────────────────────────────────────────
echo "" >&2
echo "── targeted tests ────────────────────────────────────────────────────" >&2
echo "File touched: $BASENAME.py  →  $TEST_FILE" >&2
echo "──────────────────────────────────────────────────────────────────────" >&2

cd "$REPO_ROOT"
timeout 60 $PYTEST $TEST_FILE -q --tb=short --no-header -p no:warnings 2>&1 | tail -20 >&2

exit 0
