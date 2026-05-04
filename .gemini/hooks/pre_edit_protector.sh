#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# pre_edit_protector.sh  —  LAYER A: Protected File / State Blocker
# PreToolUse/Edit|Write  |  exit 2 = block, exit 0 = allow
# ─────────────────────────────────────────────────────────────────────────────
# Prevents Gemini from editing runtime state, credentials, git internals,
# launchd service plists, and runtime log artifacts.
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

if [ -z "$FILE" ]; then
    exit 0
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 1: CREDENTIALS — .env and key files
# ════════════════════════════════════════════════════════════════════════════
if echo "$FILE" | grep -qE '(^|/)\.env($|\.)|(^|/)\.env\.[a-z]|\.pem$|\.key$|\.p12$|\.pfx$|credentials\.json$|api_keys\.|secrets\.'; then
    echo "BLOCKED [CREDENTIALS]: $FILE is a secrets/credentials file." >&2
    echo "Never edit credentials via Gemini. Modify .env manually in your terminal." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 2: RUNTIME DATABASES — SQLite files
# ════════════════════════════════════════════════════════════════════════════
if echo "$FILE" | grep -qE '\.db$|\.db-shm$|\.db-wal$'; then
    echo "BLOCKED [DB-INTEGRITY]: $FILE is a SQLite database file." >&2
    echo "Never write to database files directly. Use sqlite3 CLI queries on a safe copy." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 3: GIT INTERNALS
# ════════════════════════════════════════════════════════════════════════════
if echo "$FILE" | grep -qE '/\.git/'; then
    echo "BLOCKED [GIT-INTEGRITY]: $FILE is inside .git/. Never edit git internals directly." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 4: LAUNCHD SERVICE PLISTS — trading bot auto-start configs
# ════════════════════════════════════════════════════════════════════════════
if echo "$FILE" | grep -qE 'com\.algotrading[^/]*\.plist$|LaunchAgents[^/]*/[^/]*algotrading'; then
    echo "BLOCKED [SERVICE-RISK]: $FILE is a launchd service plist for the trading bot." >&2
    echo "Service files are managed via 'bash scripts/install_services.sh' only." >&2
    echo "If a plist edit is truly needed, justify it explicitly before proceeding." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 5: RUNTIME LOGS — live log and artifact files
# ════════════════════════════════════════════════════════════════════════════
if echo "$FILE" | grep -qE '/logs/.*\.(log|csv)$'; then
    echo "BLOCKED [RUNTIME-STATE]: $FILE is a runtime log/artifact." >&2
    echo "Never write to logs/ directly. Logs are owned by the trading bot process." >&2
    exit 2
fi

# ════════════════════════════════════════════════════════════════════════════
# BLOCK 6: GEMINI COMMAND LOG — hook observability log
# ════════════════════════════════════════════════════════════════════════════
if echo "$FILE" | grep -qE '\.gemini/logs/commands\.log$'; then
    echo "BLOCKED [META]: commands.log is the hook observability log. Do not edit it." >&2
    exit 2
fi

exit 0
