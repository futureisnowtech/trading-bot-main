"""Resolve runtime build metadata for operator-facing surfaces."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from VERSION import VERSION as SOURCE_VERSION
from config import DB_PATH, REPO_ROOT

_ROOT = Path(REPO_ROOT).resolve()
_RUNTIME_DIR = Path(DB_PATH).resolve().parent


def _read_git_value(*args: str) -> str:
    git_dir = _ROOT / ".git"
    if not git_dir.exists():
        return ""
    try:
        return subprocess.check_output(
            ["git", "-C", str(_ROOT), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return ""


def load_deploy_metadata() -> dict[str, Any]:
    manifest_candidates = [
        _RUNTIME_DIR / "deploy_manifest.json",
        _ROOT / "deploy_manifest.json",
    ]
    version_candidates = [
        _RUNTIME_DIR / "version.txt",
        _ROOT / "version.txt",
    ]
    payload: dict[str, Any] = {}

    for manifest_path in manifest_candidates:
        if not manifest_path.exists():
            continue
        try:
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                payload.update(parsed)
                break
        except Exception:
            continue

    for version_path in version_candidates:
        if not version_path.exists():
            continue
        try:
            for line in version_path.read_text(encoding="utf-8").splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                payload.setdefault(key.strip(), value.strip())
            break
        except Exception:
            continue

    return payload


def get_build_info() -> dict[str, Any]:
    metadata = load_deploy_metadata()
    metadata_sha = str(metadata.get("sha") or "").strip()
    git_sha = _read_git_value("rev-parse", "HEAD")
    git_branch = _read_git_value("branch", "--show-current")
    metadata_stale = bool(git_sha and metadata_sha and metadata_sha != git_sha)
    app_version = SOURCE_VERSION if metadata_stale else str(
        metadata.get("app_version")
        or metadata.get("version")
        or SOURCE_VERSION
    ).strip()
    sha = git_sha or metadata_sha
    branch = git_branch or str(metadata.get("branch") or "").strip()
    deployed_at_utc = "" if metadata_stale else str(metadata.get("deployed_at_utc") or "").strip()
    cockpit_url = "" if metadata_stale else str(metadata.get("cockpit_url") or "").strip()

    return {
        **metadata,
        "app_version": app_version or SOURCE_VERSION,
        "version": app_version or SOURCE_VERSION,
        "sha": sha,
        "short_sha": sha[:7] if sha else "",
        "branch": branch,
        "deployed_at_utc": deployed_at_utc,
        "cockpit_url": cockpit_url,
        "metadata_stale": metadata_stale,
    }
