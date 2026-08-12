#!/bin/bash
# -----------------------------------------------------------------------------
# refresh_host_service_status.sh — keep logs/host_service_status.json current
# -----------------------------------------------------------------------------
# The release audit runs *inside* execution-engine, where the Docker daemon is
# not reachable, so it falls back to this host-written artifact for service
# liveness — and rejects the artifact once it is older than
# HOST_SERVICE_ARTIFACT_MAX_AGE_SECONDS (30 minutes, scripts/release_audit.py).
#
# Writing it only at deploy time therefore closed the release gate 30 minutes
# after every deploy: the engine's own periodic audit reported
# `host_service_status_artifact_stale` and entries stopped. deploy.sh installs a
# cron entry that runs this script so the artifact stays continuously fresh.
#
# Runs on the droplet host (needs `docker ps`), as the deploy user, so the
# artifact keeps algo-runner ownership. Only stdlib imports are involved.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/algo-runner/bot}"
cd "${PROJECT_DIR}"

SERVICE_STATUS_SHA="$(sed -n 's/^sha=//p' version.txt 2>/dev/null | head -n 1 || true)"
SERVICE_STATUS_AS_OF="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SERVICE_STATUS_B64="$(docker ps --format '{{.Names}}|{{.Status}}' | base64 | tr -d '\n')"

export PYTHONDONTWRITEBYTECODE=1
export SERVICE_STATUS_SHA SERVICE_STATUS_AS_OF SERVICE_STATUS_B64

python3 - << 'PYEOF'
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
print(f"host service status artifact written: {path}")
PYEOF
