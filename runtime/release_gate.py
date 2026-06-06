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

