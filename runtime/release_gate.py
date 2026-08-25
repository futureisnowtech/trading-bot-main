"""Shared release-gate helpers for audit artifacts and blocker classification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import RUNTIME_ROOT

VERDICT_BLOCKED = "BLOCKED"
VERDICT_PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
VERDICT_READY_FOR_LIVE = "READY_FOR_LIVE"
PASSING_VERDICTS = {
    VERDICT_PASS_WITH_WARNINGS,
    VERDICT_READY_FOR_LIVE,
}

_INFRASTRUCTURE_PREFIXES: tuple[str, ...] = (
    "balance_truth_mismatch",
    "broker_disconnected",
    "deploy_runtime",
    "feature_computation_failed",
    "get_account_balance_failed",
    "get_positions_failed",
    "missing_live_ask",
    "missing_quotes",
    "missing_weather_data",
    "provider_mode_unknown",
    "quote_ingestion_failure",
    "release_audit",
    "remote_sha_mismatch",
    "scan_no_markets_scored",
    "stale_ensemble_data",
    "stale_weather_model_data",
    "stale_market_data",
    "stale_runtime_heartbeat",
    "storage_headroom",
    "sync_positions_failed",
    "telegram_model_probe_failed",
    "unresolved_critical_incidents",
    "weather_provider_unavailable",
)

_LIQUIDITY_WARNING_PREFIXES: tuple[str, ...] = (
    "depth_capped",
    "fill_or_kill_insufficient_resting_volume",
    "insufficient_resting_volume",
)


def get_release_artifact_paths() -> dict[str, Path]:
    root = Path(RUNTIME_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return {
        "json": root / "release_audit.json",
        "markdown": root / "release_audit.md",
        "verdict": root / "release_verdict.txt",
    }


def get_host_service_status_artifact_path() -> Path:
    root = Path(RUNTIME_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root / "host_service_status.json"


def load_release_audit_artifact() -> dict[str, Any]:
    path = get_release_artifact_paths()["json"]
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def write_release_audit_artifact(
    payload: dict[str, Any],
    *,
    markdown: str = "",
) -> dict[str, str]:
    paths = get_release_artifact_paths()
    paths["json"].write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    paths["markdown"].write_text(markdown or "", encoding="utf-8")

    verdict = str(payload.get("verdict") or VERDICT_BLOCKED)
    entries_allowed = "YES" if bool(payload.get("entries_allowed")) else "NO"
    sha = str(payload.get("audited_sha") or "").strip()
    line = f"{verdict}\nentries_allowed={entries_allowed}\naudited_sha={sha}\n"
    paths["verdict"].write_text(line, encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def load_host_service_status_artifact() -> dict[str, Any]:
    path = get_host_service_status_artifact_path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def write_host_service_status_artifact(payload: dict[str, Any]) -> str:
    path = get_host_service_status_artifact_path()
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return str(path)


def build_deploy_pending_artifact(
    *,
    prior_release: dict[str, Any] | None,
    audited_sha: str,
    app_version: str,
    branch: str,
    deployed_at_utc: str,
) -> dict[str, Any]:
    """Build the temporary artifact written between container restart and hosted audit.

    If the currently running release is already passing, preserve that tradable state
    during the short deploy window and surface the new-build condition as a warning.
    If the currently running release is already blocked, keep it blocked.
    """
    prior = prior_release if isinstance(prior_release, dict) else {}
    prior_verdict = str(
        prior.get("verdict") or prior.get("current_release_verdict") or ""
    ).strip()
    prior_entries_allowed = bool(prior.get("entries_allowed"))
    prior_details = prior.get("details") if isinstance(prior.get("details"), dict) else {}
    prior_last_success = str(
        prior.get("last_successful_audit_at") or prior.get("as_of") or ""
    ).strip()

    passing = prior_entries_allowed and prior_verdict in PASSING_VERDICTS

    payload: dict[str, Any] = {
        "mode": "deploy_pending",
        "as_of": deployed_at_utc,
        "audited_sha": str(audited_sha or "").strip(),
        "verdict": VERDICT_BLOCKED,
        "entries_allowed": False,
        "last_successful_audit_at": "",
        "blockers": ["release_audit_pending_new_build"],
        "warnings": [],
        "details": {
            "build": {
                "app_version": str(app_version or ""),
                "sha": str(audited_sha or "").strip(),
                "branch": str(branch or ""),
                "deployed_at_utc": str(deployed_at_utc or ""),
            },
            "prior_release": {
                "audited_sha": str(prior.get("audited_sha") or "").strip(),
                "verdict": prior_verdict,
                "as_of": str(prior.get("as_of") or ""),
            },
        },
    }

    if passing:
        payload["verdict"] = VERDICT_PASS_WITH_WARNINGS
        payload["entries_allowed"] = True
        payload["last_successful_audit_at"] = prior_last_success
        payload["blockers"] = []
        payload["warnings"] = ["release_audit_pending_new_build"]
        for key in ("live_truth", "provider_status", "balance_truth"):
            value = prior_details.get(key)
            if isinstance(value, dict) and value:
                payload["details"][key] = value

    return payload


def is_infrastructure_reason(reason: str) -> bool:
    token = str(reason or "").strip().lower()
    if not token:
        return False
    return token.startswith(_INFRASTRUCTURE_PREFIXES)


def is_liquidity_warning(reason: str) -> bool:
    token = str(reason or "").strip().lower()
    if not token:
        return False
    return token.startswith(_LIQUIDITY_WARNING_PREFIXES)
