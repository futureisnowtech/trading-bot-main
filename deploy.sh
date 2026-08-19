#!/bin/bash
# -----------------------------------------------------------------------------
# deploy.sh — deploy the current committed SHA to the lean Kalshi runtime
# -----------------------------------------------------------------------------
set -euo pipefail

NYC_IP="157.245.15.40"
NYC_PORT="2222"
NYC_USER="algo-runner"
PROJECT_DIR="/home/${NYC_USER}/bot"
# ServerAliveInterval keeps the session alive through the long silent steps. The
# hosted release audit soaks for 360s without writing anything, which is long
# enough for an idle NAT/firewall hop to drop the connection: the deploy then
# reports "client_loop: send disconnect: Broken pipe" and fails *after* the bot
# is already up and healthy, skipping its own verification step.
SSH_CMD="ssh -p ${NYC_PORT} -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ServerAliveCountMax=20"
TMP_EXPORT_DIR=""

cleanup() {
    if [ -n "${TMP_EXPORT_DIR}" ] && [ -d "${TMP_EXPORT_DIR}" ]; then
        rm -rf "${TMP_EXPORT_DIR}"
    fi
}
trap cleanup EXIT

remote_ownership_report() {
    ${SSH_CMD} ${NYC_USER}@${NYC_IP} bash -s << REMOTE_OWNERSHIP
set -euo pipefail
if [ ! -d ${PROJECT_DIR} ]; then
    exit 0
fi
find ${PROJECT_DIR} \
  -path ${PROJECT_DIR}/.git -prune -o \
  -not -user ${NYC_USER} -printf '%u:%g %m %p\n'
REMOTE_OWNERSHIP
}

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
# The soak sleeps, then re-collects runtime state so the release gate judges a
# container that has been running, not one that merely booted.
#
# The floor is one full execution cycle. Production runs execution_daemon.py,
# whose loop sleeps SNIPER_SLEEP_SECONDS (default 300 s, unset on the droplet).
# That loop is the real cadence: it runs the cycle, and run_position_monitor at
# the end of it writes the lane heartbeat the audit checks. The quote-harvester
# sweep takes ~104 s after a restart and fits inside the same window.
#
# Do NOT reason from the schedule.every(...) jobs in forecast.runner
# (_bg_discovery 5 m, strategy 2 m, position monitor 30 s). They live in
# start_forecast_lane, which only runs when forecast/runner.py is executed
# directly -- production never calls it.
#
# 360 s covers one 300 s cycle plus a minute of slack. Below 300 s the soak can
# return before any cycle has completed, and degrades into a slow restart check.
RELEASE_AUDIT_SOAK_SECONDS="${RELEASE_AUDIT_SOAK_SECONDS:-360}"
# Printed as the very last remote statement and checked locally, so a remote
# block that dies early can never be reported as a successful deploy.
REMOTE_COMPLETION_SENTINEL="__REMOTE_DEPLOY_COMPLETE__"
APP_VERSION=$(python3 - <<'PYEOF'
from VERSION import VERSION
print(VERSION)
PYEOF
)
# Resolve owner/repo for the ghcr tag. The old expression required a trailing
# ".git", which only the SSH remote carries -- so a laptop deploy worked while
# the Actions runner (which checks out "https://github.com/owner/repo", no
# suffix) passed the whole URL through and produced the invalid tag
# "ghcr.io/https://github.com/owner/repo". Prefer GITHUB_REPOSITORY when the
# runner sets it, and make the fallback tolerate both remote forms.
REPO_SLUG="${GITHUB_REPOSITORY:-$(git remote get-url origin \
    | sed -E 's#^(https?://|git@)github\.com[:/]##; s#\.git$##')}"
IMAGE_REPO="ghcr.io/$(printf '%s' "${REPO_SLUG}" | tr '[:upper:]' '[:lower:]')"
LOCAL_IMAGE_NAME="${IMAGE_REPO}"
LOCAL_DASHBOARD_IMAGE_NAME="${IMAGE_REPO}-dashboard"

TMP_EXPORT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/kalshi-deploy.XXXXXX")

echo "Auditing remote ownership..."
REMOTE_OWNERSHIP_DRIFT="$(remote_ownership_report)"
if [ -n "${REMOTE_OWNERSHIP_DRIFT}" ]; then
    echo "ERROR: Remote tree contains files not owned by ${NYC_USER}."
    echo "       Repair ownership before deploying so the blessed path stays exact."
    printf '%s\n' "${REMOTE_OWNERSHIP_DRIFT}" | head -n 40
    exit 1
