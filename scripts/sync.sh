#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# scripts/sync.sh  —  Concurrent Sync: Local -> GitHub -> NYC Droplet
# ─────────────────────────────────────────────────────────────────────────────
# This script ensures strict version control and concurrent deployment.
# It performs:
#   1. Validation (Proof suite + Config)
#   2. Commit (if needed)
#   3. Push to GitHub (Source of Truth)
#   4. Deployment to NYC Droplet
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "Current branch: $BRANCH"

# ── 1. Validation ────────────────────────────────────────────────────────────
echo "Running validation..."
python3 scripts/validate.py
python3 -m pytest tests/proof/ -q --tb=short --no-header -p no:warnings

# ── 2. Check for uncommitted changes ─────────────────────────────────────────
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Uncommitted changes detected."
    read -p "Enter commit message: " COMMIT_MSG
    git add .
    git commit -m "$COMMIT_MSG"
fi

# ── 3. Push to GitHub ────────────────────────────────────────────────────────
echo "Pushing to GitHub..."
git push origin "$BRANCH"

# ── 4. Deploy to NYC Droplet ──────────────────────────────────────────────────
if [[ "$BRANCH" == "feature/v10-rebuild" || "$BRANCH" == "feature/v18.17-dag-rewrite" ]]; then
    echo "Launching concurrent NYC deployment..."
    bash deploy.sh
else
    echo "Skipping deployment for non-authoritative branch: $BRANCH"
fi

echo "✅ Sync complete."
