#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# pre_bash_blocker.sh  —  LAYER A: Dangerous Command Blocker + PR Gate
# PreToolUse/Bash  |  exit 2 = block, exit 0 = allow
# ─────────────────────────────────────────────────────────────────────────────
# Reads Claude Code hook JSON from stdin. Extracts the bash command and
# checks it against a deny-list of patterns that are dangerous in a
# live-capable quantitative trading repo.
# ─────────────────────────────────────────────────────────────────────────────

INPUT=$(cat)
CMD=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$CMD" ]; then
    exit 0
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 1: LIVE TRADING — never run live mode from Claude
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE -- '--mode live'; then
    echo "BLOCKED [LIVE-RISK]: '--mode live' detected." >&2
    echo "Live trading mode must be started manually by the owner, not via Claude." >&2
    echo "Safe alternative: use '--mode paper' for all automated testing." >&2
    exit 2
fi

if echo "$CMD" | grep -qE 'promote_perp_live\.py'; then
    echo "BLOCKED [LIVE-RISK]: promote_perp_live.py would flip the system to live trading." >&2
    echo "This requires an explicit human decision, not an automated action." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 2: FORCE-TRADE — closes real positions without review
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE 'force_10_trades\.py'; then
    echo "BLOCKED [POSITION-RISK]: force_10_trades.py force-closes open positions." >&2
    echo "This requires explicit owner authorization with acknowledged loss risk." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 3: DESTRUCTIVE GIT — history rewrite / force push
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE 'git reset --hard|git clean -fd|git clean -f[^a-z]|git push --force|git push -f[ $]|git push.*--force-with-lease'; then
    echo "BLOCKED [GIT-DESTRUCTIVE]: Destructive git command detected." >&2
    echo "Safe alternatives: 'git stash', 'git revert <commit>', or 'git diff' to inspect first." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 4: rm -rf — broad recursive deletion
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE 'rm -rf [^$({]'; then
    echo "BLOCKED [DESTRUCTIVE]: 'rm -rf' with non-variable path detected." >&2
    echo "Use targeted deletion with explicit paths verified first via 'ls'." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 5: PIPE-TO-SHELL — code execution from untrusted downloads
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE '(curl|wget)[^|]*\|[[:space:]]*(bash|sh|zsh|python3?)[^/]'; then
    echo "BLOCKED [SECURITY]: Pipe-to-shell pattern detected (curl/wget | bash)." >&2
    echo "Download the script first, inspect it, then run it explicitly." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 6: RUNTIME DB — destructive SQLite on live databases
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qiE 'sqlite3[[:space:]]+.*logs/trades\.db.*[[:space:]]+(DROP|DELETE FROM|VACUUM|UPDATE|INSERT)'; then
    echo "BLOCKED [DB-INTEGRITY]: Destructive operation on runtime trades.db." >&2
    echo "Work on a safe copy: 'cp logs/trades.db /tmp/trades_test.db' then use that path." >&2
    exit 2
fi
if echo "$CMD" | grep -qiE 'sqlite3[[:space:]]+.*trade_memory\.db.*[[:space:]]+(DROP|DELETE FROM|VACUUM)'; then
    echo "BLOCKED [DB-INTEGRITY]: Destructive operation on runtime trade_memory.db." >&2
    exit 2
fi
if echo "$CMD" | grep -qiE 'sqlite3[[:space:]]+.*price_archive\.db.*[[:space:]]+(DROP|DELETE FROM|VACUUM)'; then
    echo "BLOCKED [DB-INTEGRITY]: Destructive operation on runtime price_archive.db." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 7: CREDENTIALS — reading or exfiltrating .env content
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE '^[[:space:]]*(cat|head|tail)[[:space:]].*\.env[[:space:]]*$'; then
    echo "BLOCKED [CREDENTIALS]: Direct .env file read detected." >&2
    echo "Read specific keys via 'grep ^KEY_NAME= .env' or via config.py imports only." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 8: LAUNCHD SERVICE TAMPERING — disabling trading services
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE 'launchctl (unload|disable|bootout|remove).*algotrading'; then
    # Allow only if it's part of the install/uninstall workflow
    if ! echo "$CMD" | grep -qE 'install_services\.sh'; then
        echo "BLOCKED [SERVICE-RISK]: Disabling a trading bot launchd service outside of install_services.sh." >&2
        echo "Use 'bash scripts/install_services.sh --uninstall' if service removal is intended." >&2
        exit 2
    fi
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 9: PR CREATION GATE — block gh pr create if tests fail
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE 'gh pr create'; then
    echo "PR gate: running smoke tests before allowing PR creation..." >&2
    REPO_ROOT="/Users/joshmacbookair2020/Desktop/algo_trading_final"
    RESULT=$(cd "$REPO_ROOT" && timeout 45 python3 -m pytest tests/test_indicators.py tests/test_exit_logic.py tests/test_ml_consistency.py -q --tb=no --no-header 2>&1 | tail -3)
    if echo "$RESULT" | grep -qiE 'failed|error'; then
        echo "BLOCKED [TEST-GATE]: Tests must pass before creating a PR." >&2
        echo "$RESULT" >&2
        echo "Fix failing tests, then retry gh pr create." >&2
        exit 2
    fi
    echo "PR gate: smoke tests passed — allowing gh pr create." >&2
fi

exit 0
