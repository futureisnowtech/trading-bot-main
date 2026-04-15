#!/usr/bin/env python3
"""
scripts/live_runtime_audit.py — Post-restart runtime truth audit.

Checks actual runtime state after bot restart:
- process mode
- active lanes
- forecast lane state
- incident summary
- MES dormant confirmation
- Coinbase auth
- lane heartbeats

Usage: python3 scripts/live_runtime_audit.py
"""

import os
import sys
import socket
from datetime import datetime, timezone, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PASS = "✅"
WARN = "⚠️ "
FAIL = "❌"

_results = []


def _pr(icon: str, label: str, detail: str = "") -> None:
    line = f"  {icon} {label}"
    if detail:
        line += f": {detail}"
    print(line)
    _results.append((icon, label))


def _check_ibkr_port(
    host: str = "127.0.0.1", port: int = 0, timeout: float = 1.5
) -> bool:
    """Return True if IBKR/TWS port is open. Port 0 means read from config."""
    if port == 0:
        try:
            from config import IBKR_PORT as _ibkr_port

            port = _ibkr_port
        except Exception:
            port = 7496  # live port default (read from IBKR_PORT in config)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def main() -> int:
    print()
    print("━" * 56)
    print("  LIVE RUNTIME AUDIT — v15.2")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("━" * 56)

    # ── 1. Import runtime state ───────────────────────────────────────────────
    print("\n── System State ──────────────────────────────────────")
    try:
        from runtime.runtime_state import get_system_state, get_all_lane_states
        from runtime.incident_tracker import ingest_system_events, get_incident_summary

        sys_state = get_system_state()
        if not sys_state:
            _pr(WARN, "System state", "No row found — bot not yet started")
        else:
            mode = sys_state.get("process_mode", "unknown")
            alive = bool(sys_state.get("process_alive", 0))
            status = sys_state.get("global_status", "UNKNOWN")
            startup = sys_state.get("startup_ts", "N/A")
            readiness = sys_state.get("launch_readiness_state", "UNKNOWN")
            hb = sys_state.get("last_global_heartbeat_at", "never")

            _pr(
                PASS if alive else WARN,
                "Process alive",
                f"mode={mode}, status={status}",
            )
            _pr(PASS, "Startup time", startup)
            _pr(PASS, "Launch readiness", readiness)
            _pr(PASS if hb and hb != "never" else WARN, "Last heartbeat", hb or "never")

    except Exception as e:
        _pr(FAIL, "System state import failed", str(e))

    # ── 2. Lane states ────────────────────────────────────────────────────────
    print("\n── Lane States ───────────────────────────────────────")
    try:
        from runtime.runtime_state import get_all_lane_states
        from config import FUTURES_LANE_ACTIVE, FORECAST_LANE_ACTIVE

        lane_states = get_all_lane_states()
        if not lane_states:
            _pr(WARN, "Lane states", "No rows found — bot not yet started")
        else:
            lane_by_id = {l["lane_id"]: l for l in lane_states}

            for lid in ("crypto", "forecast", "mes_archived"):
                ls = lane_by_id.get(lid)
                if not ls:
                    _pr(WARN, f"Lane [{lid}]", "Not registered")
                    continue

                enabled = bool(ls.get("enabled", 0))
                active = bool(ls.get("active", 0))
                health = ls.get("health", "UNKNOWN")
                readiness = ls.get("readiness_state", "UNKNOWN")
                hb = ls.get("last_heartbeat_at", "never")
                blocked = ls.get("blocked_reason", "")

                if lid == "mes_archived":
                    if active:
                        _pr(
                            FAIL,
                            f"Lane [{lid}]",
                            f"active=True but FUTURES_LANE_ACTIVE={FUTURES_LANE_ACTIVE} — should be dormant",
                        )
                    else:
                        _pr(
                            PASS,
                            f"Lane [{lid}] DORMANT",
                            f"enabled={enabled}, readiness={readiness}",
                        )
                elif lid == "forecast":
                    if FORECAST_LANE_ACTIVE and not active:
                        _pr(
                            WARN,
                            f"Lane [{lid}]",
                            f"FORECAST_LANE_ACTIVE=True but active=False, readiness={readiness}",
                        )
                    elif not FORECAST_LANE_ACTIVE:
                        _pr(
                            PASS,
                            f"Lane [{lid}] DISABLED",
                            f"FORECAST_LANE_ACTIVE=False, readiness={readiness}",
                        )
                    else:
                        icon = PASS if health in ("OK", "UNKNOWN") else WARN
                        _pr(
                            icon,
                            f"Lane [{lid}]",
                            f"active={active}, health={health}, readiness={readiness}",
                        )
                else:  # crypto
                    icon = PASS if (active and health != "ERROR") else WARN
                    _pr(
                        icon,
                        f"Lane [{lid}]",
                        f"active={active}, health={health}, readiness={readiness}, hb={hb or 'never'}",
                    )

    except Exception as e:
        _pr(FAIL, "Lane states read failed", str(e))

    # ── 3. Incident summary ───────────────────────────────────────────────────
    print("\n── Incidents ─────────────────────────────────────────")
    try:
        from runtime.incident_tracker import ingest_system_events, get_incident_summary

        ingest_system_events()
        summary = get_incident_summary()
        total = summary.get("total_open", 0)
        by_sev = summary.get("by_severity", {})
        critical = by_sev.get("CRITICAL", 0)
        errors = by_sev.get("ERROR", 0)

        if critical > 0:
            _pr(
                FAIL, "Incidents", f"{total} open ({critical} CRITICAL, {errors} ERROR)"
            )
        elif errors > 0:
            _pr(WARN, "Incidents", f"{total} open ({errors} ERROR)")
        elif total > 0:
            _pr(WARN, "Incidents", f"{total} open (WARNING/INFO only)")
        else:
            _pr(PASS, "Incidents", "0 open incidents")

        if summary.get("by_lane"):
            for lane, cnt in summary["by_lane"].items():
                print(f"       {lane}: {cnt}")

    except Exception as e:
        _pr(WARN, "Incident check failed", str(e))

    # ── 4. Coinbase auth (paper=True — no API call) ───────────────────────────
    print("\n── Coinbase Auth ─────────────────────────────────────")
    try:
        from config import (
            PAPER_TRADING,
            COINBASE_CDP_KEY_NAME,
            COINBASE_CDP_PRIVATE_KEY,
        )

        if PAPER_TRADING:
            _pr(PASS, "Coinbase auth", "Paper mode — no credentials required")
        elif COINBASE_CDP_KEY_NAME and COINBASE_CDP_PRIVATE_KEY:
            key_preview = (
                COINBASE_CDP_KEY_NAME[:40] + "..."
                if len(COINBASE_CDP_KEY_NAME) > 40
                else COINBASE_CDP_KEY_NAME
            )
            _pr(PASS, "Coinbase credentials", f"CDP key present: {key_preview}")
        else:
            _pr(
                FAIL,
                "Coinbase credentials",
                "COINBASE_CDP_KEY_NAME or COINBASE_CDP_PRIVATE_KEY missing — live trading will fail",
            )
    except Exception as e:
        _pr(WARN, "Coinbase auth check failed", str(e))

    # Live Coinbase connectivity check (only when credentials are present)
    try:
        from config import COINBASE_CDP_KEY_NAME

        if COINBASE_CDP_KEY_NAME:
            try:
                from execution.coinbase_broker import CoinbaseBroker

                cb = CoinbaseBroker(paper=False)
                ok = cb.connect()
                if ok:
                    bp = cb.get_account_balance()
                    _pr(
                        PASS, "Coinbase LIVE auth", f"connected, buying_power=${bp:.2f}"
                    )
                else:
                    _pr(FAIL, "Coinbase LIVE auth", "connect() returned False")
            except Exception as e:
                _pr(FAIL, "Coinbase LIVE auth error", str(e))
    except Exception:
        pass

    # ── 5. IBKR port ─────────────────────────────────────────────────────────
    print("\n── IBKR Port ─────────────────────────────────────────")
    try:
        from config import FUTURES_LANE_ACTIVE, FORECAST_LANE_ACTIVE, IBKR_PORT

        port_open = _check_ibkr_port(port=IBKR_PORT)
        ibkr_needed = FUTURES_LANE_ACTIVE or FORECAST_LANE_ACTIVE
        if ibkr_needed:
            if port_open:
                who = []
                if FUTURES_LANE_ACTIVE:
                    who.append("MES")
                if FORECAST_LANE_ACTIVE:
                    who.append("ForecastEx")
                _pr(
                    PASS,
                    f"IBKR TWS port {IBKR_PORT}",
                    f"open — {'/'.join(who)} can connect",
                )
            else:
                _pr(
                    FAIL,
                    f"IBKR TWS port {IBKR_PORT}",
                    "closed — TWS not running (required)",
                )
        else:
            if port_open:
                _pr(
                    PASS,
                    f"IBKR TWS port {IBKR_PORT}",
                    "open — MES dormant and ForecastEx inactive (benign)",
                )
            else:
                _pr(
                    PASS,
                    f"IBKR TWS port {IBKR_PORT}",
                    "closed — MES dormant and ForecastEx inactive (expected)",
                )
    except Exception as e:
        _pr(WARN, "IBKR port check failed", str(e))

    # ── 6. MES dormant confirmation ───────────────────────────────────────────
    print("\n── MES Dormant Check ─────────────────────────────────")
    try:
        from config import FUTURES_LANE_ACTIVE

        if not FUTURES_LANE_ACTIVE:
            _pr(
                PASS,
                "MES dormant",
                "FUTURES_LANE_ACTIVE=False — MES fully archived (expected)",
            )
        else:
            _pr(
                WARN,
                "MES active",
                "FUTURES_LANE_ACTIVE=True — MES lane is NOT archived",
            )
    except Exception as e:
        _pr(WARN, "MES dormant check failed", str(e))

    # ── 7. Heartbeat age ──────────────────────────────────────────────────────
    print("\n── Heartbeat Age ─────────────────────────────────────")
    try:
        from runtime.runtime_state import get_system_state as _get_sys

        state = _get_sys()
        if state.get("last_global_heartbeat_at"):
            try:
                hb_ts = datetime.fromisoformat(state["last_global_heartbeat_at"])
                age_min = (datetime.now(timezone.utc) - hb_ts).total_seconds() / 60
                if age_min < 5:
                    _pr(PASS, "System heartbeat", f"{age_min:.1f}m ago")
                else:
                    _pr(WARN, "System heartbeat", f"{age_min:.1f}m ago (stale)")
            except Exception:
                _pr(WARN, "System heartbeat", "could not parse timestamp")
        else:
            _pr(WARN, "System heartbeat", "never written")
    except Exception as e:
        _pr(WARN, "Heartbeat age check failed", str(e))

    # ── 8. Forecast DB detail + readiness ────────────────────────────────────
    print("\n── Forecast DB ───────────────────────────────────────")
    try:
        import sqlite3 as _sqlite3
        from config import DB_PATH

        with _sqlite3.connect(DB_PATH) as _conn:
            _conn.row_factory = _sqlite3.Row
            n_markets = _conn.execute(
                "SELECT COUNT(*) FROM forecast_markets WHERE active=1"
            ).fetchone()[0]
            n_contracts = _conn.execute(
                "SELECT COUNT(*) FROM forecast_contracts WHERE active=1"
            ).fetchone()[0]
            n_quotes = _conn.execute("SELECT COUNT(*) FROM forecast_quotes").fetchone()[
                0
            ]
            n_bars = _conn.execute(
                "SELECT COUNT(*) FROM forecast_bars WHERE interval='5m'"
            ).fetchone()[0]
            n_stubs = _conn.execute(
                "SELECT COUNT(*) FROM forecast_markets fm WHERE fm.active=1 "
                "AND NOT EXISTS (SELECT 1 FROM forecast_contracts fc WHERE fc.market_id=fm.id AND fc.active=1)"
            ).fetchone()[0]
        icon = PASS if (n_markets > 0 or n_contracts > 0) else WARN
        _pr(
            icon,
            "Forecast DB",
            f"markets={n_markets} (stubs={n_stubs}), contracts={n_contracts}, "
            f"quotes={n_quotes}, bars_5m={n_bars}",
        )

        # Sample forecast_markets rows
        if n_markets > 0:
            with _sqlite3.connect(DB_PATH) as _conn2:
                _conn2.row_factory = _sqlite3.Row
                sample = _conn2.execute(
                    "SELECT market_symbol, market_name, underlier_conid, first_seen_at "
                    "FROM forecast_markets WHERE active=1 LIMIT 8"
                ).fetchall()
            for r in sample:
                print(
                    f"       {r['market_symbol']}: conid={r['underlier_conid']}, "
                    f"name='{r['market_name'] or '(stub)'}', first_seen={r['first_seen_at'][:16]}"
                )

    except Exception as e:
        _pr(WARN, "Forecast DB", f"error — {e}")

    # ── 9. Forecast readiness classification ─────────────────────────────────
    print("\n── Forecast Readiness ────────────────────────────────")
    try:
        from dashboard.data.forecast import get_forecast_readiness

        r = get_forecast_readiness()
        lane_state = r.get("lane_state", "UNKNOWN")
        status = r.get("status", "UNKNOWN")
        underliers = r.get("underliers_visible", 0)
        unavailable = r.get("contracts_unavailable_count", 0)

        ready_states = {"OPERATIONAL"}
        blocked_states = {"BROKER_DISCONNECTED", "NO_UNDERLIERS", "LANE_NOT_STARTED"}
        icon = (
            PASS
            if lane_state in ready_states
            else (WARN if lane_state not in blocked_states else FAIL)
        )
        _pr(
            icon,
            "Forecast readiness",
            f"{lane_state} | underliers={underliers}, stubs_unavail={unavailable}",
        )

        for chk in r.get("checks", []):
            chk_icon = (
                PASS
                if chk["status"] == "PASS"
                else (WARN if chk["status"] == "ACTION_NEEDED" else FAIL)
            )
            print(f"       {chk_icon} {chk['name']}: {chk['detail']}")

    except Exception as e:
        _pr(WARN, "Forecast readiness check failed", str(e))

    # ── Verdict ────────────────────────────────────────────────────────────────
    print()
    print("━" * 56)
    n_fail = sum(1 for icon, _ in _results if icon == FAIL)
    n_warn = sum(1 for icon, _ in _results if icon == WARN)

    if n_fail > 0:
        print(f"  {FAIL} OVERALL: NOT_READY — {n_fail} failure(s), {n_warn} warning(s)")
        verdict = 1
    elif n_warn > 0:
        print(
            f"  {WARN} OVERALL: NOT_READY — 0 failures, {n_warn} warning(s) require review"
        )
        verdict = 0
    else:
        print(f"  {PASS} OVERALL: READY — all checks passed")
        verdict = 0

    print("━" * 56)
    print()
    return verdict


if __name__ == "__main__":
    sys.exit(main())
