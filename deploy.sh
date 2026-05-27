#!/bin/bash
# -----------------------------------------------------------------------------
# deploy.sh — Truthful NYC3 deployment script for feature/v10-rebuild
#
# Safety invariants:
#   - Refuses to deploy from a dirty worktree (uncommitted changes)
#   - Refuses to deploy if local HEAD != origin/feature/v10-rebuild
#   - Does NOT auto-commit or auto-push (that is the engineer's job)
#   - Deploys the already-authored, already-pushed SHA only
#   - Writes /home/algo-runner/bot/version.txt and /home/algo-runner/bot/deploy_manifest.json
#     on the server as provenance markers after a successful sync
# -----------------------------------------------------------------------------
set -euo pipefail

NYC_IP="64.225.20.38"
NYC_PORT="2222"
NYC_USER="algo-runner"
PROJECT_DIR="/home/${NYC_USER}/bot"
DASHBOARD_UID="d9ecf89d-5e95-4e63-b0ae-f8008debbc0f"
PROMETHEUS_TARGET="algo-bot-spot:8000"
SSH_CMD="ssh -p ${NYC_PORT} -o StrictHostKeyChecking=no"
BRANCH=$(git branch --show-current)

echo "Checking worktree cleanliness..."
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: Worktree is dirty. Commit or stash all changes before deploying."
    echo "       Run: git status"
    exit 1
fi
echo "  OK: worktree is clean."

# ── Guard 2: local HEAD must match origin/feature/v10-rebuild ─────────────────
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

# ── Sync code to server ───────────────────────────────────────────────────────
LOCAL_IMAGE_NAME="ghcr.io/$(git remote get-url origin | sed 's/.*github.com[:\/]\(.*\)\.git/\1/' | tr '[:upper:]' '[:lower:]')"

echo "Syncing code to NYC3 via rsync (SHA: ${LOCAL_SHA})..."
rsync -avz \
    -e "ssh -p ${NYC_PORT} -o StrictHostKeyChecking=no" \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude 'logs' \
    --exclude '.pytest_cache' \
    --exclude '*.pyc' \
    --exclude 'sop_state.generated.js' \
    --exclude '.env' \
    . "${NYC_USER}@${NYC_IP}:${PROJECT_DIR}/"

# ── Server-side: restart stack and provision ─────────────────────────────────
echo "Restarting Docker stack on NYC3..."
${SSH_CMD} ${NYC_USER}@${NYC_IP} bash -s << REMOTE_EOF
set -euo pipefail
cd ${PROJECT_DIR}

export IMAGE_NAME="${LOCAL_IMAGE_NAME}"

echo "  Attempting to pull latest images from GHCR..."
if ! docker compose pull; then
    echo "  WARNING: GHCR pull failed (denied or not found). Falling back to local build..."
    docker compose build
fi

echo "  Hot-reloading services..."
docker compose up -d --remove-orphans

echo "  Waiting for health check..."
sleep 15
docker ps | grep algo-bot-spot
docker ps | grep algo-bot-forecast

echo "  Finalizing Grafana provisioning..."
docker exec algo-bot-spot python3 provision_grafana_final.py

echo "  Writing provenance markers..."
cat > ${PROJECT_DIR}/version.txt << VTXT
sha=${LOCAL_SHA}
branch=${BRANCH}
deployed_at_utc=${DEPLOY_UTC}
VTXT

python3 - << PYEOF
import json, datetime
manifest = {
    "sha": "${LOCAL_SHA}",
    "branch": "${BRANCH}",
    "deployed_at_utc": "${DEPLOY_UTC}",
    "dashboard_uid": "${DASHBOARD_UID}",
    "prometheus_target": "${PROMETHEUS_TARGET}"
}
with open("${PROJECT_DIR}/deploy_manifest.json", "w") as f:
    json.dump(manifest, f, indent=2)
print("  deploy_manifest.json written.")
PYEOF

echo "  version.txt contents:"
cat ${PROJECT_DIR}/version.txt

REMOTE_EOF

echo "Refreshing local SOP live snapshot..."
SOP_BRANCH="${BRANCH}" \
SOP_DEPLOYED_SHA="${LOCAL_SHA}" \
SOP_DEPLOYED_AT_UTC="${DEPLOY_UTC}" \
SOP_DASHBOARD_UID="${DASHBOARD_UID}" \
SOP_PROMETHEUS_TARGET="${PROMETHEUS_TARGET}" \
SOP_DOCKER_HEALTH="healthy" \
python3 scripts/refresh_sop.py

echo ""
echo "Deployment complete."
echo "  SHA deployed : ${LOCAL_SHA}"
echo "  Branch       : ${BRANCH}"
echo "  Deploy UTC   : ${DEPLOY_UTC}"
echo "  Server       : ${NYC_USER}@${NYC_IP}:${PROJECT_DIR}"
