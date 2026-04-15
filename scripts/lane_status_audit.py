#!/usr/bin/env python3
"""
scripts/lane_status_audit.py — Quick lane status snapshot.

Shows current state of all lanes from runtime truth tables.
Usage: python3 scripts/lane_status_audit.py
"""

import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _trunc(s: str, n: int) -> str:
    if not s:
        return "—"
    return s[:n] if len(s) <= n else s[:n - 1] + "…"


def main() -> int:
    print()
    print("━" * 90)
    print("  LANE STATUS SNAPSHOT")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("━" * 90)

    try:
        from runtime.runtime_state import get_all_lane_states, get_system_state
    except Exception as e:
        print(f"  ERROR: Cannot import runtime.runtime_state: {e}")
        return 1

    sys_state = get_system_state()
    if sys_state:
        mode = sys_state.get("process_mode", "unknown")
        alive = "YES" if sys_state.get("process_alive", 0) else "NO"
        status = sys_state.get("global_status", "UNKNOWN")
        hb = sys_state.get("last_global_heartbeat_at", "never")
        print(f"\n  System: mode={mode}, alive={alive}, status={status}, heartbeat={hb or 'never'}")
    else:
        print("\n  System: not initialized (bot not yet started)")

    lane_states = get_all_lane_states()

    if not lane_states:
        print("\n  No lane rows found — bot not yet started.\n")
        return 0

    # Table header
    print()
    header = (
        f"  {'Lane':<16} {'Enabled':<8} {'Active':<7} {'Mode':<10} "
        f"{'Health':<10} {'Readiness':<22} {'Last Heartbeat'}"
    )
    print(header)
    print("  " + "-" * 86)

    for ls in lane_states:
        lane_id = ls.get("lane_id", "?")
        enabled = "YES" if ls.get("enabled", 0) else "NO "
        active  = "YES" if ls.get("active", 0) else "NO "
        mode    = _trunc(ls.get("mode") or "—", 10)
        health  = _trunc(ls.get("health") or "UNKNOWN", 10)
        rs      = _trunc(ls.get("readiness_state") or "UNKNOWN", 22)
        hb      = ls.get("last_heartbeat_at") or "never"
        # Shorten ISO timestamp
        if hb and hb != "never" and "T" in hb:
            hb = hb.replace("T", " ")[:19] + " UTC"

        print(
            f"  {lane_id:<16} {enabled:<8} {active:<7} {mode:<10} "
            f"{health:<10} {rs:<22} {hb}"
        )

    # Additional detail for blocked/errored lanes
    print()
    for ls in lane_states:
        blocked = ls.get("blocked_reason", "")
        action  = ls.get("action_needed", "")
        issues  = ls.get("issue_count", 0)
        lid     = ls.get("lane_id", "?")
        if blocked:
            print(f"  [{lid}] blocked_reason: {blocked}")
        if action:
            print(f"  [{lid}] action_needed: {action}")
        if issues and int(issues) > 0:
            print(f"  [{lid}] issue_count: {issues}")

    print("━" * 90)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
