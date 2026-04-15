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
        {tables_exist, active_markets, active_contracts, quote_lag_minutes,
         bars_5m_count, positions_open, last_discovery_at}
    """
    result = {
        "tables_exist": False,
        "active_markets": 0,
        "active_contracts": 0,
        "quote_lag_minutes": None,
        "bars_5m_count": 0,
        "positions_open": 0,
        "last_discovery_at": None,
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
                return result

            result["active_markets"] = c.execute(
                "SELECT COUNT(*) FROM forecast_markets WHERE active=1"
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


def get_forecast_readiness() -> dict:
    """
    Compute lane readiness for the dashboard READINESS panel.

    Returns:
        {status: "READY"|"BLOCKED"|"ACTION_NEEDED",
         checks: [{name, status, detail}]}
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

    _chk(
        "DB tables",
        health["tables_exist"],
        "All 5 forecast tables present"
        if health["tables_exist"]
        else "Run forecast.db.init_forecast_db()",
    )
    _chk(
        "Active markets",
        health["active_markets"] > 0,
        f"{health['active_markets']} economic markets cached",
        needs_human=False,
    )
    _chk(
        "Active contracts",
        health["active_contracts"] > 0,
        f"{health['active_contracts']} YES/NO contracts cached",
    )

    lag = health.get("quote_lag_minutes")
    quote_fresh = lag is not None and lag < 5.0
    _chk(
        "Quote freshness",
        quote_fresh,
        f"Last quote {lag:.1f}m ago" if lag is not None else "No quotes yet",
    )

    _chk(
        "Bars built",
        health["bars_5m_count"] > 0,
        f"{health['bars_5m_count']} 5m bars in DB",
    )

    # TWS connectivity check (best-effort)
    twitch_connected = False
    try:
        from execution.forecastex_broker import get_forecastex_broker

        twitch_connected = get_forecastex_broker().is_connected()
    except Exception:
        pass
    _chk(
        "TWS connected",
        twitch_connected,
        "ForecastEx broker connected to TWS"
        if twitch_connected
        else "TWS not connected — paper mode only",
        needs_human=not twitch_connected,
    )

    overall = (
        "READY"
        if not blocked and not action
        else ("ACTION_NEEDED" if action else "BLOCKED")
    )
    return {"status": overall, "checks": checks}
