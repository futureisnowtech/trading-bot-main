#!/usr/bin/env python3
"""
scripts/live_runtime_audit.py — runtime truth audit for the active spot lane.

Checks:
- system + crypto lane runtime state
- broker-first spot truth snapshot
- spot truth blockers
- external/manual holdings visibility
- latest spot health row
- spot kill-switch state
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from runtime.runtime_state import get_all_lane_states, get_lane_state, get_system_state
from runtime.spot_kill_switch import kill_switch_status
from runtime.spot_position_truth import get_spot_position_truth
from scripts.truth_audit_lib import default_db_path

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
ALLOWED_SYSTEM_STATES = {"NOT_READY", "READY_FOR_TINY_LIVE", "TINY_LIVE", "DEGRADED", "HALTED"}
ALLOWED_LANE_STATES = {"NOT_READY", "READY_FOR_TINY_LIVE", "TINY_LIVE", "DEGRADED", "HALTED"}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(default_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _latest_health() -> dict:
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT ts, level, message FROM system_events "
                "WHERE source='health_check' ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _line(status: str, label: str, detail: str) -> tuple[str, str, str]:
    print(f"  [{status}] {label}: {detail}")
    return status, label, detail


def main() -> int:
    print()
    print("━" * 72)
    print("  SPOT LIVE RUNTIME AUDIT")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("━" * 72)

    findings: list[tuple[str, str, str]] = []

    sys_state = get_system_state()
    crypto = get_lane_state("crypto")
    truth = get_spot_position_truth(paper=False)
    ks = kill_switch_status()
    health = _latest_health()

    print("\n── System State ─────────────────────────────────────")
    sys_readiness = str(sys_state.get("launch_readiness_state") or "UNKNOWN")
    sys_legacy = sys_readiness not in ALLOWED_SYSTEM_STATES
    findings.append(
        _line(
            FAIL if sys_legacy else (PASS if sys_state else WARN),
            "system_runtime_state",
            (
                f"mode={sys_state.get('process_mode')} "
                f"status={sys_state.get('global_status')} "
                f"readiness={sys_readiness}"
            )
            if sys_state
            else "missing",
        )
    )
    if sys_legacy:
        findings.append(
            _line(
                FAIL,
                "system_runtime_state_legacy",
                "runtime row still uses pre-state-machine readiness language",
            )
        )

    print("\n── Crypto Lane ──────────────────────────────────────")
    lane_readiness = str(crypto.get("readiness_state") or "UNKNOWN") if crypto else "UNKNOWN"
    lane_legacy = lane_readiness not in ALLOWED_LANE_STATES
    findings.append(
        _line(
            FAIL
            if lane_legacy
            else (
                PASS
                if crypto and lane_readiness in {"READY_FOR_TINY_LIVE", "TINY_LIVE"}
                else WARN
            ),
            "crypto lane",
            (
                f"active={crypto.get('active')} connected={crypto.get('connected')} "
                f"health={crypto.get('health')} readiness={lane_readiness}"
            )
            if crypto
            else "missing",
        )
    )
    if lane_legacy:
        findings.append(
            _line(
                FAIL,
                "crypto_lane_legacy",
                "lane row still uses pre-state-machine readiness language",
            )
        )

    print("\n── Spot Truth ───────────────────────────────────────")
    blockers = truth.get("blocking_issues") or []
    holdings = truth.get("all_live_holdings") or []
    external_manual = [
        row
        for row in holdings
        if row.get("position_truth_status") == "external_manual"
    ]
    findings.append(
        _line(
            PASS if truth.get("snapshot_ok") else FAIL,
            "broker snapshot",
            f"snapshot_ok={truth.get('snapshot_ok')} broker_cash={truth.get('broker_cash_usd')}",
        )
    )
    findings.append(
        _line(
            FAIL if blockers else PASS,
            "spot truth blockers",
            ", ".join(
                f"{row.get('symbol') or 'GLOBAL'}:{row.get('position_truth_status')}"
                for row in blockers
            )
            if blockers
            else "none",
        )
    )
    findings.append(
        _line(
            PASS,
            "holdings visibility",
            f"live_holdings={len(holdings)} external_manual={len(external_manual)} bot_managed={len(truth.get('bot_managed_positions') or [])}",
        )
    )

    print("\n── Spot Health ──────────────────────────────────────")
    health_message = str(health.get("message") or "")
    legacy_health = "7/7" in health_message
    findings.append(
        _line(
            FAIL if legacy_health else (PASS if health and health.get("level") != "ERROR" else WARN),
            "latest health_check",
            health_message or "missing",
        )
    )
    if legacy_health:
        findings.append(
            _line(
                FAIL,
                "health_check_legacy",
                "old '7/7 HEALTHY' wording is not accepted as spot truth-lane proof",
            )
        )
    findings.append(
        _line(
            FAIL if ks.get("halted") else PASS,
            "spot kill switch",
            ks.get("last_halt_reason") or "not halted",
        )
    )

    print("\n── Dormant / Reference Lanes ────────────────────────")
    for lane in get_all_lane_states():
        lane_id = lane.get("lane_id")
        if lane_id == "crypto":
            continue
        _line(
            PASS,
            f"{lane_id}",
            f"active={lane.get('active')} readiness={lane.get('readiness_state')} (informational only)",
        )

    hard_fail = any(status == FAIL for status, _, _ in findings)
    print("\n── Verdict ──────────────────────────────────────────")
    if hard_fail:
        print("  RESULT: FAIL")
        return 1
    if blockers or not truth.get("snapshot_ok"):
        print("  RESULT: WARN")
        return 1
    print("  RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
