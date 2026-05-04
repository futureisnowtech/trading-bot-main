#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# pre_bash_blocker.sh  —  LAYER A: Dangerous Command Blocker + PR Gate
# PreToolUse/Bash  |  exit 2 = block, exit 0 = allow
# ─────────────────────────────────────────────────────────────────────────────
# Reads Gemini Code hook JSON from stdin. Extracts the bash command and
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
# ALLOW: CONTROLLED MODE-TRANSITION SCRIPTS
# Gemini may use ONLY these exact repo-local wrappers for mode changes.
# They embed the policy checks and avoid ad hoc live-start commands.
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE '^[[:space:]]*python3([[:space:]]+-B)?[[:space:]]+(.*/)?scripts/go_(live|paper)\.py[[:space:]]*$'; then
    exit 0
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 1: LIVE TRADING — never run live mode from Gemini
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE -- '--mode live'; then
    echo "BLOCKED [LIVE-RISK]: '--mode live' detected." >&2
    echo "Live trading mode must use the controlled launcher, not raw '--mode live'." >&2
    echo "Safe alternative: use '--mode paper' or 'python3 scripts/go_live.py'." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 1b: IMPLICIT LIVE START — stdin-pipe bypass of --mode live
# Blocks "echo 'I UNDERSTAND' | python3 main.py" which implicitly starts
# live trading when PAPER_TRADING=false is in .env. Gemini must never
# initiate a live-start regardless of how it's invoked.
# Live trading must be started by the owner directly in a terminal.
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qiE 'I[[:space:]]+UNDERSTAND' && echo "$CMD" | grep -qE 'main\.py'; then
    echo "BLOCKED [LIVE-RISK]: Implicit live-start via stdin pipe detected." >&2
    echo "Live trading must use the controlled launcher, not stdin piping." >&2
    echo "Use: python3 scripts/go_live.py" >&2
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
# Uses python3 regex to match only at command-token boundaries (^, ;, &&,
# ||, (, newline) — avoids false positives from heredoc/commit message text.
# ════════════════════════════════════════════════════════════════════════════
GIT_DESTRUCTIVE=$(echo "$CMD" | python3 -c "
import sys, re
text = sys.stdin.read()
# Only match 'git <dangerous>' when git starts a command token
pattern = r'(?:^|;|&&|\|\||\(|\n)\s*git\s+(?:reset\s+--hard|clean\s+-fd?(?!\w)|push\s+(?:[^\n]*?\s)?(?:--force|-f)(?:\s|$)|push\s+--force-with-lease)'
if re.search(pattern, text, re.MULTILINE):
    sys.exit(1)
sys.exit(0)
" 2>/dev/null; echo $?)
if [ "$GIT_DESTRUCTIVE" = "1" ]; then
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
if echo "$CMD" | grep -qE '(curl|wget)[^|]*\|[[:space:]]*(bash|sh|zsh|python3?)'; then
    echo "BLOCKED [SECURITY]: Pipe-to-shell pattern detected (curl/wget | bash)." >&2
    echo "Download the script first, inspect it, then run it explicitly." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 6: RUNTIME DB — destructive SQLite on live databases
# Check independently for sqlite3 + protected DB path + destructive keyword
# (order-independent — handles quoted SQL, escaped args, etc.)
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qi 'sqlite3'; then
    if echo "$CMD" | grep -qE 'logs/trades\.db'; then
        if echo "$CMD" | grep -qiE '\b(DROP|DELETE|VACUUM|UPDATE|INSERT)\b'; then
            echo "BLOCKED [DB-INTEGRITY]: Destructive operation on runtime trades.db." >&2
            echo "Work on a safe copy: 'cp logs/trades.db /tmp/trades_test.db' then use that path." >&2
            exit 2
        fi
    fi
    if echo "$CMD" | grep -qE 'trade_memory\.db'; then
        if echo "$CMD" | grep -qiE '\b(DROP|DELETE|VACUUM)\b'; then
            echo "BLOCKED [DB-INTEGRITY]: Destructive operation on runtime trade_memory.db." >&2
            exit 2
        fi
    fi
    if echo "$CMD" | grep -qE 'price_archive\.db'; then
        if echo "$CMD" | grep -qiE '\b(DROP|DELETE|VACUUM)\b'; then
            echo "BLOCKED [DB-INTEGRITY]: Destructive operation on runtime price_archive.db." >&2
            exit 2
        fi
    fi
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
# BLOCK 10: BOT PROCESS KILL — killing the trading bot without review
# Blocks SIGKILL (-9) to main.py to prevent unclean shutdown mid-position.
# Regular SIGTERM (pkill -f "main.py") is allowed — the bot handles it cleanly.
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qE 'kill\s+-9\s+.*main\.py|kill\s+-KILL\s+.*main\.py'; then
    echo "BLOCKED [PROCESS-RISK]: SIGKILL to main.py is not safe." >&2
    echo "Use 'pkill -f main.py' (SIGTERM) for clean shutdown with position state preserved." >&2
    echo "If bot is stuck, inspect first: 'ps aux | grep main.py'" >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 11: PRICE/TRADE ARCHIVE DESTRUCTION
# These DBs hold historical price data and trade memory. Destruction loses
# all accumulated learning signal and triggers full-retrain on next start.
# ════════════════════════════════════════════════════════════════════════════
if echo "$CMD" | grep -qi 'sqlite3'; then
    if echo "$CMD" | grep -qE 'price_archive\.db|trade_memory\.db|memory/.*\.db'; then
        if echo "$CMD" | grep -qiE '\b(DROP|DELETE|VACUUM|TRUNCATE)\b'; then
            echo "BLOCKED [DB-INTEGRITY]: Destructive op on price_archive.db or trade_memory.db." >&2
            echo "These hold historical data and vector memory for the learning loop." >&2
            echo "Work on a safe copy: cp logs/<db>.db /tmp/<db>_test.db" >&2
            exit 2
        fi
    fi
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
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
    [ -z "$REPO_ROOT" ] && REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
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
