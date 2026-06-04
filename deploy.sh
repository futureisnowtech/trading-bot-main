#!/bin/bash
# -----------------------------------------------------------------------------
# deploy.sh — deploy the current committed SHA to the lean Kalshi runtime
# -----------------------------------------------------------------------------
set -euo pipefail

NYC_IP="64.225.20.38"
NYC_PORT="2222"
NYC_USER="algo-runner"
PROJECT_DIR="/home/${NYC_USER}/bot"
SSH_CMD="ssh -p ${NYC_PORT} -o StrictHostKeyChecking=no"
TMP_EXPORT_DIR=""

cleanup() {
    if [ -n "${TMP_EXPORT_DIR}" ] && [ -d "${TMP_EXPORT_DIR}" ]; then
        rm -rf "${TMP_EXPORT_DIR}"
    fi
}
trap cleanup EXIT

BRANCH=$(git branch --show-current || true)
if [ -z "${BRANCH}" ]; then
    BRANCH=$(git for-each-ref --format='%(refname:short)' refs/remotes/origin --contains HEAD | sed 's#^origin/##' | grep -v '^HEAD$' | head -n 1 || true)
fi
if [ -z "${BRANCH}" ]; then
    echo "ERROR: Unable to determine the origin branch for HEAD."
    echo "       Check out a branch or set GITHUB_REF_NAME before deploying."
    exit 1
fi

echo "Checking worktree cleanliness..."
if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: Worktree is dirty or has untracked files. Deploy only from an exact committed state."
    echo "       Run: git status"
    git status --short
    exit 1
fi
echo "  OK: worktree is clean."

echo "Fetching origin to verify SHA parity..."
git fetch origin "${BRANCH}" 2>&1

LOCAL_SHA=$(git rev-parse HEAD)
ORIGIN_SHA=$(git rev-parse "origin/${BRANCH}")

if [ "${LOCAL_SHA}" != "${ORIGIN_SHA}" ]; then
    echo "ERROR: Local HEAD (${LOCAL_SHA}) does not match origin/${BRANCH} (${ORIGIN_SHA})."
    echo "       Push your commits first: git push origin ${BRANCH}"
    exit 1
fi
echo "  OK: local HEAD == origin/${BRANCH} == ${LOCAL_SHA}"

DEPLOY_UTC=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
LOCAL_IMAGE_NAME="ghcr.io/$(git remote get-url origin | sed 's/.*github.com[:\/]\(.*\)\.git/\1/' | tr '[:upper:]' '[:lower:]')"

TMP_EXPORT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/kalshi-deploy.XXXXXX")

echo "Pruning remote cache artifacts that can block sync..."
${SSH_CMD} ${NYC_USER}@${NYC_IP} bash -s << REMOTE_PRUNE
set -euo pipefail
mkdir -p ${PROJECT_DIR}
docker run --rm -v ${PROJECT_DIR}:/workspace alpine:3.20 sh -lc \
  'find /workspace \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} +; find /workspace -name "*.pyc" -delete'
REMOTE_PRUNE

echo "Exporting exact committed tree for SHA ${LOCAL_SHA}..."
git archive --format=tar "${LOCAL_SHA}" | tar -xf - -C "${TMP_EXPORT_DIR}"
echo "  OK: committed tree exported to ${TMP_EXPORT_DIR}"

echo "Syncing exact committed tree to droplet (SHA: ${LOCAL_SHA})..."
rsync -avz \
    --delete \
    --force \
    -e "ssh -p ${NYC_PORT} -o StrictHostKeyChecking=no" \
    --exclude '.git/' \
    --exclude '.env' \
    --exclude 'kalshi_private_key*.pem' \
    --exclude 'logs' \
    --exclude 'version.txt' \
    --exclude 'deploy_manifest.json' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude '*.pyc' \
    "${TMP_EXPORT_DIR}/" "${NYC_USER}@${NYC_IP}:${PROJECT_DIR}/"

echo "Restarting lean Docker stack on droplet..."
${SSH_CMD} ${NYC_USER}@${NYC_IP} bash -s << REMOTE_EOF
set -euo pipefail
cd ${PROJECT_DIR}

export IMAGE_NAME="${LOCAL_IMAGE_NAME}"

if [ ! -f .env ]; then
    echo "ERROR: ${PROJECT_DIR}/.env is missing on the droplet."
    echo "       Restore the runtime env file before starting containers."
    exit 1
fi

if [ ! -f kalshi_private_key.pem ]; then
    echo "ERROR: ${PROJECT_DIR}/kalshi_private_key.pem is missing on the droplet."
    echo "       Restore the Kalshi signing key before starting containers."
    exit 1
fi

echo "  Attempting to pull latest images from GHCR..."
if ! docker compose pull; then
    echo "  WARNING: GHCR pull failed. Falling back to local build..."
    docker compose build
fi

echo "  Hot-reloading services..."
docker compose up -d --remove-orphans

echo "  Waiting for containers..."
sleep 15
docker ps | grep execution-engine
docker ps | grep telegram-oracle

echo "  Writing provenance markers..."
cat > ${PROJECT_DIR}/version.txt << VTXT
sha=${LOCAL_SHA}
branch=${BRANCH}
deployed_at_utc=${DEPLOY_UTC}
VTXT

python3 - << PYEOF
import json
manifest = {
    "sha": "${LOCAL_SHA}",
    "branch": "${BRANCH}",
    "deployed_at_utc": "${DEPLOY_UTC}",
    "services": ["execution-engine", "telegram-oracle"],
}
with open("${PROJECT_DIR}/deploy_manifest.json", "w") as f:
    json.dump(manifest, f, indent=2)
print("  deploy_manifest.json written.")
PYEOF

echo "  version.txt contents:"
cat ${PROJECT_DIR}/version.txt
REMOTE_EOF

echo ""
echo "Deployment complete."
echo "  SHA deployed : ${LOCAL_SHA}"
echo "  Branch       : ${BRANCH}"
echo "  Deploy UTC   : ${DEPLOY_UTC}"
echo "  Server       : ${NYC_USER}@${NYC_IP}:${PROJECT_DIR}"
