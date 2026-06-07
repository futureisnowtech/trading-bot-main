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
RELEASE_AUDIT_SOAK_SECONDS="${RELEASE_AUDIT_SOAK_SECONDS:-600}"
APP_VERSION=$(python3 - <<'PYEOF'
from VERSION import VERSION
print(VERSION)
PYEOF
)
IMAGE_REPO="ghcr.io/$(git remote get-url origin | sed 's/.*github.com[:\/]\(.*\)\.git/\1/' | tr '[:upper:]' '[:lower:]')"
LOCAL_IMAGE_NAME="${IMAGE_REPO}"
LOCAL_DASHBOARD_IMAGE_NAME="${IMAGE_REPO}-dashboard"

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
export DASHBOARD_IMAGE_NAME="${LOCAL_DASHBOARD_IMAGE_NAME}"

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

if ! docker buildx version >/dev/null 2>&1; then
    echo "ERROR: docker buildx is required on the droplet for clean image builds."
    echo "       Install the buildx CLI plugin for user ${NYC_USER} before deploying."
    exit 1
fi

echo "  Building lean runtime image from the exact committed tree..."
docker buildx build --pull --load --progress=plain -t "${LOCAL_IMAGE_NAME}:latest" .

echo "  Building cockpit image..."
docker buildx build --pull --load --progress=plain -f Dockerfile.dashboard -t "${LOCAL_DASHBOARD_IMAGE_NAME}:latest" .

echo "  Seeding provisional release artifact for new SHA..."
docker run --rm -i -v ${PROJECT_DIR}:/app "${LOCAL_IMAGE_NAME}:latest" python3 - << PYEOF
from runtime.release_gate import VERDICT_BLOCKED, write_release_audit_artifact

payload = {
    "mode": "deploy_pending",
    "as_of": "${DEPLOY_UTC}",
    "audited_sha": "${LOCAL_SHA}",
    "verdict": VERDICT_BLOCKED,
    "entries_allowed": False,
    "last_successful_audit_at": "",
    "blockers": ["release_audit_pending_new_build"],
    "warnings": [],
    "details": {
        "build": {
            "app_version": "${APP_VERSION}",
            "sha": "${LOCAL_SHA}",
            "branch": "${BRANCH}",
            "deployed_at_utc": "${DEPLOY_UTC}",
        }
    },
}
write_release_audit_artifact(
    payload,
    markdown="# Release Audit\\n\\nPending hosted audit for the newly deployed SHA.\\n",
)
print("  provisional release artifact written.")
PYEOF

echo "  Hot-reloading services..."
docker compose up -d --remove-orphans --force-recreate --no-build

echo "  Waiting for containers..."
sleep 10
docker ps | grep execution-engine
docker ps | grep kalshi-cockpit

