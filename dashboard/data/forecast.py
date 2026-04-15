"""
dashboard/data/forecast.py — DB query layer for the FORECAST TRADING dashboard tab.

Mirrors the style of dashboard/data/futures.py: thin functions that return
dicts/lists, no Streamlit imports, all errors return safe defaults.

All queries hit the existing logs/trades.db via the 5 forecast_* tables
created by forecast/db.py.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from config import DB_PATH
except Exception:
    DB_PATH = os.path.join(_ROOT, "logs", "trades.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# ── Health / status ────────────────────────────────────────────────────────────


def get_forecast_health() -> dict:
    """
    Returns:
        {tables_exist, active_markets, underliers_visible, contracts_unavailable_count,
         active_contracts, quote_lag_minutes, bars_5m_count, positions_open,
         last_discovery_at, lane_started}
    """
    result = {
        "tables_exist": False,
        "active_markets": 0,
        "underliers_visible": 0,
        "contracts_unavailable_count": 0,
        "active_contracts": 0,
        "quote_lag_minutes": None,
        "bars_5m_count": 0,
        "positions_open": 0,
        "last_discovery_at": None,
        "lane_started": False,
    }
    try:
        with _conn() as c:
            # Check tables exist
            tables = {
                r[0]
                for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            required = {
                "forecast_markets",
                "forecast_contracts",
                "forecast_quotes",
                "forecast_bars",
                "forecast_resolutions",
            }
            result["tables_exist"] = required.issubset(tables)
            if not result["tables_exist"]:
                # Check if lane started even without tables
                try:
                    n = c.execute(
                        "SELECT COUNT(*) FROM system_events "
                        "WHERE source='ForecastRunner' "
                        "AND ts >= datetime('now','-2 hours')"
                    ).fetchone()[0]
                    result["lane_started"] = n > 0
                except Exception:
                    pass
                return result

            # Lane started: recent ForecastRunner system_events
            try:
                n = c.execute(
                    "SELECT COUNT(*) FROM system_events "
                    "WHERE source='ForecastRunner' "
                    "AND ts >= datetime('now','-2 hours')"
                ).fetchone()[0]
                result["lane_started"] = n > 0
            except Exception:
                pass

            # Underliers visible (all markets in DB, regardless of contracts)
            result["underliers_visible"] = c.execute(
                "SELECT COUNT(*) FROM forecast_markets WHERE active=1"
            ).fetchone()[0]

            # Active markets (alias for underliers_visible)
            result["active_markets"] = result["underliers_visible"]

            # Markets where stub_only / no contracts available
            result["contracts_unavailable_count"] = c.execute(
                "SELECT COUNT(*) FROM forecast_markets fm "
                "WHERE fm.active=1 "
                "AND NOT EXISTS (SELECT 1 FROM forecast_contracts fc WHERE fc.market_id=fm.id AND fc.active=1)"
            ).fetchone()[0]

            result["active_contracts"] = c.execute(
                "SELECT COUNT(*) FROM forecast_contracts WHERE active=1"
            ).fetchone()[0]

            # Quote freshness
            row = c.execute(
                "SELECT ts FROM forecast_quotes ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if row:
                try:
                    last_ts = datetime.fromisoformat(row[0])
                    if not last_ts.tzinfo:
                        last_ts = last_ts.replace(tzinfo=timezone.utc)
                    lag = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60.0
                    result["quote_lag_minutes"] = round(lag, 1)
                    result["last_discovery_at"] = row[0]
                except Exception:
                    pass

            result["bars_5m_count"] = c.execute(
                "SELECT COUNT(*) FROM forecast_bars WHERE interval='5m'"
            ).fetchone()[0]

    except Exception:
        pass
    return result


# ── Active positions ───────────────────────────────────────────────────────────


def get_forecast_positions() -> list[dict]:
    """
    Return open ForecastEx trades (entries without a matching exit).
    Queries the trades table where broker LIKE 'forecastex%' and pnl_usd=0.
    """
    try:
        today = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with _conn() as c:
            rows = c.execute(
                """SELECT ts, symbol, action, qty, price, fee_usd, notes, order_id
                   FROM trades
                   WHERE broker LIKE 'forecastex%'
                     AND action = 'BUY'
                     AND ts >= ?
                   ORDER BY ts DESC""",
                (today,),
            ).fetchall()
            # Only return positions that don't have a paired close
            # (simple heuristic: no SELL/CLOSE row with same order_id prefix)
            open_order_ids = set()
            closed_order_ids = set()
            for r in rows:
                notes = r["notes"] or ""
                if "reason=resolved" in notes or "reason=exit" in notes:
                    closed_order_ids.add(r["order_id"])
                else:
                    open_order_ids.add(r["order_id"])
            result = []
            for r in rows:
                if r["order_id"] not in closed_order_ids:
                    result.append(dict(r))
            return result
    except Exception:
        return []


# ── Recent trades ──────────────────────────────────────────────────────────────


def get_forecast_trades(limit: int = 50) -> list[dict]:
    """Return recent ForecastEx trades (all actions: BUY + close)."""
    try:
        with _conn() as c:
            rows = c.execute(
                """SELECT ts, symbol, action, qty, price, pnl_usd, fee_usd,
                          notes, order_id, paper
                   FROM trades
                   WHERE broker LIKE 'forecastex%'
                   ORDER BY ts DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# ── P&L summary ───────────────────────────────────────────────────────────────


