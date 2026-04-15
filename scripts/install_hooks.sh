#!/bin/bash
# scripts/install_hooks.sh — Install git hooks for the algo trading system.
# Run once: bash scripts/install_hooks.sh

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

echo "Installing git hooks..."

# ── pre-commit: repo truth gate (fast) + validate.py ─────────────────────────
cat > "$HOOKS_DIR/pre-commit" << 'HOOK'
#!/bin/bash
# pre-commit hook: truth gate (fast) + config validation before every commit
REPO_ROOT="$(git rev-parse --show-toplevel)"
echo ""
echo "Running pre-commit checks..."

# 1. Repo truth gate (fast) — catches Desktop paths, bad hook roots, live-start bypasses
python3 "$REPO_ROOT/scripts/repo_truth_gate.py" --fast
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Repo truth gate (fast) failed. Fix Desktop paths / policy issues before committing."
    echo "   To skip (dangerous): git commit --no-verify"
    exit 1
fi

# 2. Config validation
python3 "$REPO_ROOT/scripts/validate.py"
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Pre-commit validation failed. Fix errors before committing."
    echo "   To skip (dangerous): git commit --no-verify"
    exit 1
fi
echo "✅ Pre-commit checks passed."
echo ""
exit 0
HOOK

chmod +x "$HOOKS_DIR/pre-commit"
echo "  ✅ pre-commit hook installed (repo_truth_gate.py --fast + validate.py)"

# ── post-commit: auto-log to CHANGELOG.md ────────────────────────────────────
cat > "$HOOKS_DIR/post-commit" << 'HOOK'
#!/bin/bash
# post-commit: stamp version + commit hash into a version file for the dashboard
REPO_ROOT="$(git rev-parse --show-toplevel)"
COMMIT_HASH=$(git rev-parse --short HEAD)
COMMIT_MSG=$(git log -1 --pretty=%s)
TIMESTAMP=$(date '+%Y-%m-%d %H:%M')
VERSION_FILE="$REPO_ROOT/.version"
echo "$TIMESTAMP | $COMMIT_HASH | $COMMIT_MSG" > "$VERSION_FILE"
exit 0
HOOK

chmod +x "$HOOKS_DIR/post-commit"
echo "  ✅ post-commit hook installed (.version file updated on every commit)"

# ── pre-push: full gate (proof suite + validate + truth gate) ─────────────────
cat > "$HOOKS_DIR/pre-push" << 'HOOK'
#!/bin/bash
# pre-push hook: full verification gate before pushing
REPO_ROOT="$(git rev-parse --show-toplevel)"
echo ""
echo "Running pre-push gate..."

# 1. Proof suite
echo "  [1/3] Proof suite..."
python3 -m pytest "$REPO_ROOT/tests/proof/" -q --tb=short --no-header -p no:warnings
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Proof suite failed. Fix before pushing."
    echo "   To skip (dangerous): git push --no-verify"
    exit 1
fi

# 2. Config validation
echo "  [2/3] Config validation..."
python3 "$REPO_ROOT/scripts/validate.py"
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Config validation failed. Fix before pushing."
    exit 1
fi

# 3. Repo truth gate (strict)
echo "  [3/3] Repo truth gate (strict)..."
python3 "$REPO_ROOT/scripts/repo_truth_gate.py" --strict
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Repo truth gate failed. Fix Desktop paths / policy issues before pushing."
    exit 1
fi

echo "✅ Pre-push gate passed."
echo ""
exit 0
HOOK

chmod +x "$HOOKS_DIR/pre-push"
echo "  ✅ pre-push hook installed (proof suite + validate + truth gate --strict)"

echo ""
echo "Done. Hooks installed at $HOOKS_DIR"
echo ""
echo "To test hooks: bash .claude/hooks/test_hooks.sh"
echo "To run truth gate: python3 scripts/repo_truth_gate.py --fast"
