#!/usr/bin/env python3
"""Canonical release gate for the Sovereign Kalshi Weather Engine."""

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import ACCOUNT_SIZE, DB_PATH, REPO_ROOT
from execution.kalshi_execution_controller import KalshiExecutionController, TradeIntent
from forecast.db import get_active_contracts, get_bars, get_open_forecast_positions
from forecast.market_snapshot import build_market_snapshots
from forecast.quote_harvester import get_paired_quotes
from forecast.strategy_engine import _get_macro_context, evaluate_market_snapshots
from monitoring.health_check import run_health_check
from notifications.ai_agent import probe_reasoning_model
from runtime.build_info import get_build_info
from runtime.incident_tracker import (
    get_incident_summary,
    get_open_incidents,
    ingest_system_events,
    init_incident_table,
)
from runtime.operator_truth import (
    get_balance_truth_status,
    get_live_kalshi_status,
    get_release_status,
    get_weather_provider_status,
)
from runtime.release_gate import (
    VERDICT_BLOCKED,
    VERDICT_PASS_WITH_WARNINGS,
    VERDICT_READY_FOR_LIVE,
    is_infrastructure_reason,
    is_liquidity_warning,
    write_release_audit_artifact,
)
from runtime.storage_guard import runtime_storage_status

REMOTE_HOST = "64.225.20.38"
REMOTE_PORT = "2222"
REMOTE_USER = "algo-runner"
REMOTE_PROJECT_DIR = "/home/algo-runner/bot"