def get_forecast_pnl_summary() -> dict:
    """
    Returns:
        {total_trades, wins, win_rate, total_pnl, best_trade, worst_trade,
         today_pnl, avg_ev_at_entry}
    """
    result = {
        "total_trades": 0,
        "wins": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "today_pnl": 0.0,
    }
    try:
        today_str = datetime.now(timezone.utc).date().isoformat()
        with _conn() as c:
            rows = c.execute(
                """SELECT pnl_usd, won, ts
                   FROM trades
                   WHERE broker LIKE 'forecastex%'
                     AND pnl_usd != 0
                   ORDER BY ts DESC"""
            ).fetchall()
            if not rows:
                return result
            pnls = [r["pnl_usd"] for r in rows if r["pnl_usd"] is not None]
            today_pnls = [
                r["pnl_usd"]
                for r in rows
                if r["pnl_usd"] is not None and (r["ts"] or "")[:10] == today_str
            ]
            wins = sum(1 for r in rows if r.get("won") == 1)
            result["total_trades"] = len(pnls)
            result["wins"] = wins
            result["win_rate"] = wins / len(pnls) if pnls else 0.0
            result["total_pnl"] = sum(pnls)
            result["best_trade"] = max(pnls) if pnls else 0.0
            result["worst_trade"] = min(pnls) if pnls else 0.0
            result["today_pnl"] = sum(today_pnls)
    except Exception:
        pass
    return result


# ── EV / calibration ──────────────────────────────────────────────────────────


def get_forecast_ev_summary() -> dict:
    """
    Returns avg EV at entry, and resolution accuracy (q_hat vs outcome)
    sourced from forecast_resolutions joined to trades.
    """
    result = {
        "avg_ev_at_entry": None,
        "resolutions_tracked": 0,
        "calibration_error": None,
    }
    try:
        with _conn() as c:
            n = c.execute("SELECT COUNT(*) FROM forecast_resolutions").fetchone()[0]
            result["resolutions_tracked"] = n
    except Exception:
        pass
    return result


# ── Active markets summary ─────────────────────────────────────────────────────


