#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# stop_auto_commit.sh  —  LAYER D: Guarded Auto-Commit
# Stop hook  |  always exits 0 (never prevents Gemini from stopping)
# ─────────────────────────────────────────────────────────────────────────────
# Runs when Gemini finishes a response. Guards:
#   1. Nothing to commit → skip silently
#   2. Dangerous files (credentials, DBs, logs, plists) detected → warn, skip
#   3. Syntax errors in changed .py files → warn, skip
#   4. Smoke tests fail → warn, skip
#   5. Safe → auto-commit + push to current branch (never main/master)
#
# Does NOT commit:
#   .env, *.db, *.db-shm, *.db-wal, logs/, *.plist, .gemini/logs/
# ─────────────────────────────────────────────────────────────────────────────

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -z "$REPO_ROOT" ] && REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || exit 0

# ── GUARD: must be in a git repo ─────────────────────────────────────────────
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    exit 0
fi

# ── GUARD: never commit on main or master ────────────────────────────────────
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [[ "$BRANCH" == "main" || "$BRANCH" == "master" ]]; then
    echo "[auto-commit] On $BRANCH — auto-commit is disabled on protected branches." >&2
    exit 0
fi

# ── DETECT CHANGES ───────────────────────────────────────────────────────────
CHANGED_PY=$(git diff --name-only HEAD 2>/dev/null | grep '\.py$')
CHANGED_ALL=$(git diff --name-only HEAD 2>/dev/null)
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | grep -E '\.(py|md|sh|json|txt)$' | grep -v '__pycache__')

if [ -z "$CHANGED_ALL" ] && [ -z "$UNTRACKED" ]; then
    exit 0  # Nothing to commit — exit silently
fi

# ── DANGEROUS FILE SCAN ──────────────────────────────────────────────────────
DANGEROUS_FOUND=0
for f in $CHANGED_ALL $UNTRACKED; do
    if echo "$f" | grep -qE '\.env$|\.db$|\.db-shm$|\.db-wal$|^logs/|\.plist$|\.gemini/logs/'; then
        echo "[auto-commit] SKIP: dangerous file detected: $f" >&2
        DANGEROUS_FOUND=1
    fi
done

if [ "$DANGEROUS_FOUND" -eq 1 ]; then
    echo "[auto-commit] Auto-commit skipped due to dangerous files above." >&2
    echo "[auto-commit] Stage and commit safe files manually." >&2
    exit 0
fi

# ── SYNTAX CHECK ─────────────────────────────────────────────────────────────
for f in $CHANGED_PY; do
    if [ -f "$f" ]; then
        if ! python3 -m py_compile "$f" 2>/dev/null; then
            echo "[auto-commit] SKIP: syntax error in $f — fix before committing." >&2
            exit 0
        fi
    fi
done

# ── SMOKE TESTS (fast, safe subset only) ─────────────────────────────────────
# Only run if Python files were changed (not doc-only changes)
if [ -n "$CHANGED_PY" ]; then
    SMOKE=$(timeout 30 python3 -m pytest \
        tests/test_indicators.py \
        tests/test_exit_logic.py \
        tests/test_ml_consistency.py \
        -q --tb=no --no-header -p no:warnings 2>&1 | tail -2)

    if echo "$SMOKE" | grep -qiE '\bfailed\b|error'; then
        echo "[auto-commit] SKIP: smoke tests failing:" >&2
        echo "$SMOKE" >&2
        echo "[auto-commit] Fix the failures, then commit manually." >&2
        exit 0
    fi
fi

# ── BUILD SAFE FILE LIST ─────────────────────────────────────────────────────
SAFE_FILES=""
for f in $CHANGED_ALL $UNTRACKED; do
    # Skip empty entries
    [ -z "$f" ] && continue
    # Skip dangerous patterns
    echo "$f" | grep -qE '\.env$|\.db$|\.db-shm$|\.db-wal$|^logs/|\.plist$|\.gemini/logs/' && continue
    # Skip if file doesn't exist
    [ -f "$f" ] || continue
    SAFE_FILES="$SAFE_FILES $f"
done
SAFE_FILES=$(echo "$SAFE_FILES" | xargs)  # trim

if [ -z "$SAFE_FILES" ]; then
    exit 0
fi

# ── DETERMINE COMMIT AREAS ────────────────────────────────────────────────────
AREAS=$(echo "$SAFE_FILES" | tr ' ' '\n' | \
    grep -oE '(hooks|scanner|signal_engine|position_manager|risk|ml|learning|dashboard|tests|scripts|indicators|execution|notifications|\.gemini|CHANGELOG|GEMINI)' | \
    sort -u | tr '\n' ',' | sed 's/,$//')
[ -z "$AREAS" ] && AREAS="misc"

# ── STAGE AND COMMIT ─────────────────────────────────────────────────────────
git add $SAFE_FILES 2>/dev/null

# Verify something is actually staged
STAGED=$(git diff --cached --name-only 2>/dev/null)
if [ -z "$STAGED" ]; then
    exit 0
fi

COMMIT_MSG="auto($AREAS): $(date '+%Y-%m-%d %H:%M') — Gemini Code session

Files: $(echo "$STAGED" | tr '\n' ' ' | sed 's/ $//')

Co-Authored-By: Gemini <noreply@google.com>"

git commit -m "$COMMIT_MSG" 2>&1 | tail -2 >&2

# ── STUB VALUE INTEGRITY CHECK ────────────────────────────────────────────────
# Warn if any recent scan_candidates rows have placeholder stop/target values
STUB_COUNT=$(python3 -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('logs/trades.db')
    n = conn.execute('''SELECT COUNT(*) FROM scan_candidates
        WHERE datetime(replace(substr(ts,1,19),\"T\",\" \")) >= datetime(\"now\",\"-1 hour\")
        AND decision != \"data_unavailable\"
        AND (stop_pct IN (0,50,100) OR target_pct IN (0,50,100))
    ''').fetchone()[0]
    print(n)
    conn.close()
except Exception:
    print(0)
" 2>/dev/null)
if [ -n "$STUB_COUNT" ] && [ "$STUB_COUNT" -gt 0 ]; then
    echo "[stub-check] ⚠️  $STUB_COUNT scan_candidates row(s) in last hour have placeholder stop/target values (0, 50, or 100%)" >&2
    echo "[stub-check] Gates are evaluating junk data. Check log_scan_candidate callers." >&2
fi

# ── PUSH TO CURRENT BRANCH ───────────────────────────────────────────────────
if git remote get-url origin > /dev/null 2>&1; then
    if git push origin "$BRANCH" --quiet 2>&1 | tail -2 >&2; then
        echo "[auto-commit] Pushed to origin/$BRANCH" >&2
        
        # v18.19.4: Concurrent deployment to NYC Droplet
        if [ "$BRANCH" == "feature/v10-rebuild" ]; then
            echo "[auto-commit] Launching concurrent NYC deployment..." >&2
            bash "$REPO_ROOT/deploy.sh" >&2
        fi
    fi
fi

exit 0
