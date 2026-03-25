#!/bin/bash
# scripts/install_hooks.sh — Install git hooks for the algo trading system.
# Run once: bash scripts/install_hooks.sh

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

echo "Installing git hooks..."

# ── pre-commit: run validate.py before every commit ──────────────────────────
cat > "$HOOKS_DIR/pre-commit" << 'HOOK'
#!/bin/bash
# pre-commit hook: validate system config before committing
REPO_ROOT="$(git rev-parse --show-toplevel)"
echo ""
echo "Running pre-commit validation..."
python3 "$REPO_ROOT/scripts/validate.py"
STATUS=$?
if [ $STATUS -ne 0 ]; then
    echo ""
    echo "❌ Pre-commit validation failed. Fix errors before committing."
    echo "   To skip (dangerous): git commit --no-verify"
    exit 1
fi
echo "✅ Validation passed."
echo ""
exit 0
HOOK

chmod +x "$HOOKS_DIR/pre-commit"
echo "  ✅ pre-commit hook installed"

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

echo ""
echo "Done. Hooks installed at $HOOKS_DIR"
echo ""
echo "To test: python3 scripts/validate.py"