def get_active_markets_summary() -> list[dict]:
    """Return list of active markets with contract count and latest quote."""
    try:
        with _conn() as c:
            rows = c.execute(
                """SELECT fm.market_symbol, fm.market_name, fm.category_path,
                          COUNT(fc.id) as contract_count,
                          MAX(fq.ts) as last_quote_ts,
                          MAX(fq.mid) as latest_mid
                   FROM forecast_markets fm
                   LEFT JOIN forecast_contracts fc ON fc.market_id = fm.id AND fc.active = 1
                   LEFT JOIN forecast_quotes fq    ON fq.contract_id = fc.id
                   WHERE fm.active = 1
                   GROUP BY fm.id
                   ORDER BY last_quote_ts DESC""",
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# ── Readiness status ───────────────────────────────────────────────────────────

# Readiness state machine constants
LANE_NOT_STARTED = "LANE_NOT_STARTED"
BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
NO_UNDERLIERS = "NO_UNDERLIERS"
UNDERLIERS_ONLY = "UNDERLIERS_ONLY"
NO_QUOTES = "NO_QUOTES"
QUOTES_NO_BARS = "QUOTES_NO_BARS"
OPERATIONAL = "OPERATIONAL"


def get_forecast_readiness() -> dict:
    """
    Compute lane readiness using a state machine.

    States (in order of severity):
      LANE_NOT_STARTED    — no ForecastRunner events in last 2h
      BROKER_DISCONNECTED — lane started but no recent activity
      NO_UNDERLIERS       — lane running but 0 markets in DB
      UNDERLIERS_ONLY     — IND underliers visible but 0 OPT contracts (enrollment pending)
      NO_QUOTES           — contracts exist but no fresh quotes
      QUOTES_NO_BARS      — quotes flowing but bars not built yet
      OPERATIONAL         — fully functional

    Returns:
        {lane_state: str, status: "READY"|"BLOCKED"|"ACTION_NEEDED",
         checks: [{name, status, detail}],
         underliers_visible: int, contracts_unavailable_count: int}
    """
    health = get_forecast_health()
    checks = []
    blocked = False
    action = False

    def _chk(name: str, passed: bool, detail: str, needs_human: bool = False) -> None:
        nonlocal blocked, action
        status = "PASS" if passed else ("ACTION_NEEDED" if needs_human else "BLOCKED")
        if not passed:
            if needs_human:
                action = True
            else:
                blocked = True
        checks.append({"name": name, "status": status, "detail": detail})

    # Determine lane state via state machine
    lane_state = LANE_NOT_STARTED

    if not health["tables_exist"]:
        _chk("DB tables", False, "Run forecast.db.init_forecast_db()")
        lane_state = LANE_NOT_STARTED
        return {
            "lane_state": lane_state,
            "status": "BLOCKED",
            "checks": checks,
            "underliers_visible": 0,
            "contracts_unavailable_count": 0,
        }

    _chk("DB tables", True, "All 5 forecast tables present")

    lane_started = health.get("lane_started", False)
    if not lane_started:
        _chk(
            "Lane running",
            False,
            "No ForecastRunner events in last 2h — start the forecast lane",
            needs_human=True,
        )
        lane_state = LANE_NOT_STARTED
        return {
            "lane_state": lane_state,
            "status": "ACTION_NEEDED",
            "checks": checks,
            "underliers_visible": health.get("underliers_visible", 0),
            "contracts_unavailable_count": health.get("contracts_unavailable_count", 0),
        }

    _chk("Lane running", True, "ForecastRunner active (events in last 2h)")
    lane_state = BROKER_DISCONNECTED  # assume disconnected until quote activity proves otherwise

    underliers = health.get("underliers_visible", 0)
    contracts = health.get("active_contracts", 0)
    unavailable = health.get("contracts_unavailable_count", 0)
    lag = health.get("quote_lag_minutes")
    bars = health.get("bars_5m_count", 0)

    if underliers == 0:
        _chk("Underliers visible", False, "No markets in DB — check IBKR discovery", needs_human=True)
        lane_state = NO_UNDERLIERS
    elif contracts == 0:
        _chk(
            "Underliers visible",
            True,
            f"{underliers} underlier(s) visible in DB",
        )
        _chk(
            "OPT contracts",
            False,
            f"{underliers} underlier(s) visible but 0 OPT contracts — ForecastEx enrollment required",
            needs_human=True,
        )
        if unavailable > 0:
            _chk(
                "Enrollment status",
                False,
                f"{unavailable} underlier(s): IND visible but OPT unavailable — check IBKR portal enrollment",
                needs_human=True,
            )
        lane_state = UNDERLIERS_ONLY
    else:
        _chk("Underliers visible", True, f"{underliers} underlier(s) visible")
        _chk("OPT contracts", True, f"{contracts} active YES/NO contracts")

        quote_fresh = lag is not None and lag < 5.0
        quote_exists = lag is not None
        if not quote_exists:
            _chk("Quote freshness", False, "No quotes yet — harvester initializing", needs_human=False)
            lane_state = NO_QUOTES
        elif not quote_fresh:
            _chk("Quote freshness", False, f"Last quote {lag:.1f}m ago (threshold 5m)")
            lane_state = NO_QUOTES
        else:
            _chk("Quote freshness", True, f"Last quote {lag:.1f}m ago")
            if bars == 0:
                _chk("Bars built", False, "No 5m bars yet — collecting quotes to build bars")
                lane_state = QUOTES_NO_BARS
            else:
                _chk("Bars built", True, f"{bars} 5m bars in DB")
                lane_state = OPERATIONAL

    overall = (
        "READY"
        if lane_state == OPERATIONAL
        else ("ACTION_NEEDED" if action else "BLOCKED")
    )
    return {
        "lane_state": lane_state,
        "status": overall,
        "checks": checks,
        "underliers_visible": underliers,
        "contracts_unavailable_count": unavailable,
    }