echo "  Verifying forecast lane readiness..."
VERIFY_OK=0
for attempt in \$(seq 1 18); do
if STATE_JSON=\$(python3 - << 'PYEOF'
import json
import sqlite3
import sys
from config import DB_PATH

try:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT lane_id, connected, health, readiness_state, blocked_reason
        FROM lane_runtime_state
        WHERE lane_id='forecast'
        """
    ).fetchone()
except Exception as exc:
    print(f"sqlite_error:{exc}")
    sys.exit(1)

if row is None:
    print("lane_state_missing")
    sys.exit(1)

payload = dict(row)
print(json.dumps(payload))

if int(payload.get("connected") or 0) != 1:
    sys.exit(1)
if payload.get("health") != "OK":
    sys.exit(1)
if payload.get("readiness_state") != "OPERATIONAL":
    sys.exit(1)
PYEOF
    ); then
        echo "  Forecast lane ready: \${STATE_JSON}"
        VERIFY_OK=1
        break
    fi
    echo "  Waiting for forecast lane readiness (\${attempt}/18)..."
    sleep 5
done

if [ "\${VERIFY_OK}" -ne 1 ]; then
    echo "ERROR: Forecast lane failed readiness verification."
    echo "Recent execution-engine logs:"
    docker logs --tail 120 execution-engine || true
    exit 1
fi

echo "  Verifying cockpit HTTP readiness..."
COCKPIT_OK=0
for attempt in \$(seq 1 18); do
    if python3 - << 'PYEOF'
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=5) as resp:
        body = resp.read().decode("utf-8").strip()
        if body == "ok":
            raise SystemExit(0)
except Exception:
    pass
raise SystemExit(1)
PYEOF
    then
        echo "  Cockpit ready on http://64.225.20.38:8501"
        COCKPIT_OK=1
        break
    fi
    echo "  Waiting for cockpit readiness (\${attempt}/18)..."
    sleep 5
done

if [ "\${COCKPIT_OK}" -ne 1 ]; then
    echo "ERROR: Cockpit failed readiness verification."
    echo "Recent kalshi-cockpit logs:"
    docker logs --tail 120 kalshi-cockpit || true
    exit 1
fi

echo "  Writing host service-status artifact..."
SERVICE_STATUS_B64=\$(docker ps --format '{{.Names}}|{{.Status}}' | base64 | tr -d '\n')
docker run --rm -i \
  -e SERVICE_STATUS_B64="\${SERVICE_STATUS_B64}" \
  -e SERVICE_STATUS_SHA="${LOCAL_SHA}" \
  -e SERVICE_STATUS_AS_OF="${DEPLOY_UTC}" \
  -v ${PROJECT_DIR}:/app "${LOCAL_IMAGE_NAME}:latest" python3 - << 'PYEOF'
import base64
import os

from runtime.release_gate import write_host_service_status_artifact

services = {
    "execution-engine": {"up": False, "status": ""},
    "kalshi-cockpit": {"up": False, "status": ""},
}

raw = os.environ.get("SERVICE_STATUS_B64", "").strip()
decoded = ""
if raw:
    decoded = base64.b64decode(raw.encode("utf-8")).decode("utf-8")

for line in decoded.splitlines():
    name, _sep, status = line.partition("|")
    if name in services:
        services[name] = {"up": status.startswith("Up"), "status": status}

payload = {
    "as_of": os.environ.get("SERVICE_STATUS_AS_OF", ""),
    "audited_sha": os.environ.get("SERVICE_STATUS_SHA", ""),
    "source": "host_docker_ps",
    "services": services,
    "all_up": all(bool(item.get("up")) for item in services.values()),
}

path = write_host_service_status_artifact(payload)
print(f"  host service status artifact written: {path}")
PYEOF

echo "  Writing provenance markers..."
cat > ${PROJECT_DIR}/version.txt << VTXT
app_version=${APP_VERSION}
sha=${LOCAL_SHA}
branch=${BRANCH}
deployed_at_utc=${DEPLOY_UTC}
VTXT

python3 - << PYEOF
import json
manifest = {
    "app_version": "${APP_VERSION}",
    "sha": "${LOCAL_SHA}",
    "branch": "${BRANCH}",
    "deployed_at_utc": "${DEPLOY_UTC}",
    "services": ["execution-engine", "kalshi-cockpit"],
    "cockpit_url": "http://64.225.20.38:8501",
}
with open("${PROJECT_DIR}/deploy_manifest.json", "w") as f:
    json.dump(manifest, f, indent=2)
print("  deploy_manifest.json written.")
PYEOF

docker exec -i kalshi-cockpit python3 - << PYEOF
import json
from pathlib import Path

runtime_dir = Path("/app/logs")
runtime_dir.mkdir(parents=True, exist_ok=True)

(runtime_dir / "version.txt").write_text(
    "app_version=${APP_VERSION}\\nsha=${LOCAL_SHA}\\nbranch=${BRANCH}\\ndeployed_at_utc=${DEPLOY_UTC}\\n",
    encoding="utf-8",
)
(runtime_dir / "deploy_manifest.json").write_text(
    json.dumps(
        {
            "app_version": "${APP_VERSION}",
            "sha": "${LOCAL_SHA}",
            "branch": "${BRANCH}",
            "deployed_at_utc": "${DEPLOY_UTC}",
            "services": ["execution-engine", "kalshi-cockpit"],
            "cockpit_url": "http://64.225.20.38:8501",
        },
        indent=2,
    ),
    encoding="utf-8",
)
print("  cockpit provenance mirrored to /app/logs")
PYEOF

echo "  Running hosted release audit (soak=${RELEASE_AUDIT_SOAK_SECONDS}s)..."
docker exec -i execution-engine sh -lc \
  "cd /app && python3 scripts/release_audit.py --remote-hosted --scan-limit 12 --soak-seconds ${RELEASE_AUDIT_SOAK_SECONDS}"

echo "  version.txt contents:"
cat ${PROJECT_DIR}/version.txt
REMOTE_EOF

echo ""
echo "Deployment complete."
echo "  SHA deployed : ${LOCAL_SHA}"
echo "  Branch       : ${BRANCH}"
echo "  Deploy UTC   : ${DEPLOY_UTC}"
echo "  Server       : ${NYC_USER}@${NYC_IP}:${PROJECT_DIR}"
echo "  Cockpit URL  : http://64.225.20.38:8501"