fi
echo "  OK: remote tree ownership is clean."

echo "Pruning remote cache artifacts that can block sync..."
${SSH_CMD} ${NYC_USER}@${NYC_IP} bash -s << REMOTE_PRUNE
set -euo pipefail
mkdir -p ${PROJECT_DIR}
REMOTE_UID="\$(id -u)"
REMOTE_GID="\$(id -g)"
docker run --rm -u "\${REMOTE_UID}:\${REMOTE_GID}" -v ${PROJECT_DIR}:/workspace alpine:3.20 sh -lc \
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
REMOTE_LOG="${TMP_EXPORT_DIR}/remote_deploy.log"
${SSH_CMD} ${NYC_USER}@${NYC_IP} bash -s << REMOTE_EOF | tee "${REMOTE_LOG}"
set -euo pipefail
cd ${PROJECT_DIR}

export IMAGE_NAME="${LOCAL_IMAGE_NAME}"
export DASHBOARD_IMAGE_NAME="${LOCAL_DASHBOARD_IMAGE_NAME}"
export ALGO_UID="\$(id -u)"
export ALGO_GID="\$(id -g)"

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
docker buildx build --pull --load --progress=plain \
  --build-arg BUILD_SHA="${LOCAL_SHA}" \
  -t "${LOCAL_IMAGE_NAME}:latest" .

echo "  Building cockpit image..."
docker buildx build --pull --load --progress=plain \
  --build-arg BUILD_SHA="${LOCAL_SHA}" \
  -f Dockerfile.dashboard \
  -t "${LOCAL_DASHBOARD_IMAGE_NAME}:latest" .

echo "  Capturing current live release state before restart..."
PRE_DEPLOY_RELEASE_JSON=""
if docker ps --format '{{.Names}}' | grep -qx 'execution-engine'; then
  PRE_DEPLOY_RELEASE_JSON="$(docker exec execution-engine sh -lc \
    'cd /app && python3 scripts/release_audit.py --remote-hosted --scan-limit 12 --soak-seconds 0 --format json' \
    </dev/null 2>/dev/null || true)"
fi
PRE_DEPLOY_RELEASE_B64="$(printf '%s' "\${PRE_DEPLOY_RELEASE_JSON}" | base64 | tr -d '\n')"

echo "  Seeding provisional release artifact for new SHA..."
docker run --rm -i \
  --user "\${ALGO_UID}:\${ALGO_GID}" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PRE_DEPLOY_RELEASE_B64="\${PRE_DEPLOY_RELEASE_B64}" \
  -v ${PROJECT_DIR}:/app "${LOCAL_IMAGE_NAME}:latest" python3 - << PYEOF
import base64
import json
import os

from runtime.release_gate import (
    build_deploy_pending_artifact,
    load_release_audit_artifact,
    write_release_audit_artifact,
)

prior_release = load_release_audit_artifact()
raw = os.environ.get("PRE_DEPLOY_RELEASE_B64", "").strip()
if raw:
    try:
        decoded = base64.b64decode(raw.encode("utf-8")).decode("utf-8").strip()
        parsed = json.loads(decoded)
        if isinstance(parsed, dict):
            prior_release = parsed
    except Exception:
        pass

payload = build_deploy_pending_artifact(
    prior_release=prior_release,
    audited_sha="${LOCAL_SHA}",
    app_version="${APP_VERSION}",
    branch="${BRANCH}",
    deployed_at_utc="${DEPLOY_UTC}",
)
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
        echo "  Cockpit ready on http://157.245.15.40:8501"
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

echo "  Writing provenance markers..."
mkdir -p ${PROJECT_DIR}/logs

cat > ${PROJECT_DIR}/version.txt << VTXT
app_version=${APP_VERSION}
sha=${LOCAL_SHA}
build_sha=${LOCAL_SHA}
branch=${BRANCH}
deployed_at_utc=${DEPLOY_UTC}
VTXT

cat > ${PROJECT_DIR}/logs/version.txt << VTXT
app_version=${APP_VERSION}
sha=${LOCAL_SHA}
build_sha=${LOCAL_SHA}
branch=${BRANCH}
deployed_at_utc=${DEPLOY_UTC}
VTXT

python3 - << PYEOF
import json
from pathlib import Path

