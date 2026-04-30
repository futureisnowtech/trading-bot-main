#!/usr/bin/env python3
"""
scripts/check_readiness.py — operator readiness snapshot for the active spot truth-lane.

This script is the operator-facing preflight for tiny live.
It reads:
- runtime state machine
- broker-first spot truth
- spot kill-switch status
- latest spot health row
- evidence-backed go-live audit
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJ_ROOT not in sys.path:
    sys.path.insert(0, PROJ_ROOT)

from runtime.runtime_state import get_lane_state, get_system_state
from runtime.spot_kill_switch import kill_switch_status
from runtime.spot_position_truth import get_spot_position_truth
from scripts.truth_audit_lib import build_go_live_audit, default_db_path


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(default_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _latest_health_row() -> dict:
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT ts, level, message FROM system_events "
                "WHERE source='health_check' ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _fmt_money(value: float | int | None) -> str:
    return f"${float(value or 0.0):,.2f}"


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sys_state = get_system_state()
    crypto = get_lane_state("crypto")
    truth = get_spot_position_truth(paper=False)
    ks = kill_switch_status()
    audit = build_go_live_audit(default_db_path())
    health = _latest_health_row()

    blockers = truth.get("blocking_issues") or []
    holdings = truth.get("all_live_holdings") or []
    external_manual = [
        row
        for row in holdings
        if row.get("position_truth_status") == "external_manual"
    ]
    lane_readiness = str(crypto.get("readiness_state") or "UNKNOWN")
    system_readiness = str(sys_state.get("launch_readiness_state") or "UNKNOWN")

    ready = (
        lane_readiness in {"READY_FOR_TINY_LIVE", "TINY_LIVE"}
        and truth.get("snapshot_ok")
        and not blockers
        and not ks.get("halted")
    )

    print("\n" + "=" * 72)
    print("SPOT TINY-LIVE READINESS")
    print(f"Generated: {now}")
    print("=" * 72)

    print("\nState Machine")
    print(f"  system: {system_readiness}")
    print(f"  crypto lane: {lane_readiness}")
    print(f"  process mode: {sys_state.get('process_mode') or 'unknown'}")

    print("\nBroker Truth")
    print(f"  snapshot_ok: {truth.get('snapshot_ok')}")
    print(f"  broker cash: {_fmt_money(truth.get('broker_cash_usd'))}")
    print(f"  visible holdings: {len(holdings)}")
    print(f"  bot-managed positions: {len(truth.get('bot_managed_positions') or [])}")
    print(f"  external/manual holdings: {len(external_manual)}")
    print(f"  blockers: {len(blockers)}")
    for row in blockers[:10]:
        print(
            f"    - {row.get('symbol') or 'GLOBAL'}: "
            f"{row.get('position_truth_status') or 'unknown'}"
        )

    if external_manual:
        print("\nExternal / Manual Holdings")
        for row in external_manual[:12]:
            print(
                f"  - {row.get('symbol')}: "
                f"qty={row.get('qty')} value={_fmt_money(row.get('current_value'))}"
            )

    print("\nSpot Kill Switch")
    print(f"  halted: {ks.get('halted')}")
    print(f"  last halt ts: {ks.get('last_halt_ts') or 'none'}")
    print(f"  last halt reason: {ks.get('last_halt_reason') or 'none'}")

    print("\nLatest Spot Health")
    if health:
        print(f"  ts: {health.get('ts')}")
        print(f"  level: {health.get('level')}")
        print(f"  message: {health.get('message')}")
    else:
        print("  no health_check row found")

    print("\nEvidence Audit")
    go_live = audit.get("go_live", {})
    print(f"  recommendation status: {go_live.get('status')}")
    print(f"  primary recommendation: {go_live.get('primary_recommendation')}")
    for item in (go_live.get("exact_tonight") or [])[:5]:
        print(f"    - {item}")

    print("\nVerdict")
    if ready and lane_readiness == "READY_FOR_TINY_LIVE":
        print("  READY_FOR_TINY_LIVE")
    elif ready and lane_readiness == "TINY_LIVE":
        print("  TINY_LIVE (already launched)")
    elif ks.get("halted"):
        print("  HALTED")
    elif blockers or not truth.get("snapshot_ok"):
        print("  NOT_READY — spot truth blockers present")
    else:
        print(f"  {lane_readiness or 'NOT_READY'}")

    print("=" * 72 + "\n")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
