#!/usr/bin/env python3
"""Generate the live-state sidecar for SOP.html.

Run anytime:
  python3 scripts/refresh_sop.py

This writes an untracked sibling file, sop_state.generated.js, so the
file:// SOP view can auto-refresh after deploys without mutating tracked HTML.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTFILE = ROOT / "sop_state.generated.js"
BRANCH = "feature/v10-rebuild"
REMOTE_HOST = "root@64.225.20.38"
REMOTE_PORT = "2222"
REMOTE_DIR = "/root/bot"
FALLBACK_UID = "d9ecf89d-5e95-4e63-b0ae-f8008debbc0f"
FALLBACK_PROM_TARGET = "algo-bot-live:8000"


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        check=check,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _extract_version() -> str:
    patterns = (
        re.compile(r"Canonical version:\s*(v[0-9]+(?:\.[0-9]+)+)"),
        re.compile(r"Current version:\s*(v[0-9]+(?:\.[0-9]+)+)"),
    )
    for candidate in (ROOT / "AGENTS.md", ROOT / "GEMINI.md"):
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return match.group(1)
    return "v18.16"


def _git_head() -> str:
    return _run(["git", "rev-parse", "HEAD"]).stdout.strip()


def _git_branch() -> str:
    return _run(["git", "branch", "--show-current"]).stdout.strip() or BRANCH


def _git_github_sha() -> str:
    result = _run(["git", "rev-parse", f"origin/{BRANCH}"], check=False)
    return result.stdout.strip()


def _worktree_dirty() -> bool:
    return bool(_run(["git", "status", "--porcelain"]).stdout.strip())


def _remote_snapshot() -> tuple[dict, str | None]:
    remote_python = """cd /root/bot && python3 - <<'PY'
import json
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.getcwd())

data = {
    "version": {},
    "manifest": {},
    "docker_health": "unknown",
    "runtime_mode": None,
    "launch_readiness": None,
    "global_status": None,
    "latest_health": {},
    "error": None,
}

try:
    from runtime.runtime_state import get_lane_state, get_system_state
    try:
        from config import DB_PATH
        db_path = str(DB_PATH)
    except Exception:
        db_path = os.path.join(os.getcwd(), "logs", "trades.db")

    sys_state = get_system_state() or {}
    crypto = get_lane_state("crypto") or {}
    data["runtime_mode"] = sys_state.get("process_mode")
    data["launch_readiness"] = (
        crypto.get("readiness_state")
        or sys_state.get("launch_readiness_state")
    )
    data["global_status"] = sys_state.get("global_status")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT ts, level, message FROM system_events "
            "WHERE source='health_check' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row:
            data["latest_health"] = dict(row)
except Exception as exc:
    data["error"] = f"runtime snapshot failed: {exc}"

for path_name, key in (("version.txt", "version"), ("deploy_manifest.json", "manifest")):
    try:
        if path_name.endswith(".json"):
            with open(path_name, "r", encoding="utf-8") as handle:
                data[key] = json.load(handle)
        else:
            with open(path_name, "r", encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if "=" in raw:
                        name, value = raw.split("=", 1)
                        data[key][name] = value
    except Exception as exc:
        data["error"] = "; ".join(filter(None, [data["error"], f"{path_name}: {exc}"]))

for docker_format in ("{{.State.Health.Status}}", "{{.State.Status}}"):
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", docker_format, "algo-bot-live"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data["docker_health"] = proc.stdout.strip()
            break
    except Exception as exc:
        data["error"] = "; ".join(filter(None, [data["error"], f"docker: {exc}"]))

print(json.dumps(data))
PY"""

    remote_cmd = f"bash -lc {shlex.quote(remote_python)}"
    try:
        result = _run(
            [
                "ssh",
                "-p",
                REMOTE_PORT,
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                REMOTE_HOST,
                remote_cmd,
            ],
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {}, "ssh snapshot timed out"
    if result.returncode != 0:
        return {}, (result.stderr or result.stdout or "ssh failed").strip()
    try:
        return json.loads(result.stdout.strip().splitlines()[-1]), None
    except Exception as exc:  # pragma: no cover - defensive parse fallback
        return {}, f"snapshot parse failed: {exc}"


def main() -> int:
    env_branch = os.getenv("SOP_BRANCH")
    env_deployed_sha = os.getenv("SOP_DEPLOYED_SHA")
    env_deployed_at = os.getenv("SOP_DEPLOYED_AT_UTC")
    env_dashboard_uid = os.getenv("SOP_DASHBOARD_UID")
    env_prom_target = os.getenv("SOP_PROMETHEUS_TARGET")
    env_docker_health = os.getenv("SOP_DOCKER_HEALTH")

    remote, remote_error = _remote_snapshot()
    manifest = remote.get("manifest") or {}
    version_txt = remote.get("version") or {}
    latest_health = remote.get("latest_health") or {}

    payload = {
        "generated_at_local": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "canonical_version": _extract_version(),
        "branch": env_branch or _git_branch(),
        "local_head_sha": _git_head(),
        "github_sha": _git_github_sha(),
        "worktree_dirty": _worktree_dirty(),
        "deployed_sha": env_deployed_sha or version_txt.get("sha") or manifest.get("sha"),
        "deployed_at_utc": env_deployed_at or version_txt.get("deployed_at_utc") or manifest.get("deployed_at_utc"),
        "dashboard_uid": env_dashboard_uid or manifest.get("dashboard_uid") or FALLBACK_UID,
        "prometheus_target": env_prom_target or manifest.get("prometheus_target") or FALLBACK_PROM_TARGET,
        "docker_health": env_docker_health or remote.get("docker_health"),
        "runtime_mode": remote.get("runtime_mode"),
        "launch_readiness": remote.get("launch_readiness"),
        "global_status": remote.get("global_status"),
        "live_posture_status": remote.get("live_posture_status") or "AMBER",
        "live_posture_primary": remote.get("live_posture_primary") or "constrained_live_only",
        "latest_health_level": latest_health.get("level"),
        "latest_health_message": latest_health.get("message"),
        "refresh_error": remote_error or remote.get("error"),
    }

    OUTFILE.write_text(
        "window.__SOP_STATE__ = "
        + json.dumps(payload, indent=2, sort_keys=True)
        + ";\n",
        encoding="utf-8",
    )

    print(
        "SOP snapshot refreshed — "
        f"local={payload['local_head_sha'][:12]} "
        f"github={(payload['github_sha'] or 'unknown')[:12]} "
        f"deployed={(payload['deployed_sha'] or 'unknown')[:12]} "
        f"runtime={payload['runtime_mode'] or 'unknown'} "
        f"posture={payload['live_posture_status'] or 'unknown'}"
    )
    if payload["refresh_error"]:
        print(f"Note: {payload['refresh_error']}")
    print(f"Wrote {OUTFILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