manifest = {
    "app_version": "${APP_VERSION}",
    "sha": "${LOCAL_SHA}",
    "build_sha": "${LOCAL_SHA}",
    "branch": "${BRANCH}",
    "deployed_at_utc": "${DEPLOY_UTC}",
    "services": ["execution-engine", "kalshi-cockpit"],
    "cockpit_url": "http://157.245.15.40:8501",
}
for target in (
    Path("${PROJECT_DIR}/deploy_manifest.json"),
    Path("${PROJECT_DIR}/logs/deploy_manifest.json"),
):
    target.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print("  deploy_manifest.json written to project root and logs/")
PYEOF

echo "  Writing host service-status artifact..."
chmod +x ${PROJECT_DIR}/scripts/refresh_host_service_status.sh
PROJECT_DIR=${PROJECT_DIR} ${PROJECT_DIR}/scripts/refresh_host_service_status.sh

# The in-container audit rejects this artifact once it is older than 30 minutes,
# so writing it only here closed the release gate 30 minutes after every deploy.
# Keep a host-side timer refreshing it. Installed idempotently on every deploy.
echo "  Ensuring host service-status refresh cron is installed..."
CRON_LINE="*/5 * * * * PROJECT_DIR=${PROJECT_DIR} ${PROJECT_DIR}/scripts/refresh_host_service_status.sh >> ${PROJECT_DIR}/logs/host_service_status_cron.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'refresh_host_service_status.sh' || true; echo "\${CRON_LINE}" ) | crontab -
crontab -l | grep -F 'refresh_host_service_status.sh'

# The watchdog is the only thing that will notice a silent failure while nobody
# is watching -- entries stopping, a flag on but inert, an order orphaned on the
# book. Installed the same idempotent way, so a rebuilt droplet gets it back and
# a redeploy never ends up with two copies.
echo "  Ensuring watchdog cron is installed..."
WATCHDOG_CRON="*/15 * * * * /usr/bin/docker exec execution-engine python3 /app/scripts/watchdog.py >> ${PROJECT_DIR}/logs/watchdog_cron.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'scripts/watchdog.py' || true; echo "\${WATCHDOG_CRON}" ) | crontab -
crontab -l | grep -F 'scripts/watchdog.py'

# NOTE: every docker exec below MUST redirect stdin from /dev/null. This whole
# block is piped into a remote `bash -s`, so an interactive-attached container
# consumes the rest of this script as its own stdin -- bash then hits EOF and
# exits 0, silently skipping every remaining step. That is how the soak audit,
# the ownership re-audit, and the provenance echo were once bypassed on a deploy
# that still reported success.
echo "  Running immediate hosted release audit (advisory)..."
if ! docker exec execution-engine sh -lc \
  "cd /app && python3 scripts/release_audit.py --remote-hosted --scan-limit 12 --soak-seconds 0 --no-persist" \
  </dev/null
then
  echo "  Immediate audit still warming up; keeping deploy-pending gate until the soak audit settles."
fi

echo "  Running hosted release audit (soak=${RELEASE_AUDIT_SOAK_SECONDS}s)..."
docker exec execution-engine sh -lc \
  "cd /app && python3 scripts/release_audit.py --remote-hosted --scan-limit 12 --soak-seconds ${RELEASE_AUDIT_SOAK_SECONDS}" \
  </dev/null

echo "  Auditing remote ownership after deploy..."
REMOTE_OWNERSHIP_DRIFT="\$(find ${PROJECT_DIR} \
  -path ${PROJECT_DIR}/.git -prune -o \
  -not -user ${NYC_USER} -printf '%u:%g %m %p\n')"
if [ -n "\${REMOTE_OWNERSHIP_DRIFT}" ]; then
    echo "ERROR: Deploy introduced non-${NYC_USER} ownership drift."
    printf '%s\n' "\${REMOTE_OWNERSHIP_DRIFT}" | head -n 40
    exit 1
fi

echo "  version.txt contents:"
cat ${PROJECT_DIR}/version.txt
echo "${REMOTE_COMPLETION_SENTINEL}"
REMOTE_EOF

if ! grep -q "^${REMOTE_COMPLETION_SENTINEL}\$" "${REMOTE_LOG}"; then
    echo "ERROR: The remote deploy block exited before reaching its final step."
    echo "       Every step after the last line above was skipped, so the release"
    echo "       audits and ownership checks did NOT run. Treat this deploy as"
    echo "       unverified and re-run it."
    exit 1
fi

echo ""
echo "Deployment complete."
echo "  SHA deployed : ${LOCAL_SHA}"
echo "  Branch       : ${BRANCH}"
echo "  Deploy UTC   : ${DEPLOY_UTC}"
echo "  Server       : ${NYC_USER}@${NYC_IP}:${PROJECT_DIR}"
echo "  Cockpit URL  : http://157.245.15.40:8501"