PROOF_GATE_TESTS = [
    "tests/proof/test_forecast_lane.py",
    "tests/proof/test_resolution_sync.py",
    "tests/proof/test_weather_rbi_truth.py",
    "tests/proof/test_weather_sovereign.py",
    "tests/proof/test_lane_gating.py",
    "tests/proof/test_trading_control.py",
    "tests/proof/test_scheduler_cadence_config.py",
    "tests/proof/test_runtime_layer.py",
    "tests/proof/test_release_audit.py",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _quote_age_minutes(*quotes: dict[str, Any]) -> float | None:
    ages: list[float] = []
    now = datetime.now(timezone.utc)
    for quote in quotes:
        dt = _parse_utc(quote.get("ts"))
        if dt is None:
            continue
        ages.append(max(0.0, (now - dt).total_seconds() / 60.0))
    if not ages:
        return None
    return round(max(ages), 2)


def _run_command(label: str, command: list[str]) -> dict[str, Any]:
    started = time.time()
    result = subprocess.run(
        command,
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    lines = [line for line in output.strip().splitlines() if line.strip()]
    return {
        "label": label,
        "command": " ".join(shlex.quote(token) for token in command),
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "duration_seconds": round(time.time() - started, 2),
        "output_tail": lines[-40:],
    }


def _git_head_sha() -> str:
    git_dir = _ROOT / ".git"
    if not git_dir.exists():
        return ""
    try:
        return subprocess.check_output(
            ["git", "-C", str(_ROOT), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return ""


def _running_in_container() -> bool:
    return Path("/.dockerenv").exists()


def _build_quote_map(snapshot) -> dict[str, Any]:
    yes_quote = snapshot.yes_quote or {}
    no_quote = snapshot.no_quote or {}
    return {
        "yes_bid": yes_quote.get("bid"),
        "yes_ask": yes_quote.get("ask"),
        "yes_bid_size": yes_quote.get("bid_size"),
        "yes_ask_size": yes_quote.get("ask_size"),
        "no_bid": no_quote.get("bid"),
        "no_ask": no_quote.get("ask"),
        "no_bid_size": no_quote.get("bid_size"),
        "no_ask_size": no_quote.get("ask_size"),
    }


class _AuditBrokerStub:
    def __init__(self, quote_map: dict[str, dict[str, Any]]) -> None:
        self._quote_map = quote_map

    def get_quote(self, ticker: str) -> dict[str, Any]:
        return dict(self._quote_map.get(ticker) or {})


def _scan_live_market_surface(
    *,
    bankroll: float,
    open_positions: list[dict[str, Any]],
    scan_limit: int,
) -> dict[str, Any]:
    active_contracts = get_active_contracts(db_path=DB_PATH)
    all_snapshots = build_market_snapshots(
        active_contracts,
        get_bars_fn=lambda contract_id, interval: get_bars(
            contract_id,
            interval,
            limit=200,
            db_path=DB_PATH,
        ),
        get_quotes_fn=lambda market_id, strike, last_trade_at: get_paired_quotes(
            market_id,
            strike,
            last_trade_at,
            db_path=DB_PATH,
        ),
    )
    snapshots = all_snapshots[: max(1, int(scan_limit))]
    if not snapshots:
        return {
            "sample_size": 0,
            "rows": [],
            "markets_scanned": 0,
            "approved_candidates": 0,
            "execution_ready": 0,
            "thin_liquidity_count": 0,
            "infrastructure_rejections": [],
            "non_blocking_rejections": [],
            "systematic_thin_liquidity": False,
        }

    open_positions_for_eval = [
        {
            "local_symbol": str(
                pos.get("local_symbol") or pos.get("ticker") or ""
            ),
            "side": str(pos.get("side") or "").upper(),
            "qty": _coerce_float(pos.get("qty")),
            "entry_price": _coerce_float(
                pos.get("entry_price") or pos.get("entry") or pos.get("avg_entry")
            ),
        }
        for pos in open_positions
        if str(pos.get("local_symbol") or pos.get("ticker") or "")
    ]
    open_event_families: defaultdict[str, int] = defaultdict(int)
    for pos in open_positions_for_eval:
        family = str(pos.get("local_symbol") or "").split("-")[0]
        if family:
            open_event_families[family] += 1

    deployed_value = sum(
        _coerce_float(pos.get("entry_price")) * _coerce_float(pos.get("qty"))
        for pos in open_positions_for_eval
    )
    deployed_pct = min(1.0, deployed_value / max(float(bankroll), 1.0))
    candidates = evaluate_market_snapshots(
        snapshots=snapshots,
        bankroll=float(bankroll),
        deployed_pct=deployed_pct,
        open_positions_count=len(open_positions_for_eval),
        open_event_families=open_event_families,
        macro_context=_get_macro_context(),
        open_positions=open_positions_for_eval,
    )

    quote_map = {
        snapshot.ticker: _build_quote_map(snapshot)
        for snapshot in snapshots
    }
    execution_controller = KalshiExecutionController(_AuditBrokerStub(quote_map))

    rows: list[dict[str, Any]] = []
    infra_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    approved_candidates = 0
    execution_ready = 0
    thin_liquidity_count = 0

    for candidate in candidates:
        result = candidate["result"]
        contract = candidate["contract"]
        snapshot = candidate["snapshot"]
        ticker = str(contract.get("local_symbol") or "")
        projected_weather = {}
        try:
            from data.kalshi_weather_monitor import get_contract_weather_data

            projected_weather = get_contract_weather_data(
                ticker,
                contract_name=str(contract.get("contract_name") or ""),
                strike=_coerce_float(contract.get("strike"), 0.0),
                resolution_at=str(contract.get("resolution_at") or ""),
                last_trade_at=str(contract.get("last_trade_at") or ""),
            )
        except Exception:
            projected_weather = {}

        provider_mode = str(projected_weather.get("provider_mode") or "")
        weather_age_minutes = None
        if projected_weather:
            ts_value = projected_weather.get("timestamp")
            try:
                weather_age_minutes = round(
                    max(0.0, (time.time() - float(ts_value)) / 60.0),
                    2,
                )
            except (TypeError, ValueError):
                weather_age_minutes = None

        quote_age_minutes = _quote_age_minutes(
            snapshot.yes_quote or {},
            snapshot.no_quote or {},
        )
        strategy_signal = (
            str(result.strategy_family or "").lower() != "vetoed"
            and str(result.side or "").upper() in {"YES", "NO"}
        )
        plan_status = ""
        plan_reason = ""
        execution_planable = False
        if result.econ_approved and int(result.position_contracts or 0) > 0:
            approved_candidates += 1
            plan = execution_controller.plan_entry(
                TradeIntent(
                    contract=contract,
                    result=result,
                    bankroll=float(bankroll),
                    buying_power_usd=float(bankroll),
                    market_snapshot=snapshot,
                )
            )
            plan_status = str(plan.status or "")
            plan_reason = str(plan.reason or "")
            execution_planable = plan.status == "ready"
            if execution_planable:
                execution_ready += 1
            if (not execution_planable) and is_liquidity_warning(plan.reason or ""):
                thin_liquidity_count += 1
        if execution_planable:
            final_reason = "candidate_ready"
        else:
            final_reason = (
                str(result.veto_reason or "").strip()
                or plan_reason
                or "sizing_zero"
            )
        if is_infrastructure_reason(final_reason):
            infra_counts[final_reason] += 1
        elif final_reason not in {"", "candidate_ready"}:
            warning_counts[final_reason] += 1

        rows.append(
            {
                "ticker": ticker,
                "provider_mode": provider_mode,
                "weather_data_present": bool(projected_weather),
                "weather_age_minutes": weather_age_minutes,
                "quote_age_minutes": quote_age_minutes,
                "strategy_signal": strategy_signal,
                "strategy_family": str(result.strategy_family or ""),
                "side": str(result.side or ""),
                "ev_gate_pass": bool(result.econ_approved),
                "ev": round(_coerce_float(result.ev), 4),
                "position_contracts": int(result.position_contracts or 0),
                "execution_planable": execution_planable,
                "execution_plan_status": plan_status,
                "final_reason": final_reason,
            }
        )

    sample_size = len(rows)
    systematic_thin_liquidity = bool(
        approved_candidates > 0
        and thin_liquidity_count >= max(2, math.ceil(approved_candidates * 0.6))
    )
    return {
        "sample_size": sample_size,
        "rows": rows,
        "markets_scanned": len(snapshots),
        "approved_candidates": approved_candidates,
        "execution_ready": execution_ready,
        "thin_liquidity_count": thin_liquidity_count,
        "infrastructure_rejections": [
            {"reason": reason, "count": count}
            for reason, count in infra_counts.most_common(6)
        ],
        "non_blocking_rejections": [
            {"reason": reason, "count": count}
            for reason, count in warning_counts.most_common(6)
        ],
        "systematic_thin_liquidity": systematic_thin_liquidity,
    }


def _market_scan_findings(
    scan: dict[str, Any],
    *,
    active_markets: int,
    strict_runtime: bool,
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    sample_size = int(scan.get("sample_size") or 0)
    infra_rows = scan.get("infrastructure_rejections") or []
    infra_count = sum(int(row.get("count") or 0) for row in infra_rows)

    if sample_size == 0:
        if strict_runtime and active_markets > 0:
            blockers.append("scan_no_markets_scored")
        else:
            warnings.append("market_scan_unavailable")
        return blockers, warnings

    infra_threshold = max(2, math.ceil(sample_size * 0.3))
    if infra_count >= infra_threshold and strict_runtime:
        blockers.append(
            f"quote_ingestion_failure ({infra_count}/{sample_size} infrastructure vetoes)"
        )
    elif infra_count > 0:
        warnings.append(
            f"infrastructure_vetoes_present ({infra_count}/{sample_size})"
        )

    if bool(scan.get("systematic_thin_liquidity")):
        warnings.append("systematic_thin_liquidity")

    return blockers, warnings


def _render_markdown_report(payload: dict[str, Any]) -> str:
    blockers = payload.get("blockers") or []
    warnings = payload.get("warnings") or []
    details = payload.get("details") or {}
    scan = details.get("market_scan") or {}

    lines = [
        "# Release Audit",
        "",
        f"- Verdict: `{payload.get('verdict')}`",
        f"- Entries Allowed: `{payload.get('entries_allowed')}`",
        f"- Mode: `{payload.get('mode')}`",
        f"- Audited SHA: `{payload.get('audited_sha') or 'unknown'}`",
        f"- Timestamp: `{payload.get('as_of')}`",
        "",
        "## Blockers",
    ]
    if blockers:
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append("- None")

    lines.extend(["", "## Warnings"])
    if warnings:
        lines.extend(f"- {item}" for item in warnings)
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Runtime Signals",
            f"- Provider Mode: `{details.get('provider_status', {}).get('provider_mode') or 'unknown'}`",
            f"- Broker Connected: `{details.get('live_truth', {}).get('broker_connected')}`",
            f"- Heartbeat Fresh: `{details.get('release_status', {}).get('heartbeat_fresh')}`",
            f"- Active Markets: `{details.get('live_truth', {}).get('active_markets')}`",
            "",
            "## Market Scan",
            f"- Markets Scanned: `{scan.get('markets_scanned', 0)}`",
            f"- Approved Candidates: `{scan.get('approved_candidates', 0)}`",
            f"- Execution Ready: `{scan.get('execution_ready', 0)}`",
            f"- Thin Liquidity Count: `{scan.get('thin_liquidity_count', 0)}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _summarize_verdict(blockers: list[str], warnings: list[str]) -> str:
    if blockers:
        return VERDICT_BLOCKED
    if warnings:
        return VERDICT_PASS_WITH_WARNINGS
    return VERDICT_READY_FOR_LIVE


def _slim_live_truth(truth: dict[str, Any]) -> dict[str, Any]:
    lane = truth.get("forecast_lane") or {}
    return {
        "broker_connected": bool(truth.get("broker_connected")),
        "broker_error": str(truth.get("broker_error") or ""),
        "balance_usd": truth.get("balance_usd"),
        "active_markets": int(truth.get("active_markets") or 0),
        "broker_positions_count": int(truth.get("broker_positions_count") or 0),
        "lane": {
            "health": lane.get("health"),
            "readiness_state": lane.get("readiness_state"),
            "heartbeat_stale": lane.get("heartbeat_stale"),
            "heartbeat_age_seconds": lane.get("heartbeat_age_seconds"),
            "blocked_reason": lane.get("blocked_reason"),
        },
    }


def _run_local_audit(*, scan_limit: int) -> dict[str, Any]:
    build = get_build_info()
    warnings: list[str] = []
    commands = [
        _run_command(
            "proof_gate",
            [sys.executable, "-m", "pytest", *PROOF_GATE_TESTS, "-q", "--tb=short", "--no-header"],
        ),
        _run_command("validate", [sys.executable, "scripts/validate.py"]),
        _run_command(
            "repo_truth_gate",
            [sys.executable, "scripts/repo_truth_gate.py", "--strict"],
        ),
    ]
    blockers = [
        f"{cmd['label']}_failed"
        for cmd in commands
        if not bool(cmd.get("ok"))
    ]

    health = run_health_check(force=True)
    storage = runtime_storage_status()
    if not bool(storage.get("ok")):
        blockers.append("storage_headroom_low")

    db_path = Path(DB_PATH)
    wal_path = Path(f"{DB_PATH}-wal")
    wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
    db_bytes = db_path.stat().st_size if db_path.exists() else 0

    scan = _scan_live_market_surface(
        bankroll=float(ACCOUNT_SIZE),
        open_positions=get_open_forecast_positions(db_path=DB_PATH),
        scan_limit=scan_limit,
    )
    live_truth = {
        "broker_connected": None,
        "active_markets": len({row.get("ticker") for row in scan.get("rows", [])}),
    }
    scan_blockers, scan_warnings = _market_scan_findings(
        scan,
        active_markets=int(scan.get("markets_scanned") or 0),
        strict_runtime=False,
    )
    blockers.extend(scan_blockers)
    warnings.extend(scan_warnings)
    if not bool(health.get("healthy")):
        warnings.append("health_check_degraded")

    verdict = _summarize_verdict(blockers, warnings)
    return {
        "mode": "local",
        "as_of": _now_iso(),
        "audited_sha": str(build.get("sha") or _git_head_sha() or ""),
        "verdict": verdict,
        "entries_allowed": verdict != VERDICT_BLOCKED,
        "last_successful_audit_at": _now_iso() if verdict != VERDICT_BLOCKED else "",
        "blockers": blockers,
        "warnings": warnings,
        "details": {
            "build": build,
            "commands": commands,
            "health_check": health,
            "storage": {
                **storage,
                "db_bytes": db_bytes,
                "wal_bytes": wal_bytes,
            },
            "market_scan": scan,
            "live_truth": live_truth,
        },
    }


def _docker_service_status() -> dict[str, Any]:
    services = {
        "execution-engine": {"up": False, "status": ""},
        "telegram-oracle": {"up": False, "status": ""},
        "kalshi-cockpit": {"up": False, "status": ""},
    }
    try:
        output = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}|{{.Status}}"],
            text=True,
        )
    except Exception as exc:
        return {"services": services, "error": str(exc)}

    for raw in output.splitlines():
        name, _sep, status = raw.partition("|")
        if name in services:
            services[name] = {"up": status.startswith("Up"), "status": status}
    return {"services": services}


def _cockpit_health() -> dict[str, Any]:
    urls = [
        "http://127.0.0.1:8501/_stcore/health",
        "http://kalshi-cockpit:8501/_stcore/health",
    ]
    last_error = ""
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                body = resp.read().decode("utf-8").strip()
            return {"ok": body == "ok", "url": url, "body": body}
        except Exception as exc:
            last_error = str(exc)
    return {"ok": False, "url": urls[-1], "error": last_error}


def _run_remote_hosted_audit(*, scan_limit: int, soak_seconds: int) -> dict[str, Any]:
    build = get_build_info()
    init_incident_table(DB_PATH)
    ingest_system_events(lookback_minutes=180, db_path=DB_PATH)
    health = run_health_check(force=True)
    truth = get_live_kalshi_status(db_path=DB_PATH, connect=True, sync_broker=True)
    provider_status = get_weather_provider_status(db_path=DB_PATH)
    balance_truth = get_balance_truth_status(truth=truth, db_path=DB_PATH)
    containers = _docker_service_status()
    cockpit = _cockpit_health()
    model_probe = probe_reasoning_model()
    container_mode = _running_in_container()

    open_positions = truth.get("broker_positions") or truth.get("db_positions") or []
    bankroll = _coerce_float(truth.get("balance_usd"), ACCOUNT_SIZE)
    scan = _scan_live_market_surface(
        bankroll=bankroll if bankroll > 0 else float(ACCOUNT_SIZE),
        open_positions=open_positions,
        scan_limit=scan_limit,
    )

    blockers: list[str] = []
    warnings: list[str] = []
    if not str(build.get("sha") or "").strip():
        blockers.append("deploy_runtime_sha_missing")

    if not bool(truth.get("broker_connected")):
        blockers.append(str(truth.get("broker_error") or "broker_disconnected"))
    if not balance_truth.get("balance_ok"):
        if balance_truth.get("comparison_available"):
            blockers.append(
                f"balance_truth_mismatch ({balance_truth.get('delta_usd')} usd)"
            )
        else:
            blockers.append("get_account_balance_failed")

    lane = truth.get("forecast_lane") or {}
    if bool(lane.get("heartbeat_stale")):
        blockers.append("stale_runtime_heartbeat")

    if int(get_incident_summary(DB_PATH).get("by_severity", {}).get("CRITICAL", 0) or 0) > 0:
        blockers.append("unresolved_critical_incidents")

    services = containers.get("services") or {}
    if not container_mode:
        for name, status in services.items():
            if not bool(status.get("up")):
                blockers.append(f"{name}_down")
        if containers.get("error"):
            blockers.append(f"docker_ps_failed ({containers['error']})")
    elif containers.get("error"):
        warnings.append("docker_service_check_skipped_in_container_mode")

    if not bool(cockpit.get("ok")):
        blockers.append("cockpit_health_failed")

    if not bool(model_probe.get("ok")):
        blockers.append(
            f"telegram_model_probe_failed ({model_probe.get('error') or 'no response'})"
        )

    provider_mode = str(provider_status.get("provider_mode") or "").strip()
    if int(truth.get("active_markets") or 0) > 0:
        if not provider_status.get("data_present"):
            blockers.append("weather_provider_unavailable")
        elif not provider_mode:
            blockers.append("provider_mode_unknown")

    storage = runtime_storage_status()
    if not bool(storage.get("ok")):
        blockers.append("storage_headroom_low")

    scan_blockers, scan_warnings = _market_scan_findings(
        scan,
        active_markets=int(truth.get("active_markets") or 0),
        strict_runtime=True,
    )
    blockers.extend(scan_blockers)
    warnings.extend(scan_warnings)

    if not bool(health.get("healthy")):
        warnings.append("health_check_degraded")

    if soak_seconds > 0:
        time.sleep(max(0, int(soak_seconds)))
        truth_after_soak = get_live_kalshi_status(
            db_path=DB_PATH,
            connect=False,
            sync_broker=False,
        )
        lane_after_soak = truth_after_soak.get("forecast_lane") or {}
        if bool(lane_after_soak.get("heartbeat_stale")):
            blockers.append("stale_runtime_heartbeat_after_soak")
        if not bool(truth_after_soak.get("broker_connected")):
            blockers.append("broker_disconnected_after_soak")

    verdict = _summarize_verdict(blockers, warnings)
    payload = {
        "mode": "remote_hosted",
        "as_of": _now_iso(),
        "audited_sha": str(build.get("sha") or ""),
        "verdict": verdict,
        "entries_allowed": verdict != VERDICT_BLOCKED,
        "last_successful_audit_at": _now_iso() if verdict != VERDICT_BLOCKED else "",
        "blockers": blockers,
        "warnings": warnings,
        "details": {
            "build": build,
            "health_check": health,
            "storage": storage,
            "live_truth": _slim_live_truth(truth),
            "provider_status": provider_status,
            "balance_truth": balance_truth,
            "containers": containers,
            "cockpit": cockpit,
            "telegram_model_probe": model_probe,
            "open_incidents": {
                "summary": get_incident_summary(DB_PATH),
                "critical": [
                    {
                        "source": row.get("source"),
                        "sample_message": row.get("sample_message"),
                    }
                    for row in get_open_incidents(DB_PATH)
                    if str(row.get("severity") or "").upper() == "CRITICAL"
                ][:5],
            },
            "market_scan": scan,
            "container_mode": container_mode,
        },
    }
    markdown = _render_markdown_report(payload)
    write_release_audit_artifact(payload, markdown=markdown)
    payload["details"]["release_status"] = get_release_status(
        db_path=DB_PATH,
        truth=truth,
    )
    return payload


def _run_remote_audit(*, scan_limit: int, soak_seconds: int) -> dict[str, Any]:
    local_sha = _git_head_sha()
    container_audit_cmd = (
        f"cd /app && python3 scripts/release_audit.py --remote-hosted "
        f"--scan-limit {int(scan_limit)} --soak-seconds {int(soak_seconds)} --format json"
    )
    remote_cmd = [
        "ssh",
        "-p",
        REMOTE_PORT,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        f"{REMOTE_USER}@{REMOTE_HOST}",
        (
            f"cd {shlex.quote(REMOTE_PROJECT_DIR)} && "
            f"docker exec -i execution-engine sh -lc "
            f"{shlex.quote(container_audit_cmd)}"
        ),
    ]
    proc = subprocess.run(
        remote_cmd,
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    raw_output = (proc.stdout or "").strip()
    blockers: list[str] = []
    warnings: list[str] = []
    remote_payload: dict[str, Any] = {}

    if proc.returncode != 0:
        blockers.append("remote_release_audit_failed")
        if proc.stderr:
            warnings.append(proc.stderr.strip().splitlines()[-1])
    else:
        try:
            remote_payload = json.loads(raw_output)
        except Exception:
            blockers.append("remote_release_audit_parse_failed")

    remote_sha = str(remote_payload.get("audited_sha") or "").strip()
    if local_sha and remote_sha and local_sha != remote_sha:
        blockers.append(f"remote_sha_mismatch ({remote_sha} != {local_sha})")

    if remote_payload and str(remote_payload.get("verdict") or "") == VERDICT_BLOCKED:
        blockers.extend(str(item) for item in (remote_payload.get("blockers") or []))
    elif remote_payload:
        warnings.extend(str(item) for item in (remote_payload.get("warnings") or []))

    verdict = _summarize_verdict(blockers, warnings)
    payload = {
        "mode": "remote",
        "as_of": _now_iso(),
        "audited_sha": remote_sha or local_sha,
        "verdict": verdict,
        "entries_allowed": verdict != VERDICT_BLOCKED,
        "last_successful_audit_at": _now_iso() if verdict != VERDICT_BLOCKED else "",
        "blockers": blockers,
        "warnings": warnings,
        "details": {
            "ssh_command": " ".join(shlex.quote(token) for token in remote_cmd),
            "remote_payload": remote_payload,
        },
    }
    return payload


def _run_promote(*, scan_limit: int, soak_seconds: int) -> dict[str, Any]:
    local = _run_local_audit(scan_limit=scan_limit)
    remote = _run_remote_audit(scan_limit=scan_limit, soak_seconds=soak_seconds)

    blockers = list(local.get("blockers") or []) + list(remote.get("blockers") or [])
    warnings = list(local.get("warnings") or []) + list(remote.get("warnings") or [])

    verdict = _summarize_verdict(blockers, warnings)
    return {
        "mode": "promote",
        "as_of": _now_iso(),
        "audited_sha": str(remote.get("audited_sha") or local.get("audited_sha") or ""),
        "verdict": verdict,
        "entries_allowed": verdict != VERDICT_BLOCKED,
        "last_successful_audit_at": _now_iso() if verdict != VERDICT_BLOCKED else "",
        "blockers": blockers,
        "warnings": warnings,
        "details": {
            "local": local,
            "remote": remote,
        },
    }


def _print_payload(payload: dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    print(f"Release Audit :: {payload.get('mode')}")
    print(f"Verdict: {payload.get('verdict')}")
    print(f"Entries Allowed: {payload.get('entries_allowed')}")
    if payload.get("blockers"):
        print("Blockers:")
        for blocker in payload["blockers"]:
            print(f"  - {blocker}")
    if payload.get("warnings"):
        print("Warnings:")
        for warning in payload["warnings"]:
            print(f"  - {warning}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--local", action="store_true", help="Run repo-local release checks.")
    mode.add_argument("--remote", action="store_true", help="SSH into the droplet and run the hosted release audit.")
    mode.add_argument("--promote", action="store_true", help="Run local + remote release checks and summarize a promotion verdict.")
    mode.add_argument("--remote-hosted", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--scan-limit", type=int, default=12, help="Maximum market snapshots to score in the bounded scan.")
    parser.add_argument("--soak-seconds", type=int, default=600, help="Runtime soak window for the hosted audit.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Stdout format.")
    args = parser.parse_args()

    if args.local:
        payload = _run_local_audit(scan_limit=args.scan_limit)
        markdown = _render_markdown_report(payload)
        write_release_audit_artifact(payload, markdown=markdown)
    elif args.remote:
        payload = _run_remote_audit(
            scan_limit=args.scan_limit,
            soak_seconds=args.soak_seconds,
        )
        markdown = _render_markdown_report(payload)
        write_release_audit_artifact(payload, markdown=markdown)
    elif args.promote:
        payload = _run_promote(
            scan_limit=args.scan_limit,
            soak_seconds=args.soak_seconds,
        )
        markdown = _render_markdown_report(payload)
        write_release_audit_artifact(payload, markdown=markdown)
    else:
        payload = _run_remote_hosted_audit(
            scan_limit=args.scan_limit,
            soak_seconds=args.soak_seconds,
        )
        markdown = _render_markdown_report(payload)
        write_release_audit_artifact(payload, markdown=markdown)

    _print_payload(payload, args.format)
    return 1 if str(payload.get("verdict") or "") == VERDICT_BLOCKED else 0


if __name__ == "__main__":
    raise SystemExit(main())
