"""
dashboard/data/stocks.py — Data readers for the STOCKS page.

All functions read from SQLite logs/trades.db.
Pattern mirrors dashboard/data/crypto_dashboard.py exactly.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.dirname(_DASH_DIR)
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

from db import _q, _q1

_TS_NORM = "datetime(replace(substr(ts,1,19),'T',' '))"


def _cutoff(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_stock_header() -> dict:
    """
    Returns:
      connected, account_value, open_count, mode_label, pdt_count
    """
    result: dict = {
        "connected": False,
        "account_value": 0.0,
        "open_count": 0,
        "mode_label": "UNKNOWN",
        "pdt_count": 0,
    }

    # Runtime mode
    try:
        from db import _runtime_paper_flag

        result["mode_label"] = "PAPER" if _runtime_paper_flag() else "LIVE"
    except Exception:
        pass

    # Lane runtime state (stocks)
    lane = _q1(
        "SELECT health, active, connected FROM lane_runtime_state "
        "WHERE lane_id='stocks' ORDER BY id DESC LIMIT 1"
    )
    result["connected"] = bool(lane.get("connected"))

    # Open position count from DB
    rows = _q("SELECT COUNT(*) AS n FROM open_positions WHERE strategy LIKE 'stocks_%'")
    result["open_count"] = int((rows[0].get("n") or 0) if rows else 0)

    # PDT count (day trades open+close same day in last 7 days)
    pdt_rows = _q(
        """
        SELECT COUNT(DISTINCT day) AS n FROM (
            SELECT date(ts) AS day, symbol
            FROM trades
            WHERE broker='ibkr_stocks'
              AND ts >= date('now', '-7 days')
            GROUP BY day, symbol
            HAVING SUM(CASE WHEN action='BUY' THEN 1 ELSE 0 END) >= 1
               AND SUM(CASE WHEN action='SELL' THEN 1 ELSE 0 END) >= 1
        )
        """
    )
    result["pdt_count"] = int((pdt_rows[0].get("n") or 0) if pdt_rows else 0)

    # Account value — read from lane_runtime_state if persisted, else 0
    try:
        av_row = _q1(
            "SELECT buying_power_usd FROM lane_runtime_state "
            "WHERE lane_id='stocks' ORDER BY id DESC LIMIT 1"
        )
        av = float(av_row.get("buying_power_usd") or 0.0)
        if av > 0:
            result["account_value"] = av
    except Exception:
        pass

    return result


def get_stock_positions() -> list[dict]:
    """
    Open stock positions (strategy LIKE 'stocks_%') from open_positions table.
    Returns list of dicts with: symbol, qty, entry, stop, target, strategy, ts_entry.
    """
    rows = _q(
        "SELECT symbol, qty, entry, stop, target, strategy, ts_entry "
        "FROM open_positions "
        "WHERE strategy LIKE 'stocks_%' "
        "ORDER BY ts_entry DESC"
    )
    return rows or []


def get_stock_trades_today() -> list[dict]:
    """
    All stock trades today (broker='ibkr_stocks').
    Returns list of dicts with: ts, symbol, action, qty, price, pnl_usd, order_id.
    """
    today = _today_str()
    rows = _q(
        f"""
        SELECT ts, symbol, action, qty, price, pnl_usd, order_id, notes
        FROM trades
        WHERE broker='ibkr_stocks'
          AND {_TS_NORM} >= datetime(?)
        ORDER BY ts DESC
        """,
        (today + " 00:00:00",),
    )
    return rows or []


def get_stock_all_time_stats() -> dict:
    """
    All-time win/loss stats for ibkr_stocks broker.
    Returns: wins, losses, closes, profit_factor, total_pnl, by_symbol list.
    """
    result: dict = {
        "wins": 0,
        "losses": 0,
        "closes": 0,
        "profit_factor": 0.0,
        "total_pnl": 0.0,
        "by_symbol": [],
    }

    agg = _q1(
        """
        SELECT
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END) AS losses,
            COUNT(*) AS closes,
            SUM(pnl_usd) AS total_pnl,
            SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) AS gross_win,
            ABS(SUM(CASE WHEN pnl_usd < 0 THEN pnl_usd ELSE 0 END)) AS gross_loss
        FROM trades
        WHERE broker='ibkr_stocks'
          AND action='SELL'
          AND pnl_usd IS NOT NULL
        """
    )

    if agg:
        result["wins"] = int(agg.get("wins") or 0)
        result["losses"] = int(agg.get("losses") or 0)
        result["closes"] = int(agg.get("closes") or 0)
        result["total_pnl"] = float(agg.get("total_pnl") or 0.0)
        gw = float(agg.get("gross_win") or 0.0)
        gl = float(agg.get("gross_loss") or 0.0)
        result["profit_factor"] = (
            round(gw / gl, 2) if gl > 0 else (float("inf") if gw > 0 else 0.0)
        )

    # Per-symbol breakdown
    by_sym = _q(
        """
        SELECT
            symbol,
            COUNT(*) AS closes,
            SUM(pnl_usd) AS total_pnl,
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins
        FROM trades
        WHERE broker='ibkr_stocks'
          AND action='SELL'
          AND pnl_usd IS NOT NULL
        GROUP BY symbol
        ORDER BY total_pnl DESC
        """
    )
    result["by_symbol"] = by_sym or []

    return result


def get_stock_daily_pnl() -> float:
    """Sum of pnl_usd for ibkr_stocks trades closed today."""
    today = _today_str()
    row = _q1(
        f"""
        SELECT SUM(pnl_usd) AS total
        FROM trades
        WHERE broker='ibkr_stocks'
          AND action='SELL'
          AND {_TS_NORM} >= datetime(?)
        """,
        (today + " 00:00:00",),
    )
    return float(row.get("total") or 0.0) if row else 0.0


def get_stock_candidates(hours: int = 24) -> list[dict]:
    """
    Recent scan_candidates with source='stocks', newest first.
    Falls back to [] if table missing source column.
    """
    cutoff = _cutoff(hours)
    try:
        rows = _q(
            f"""
            SELECT
                COALESCE(symbol, '') AS symbol,
                COALESCE(direction, 'LONG') AS direction,
                COALESCE(composite_score, 0.0) AS score,
                COALESCE(decision, '') AS decision,
                COALESCE(notes, '') AS notes,
                ts
            FROM scan_candidates
            WHERE source='stocks'
              AND {_TS_NORM} >= datetime(replace(substr(?,1,19),'T',' '))
            ORDER BY ts DESC
            LIMIT 100
            """,
            (cutoff,),
        )
        return rows or []
    except Exception:
        return []


def get_stock_recent_trades(limit: int = 50) -> list[dict]:
    """All recent ibkr_stocks trades (both BUY and SELL), newest first."""
    rows = _q(
        """
        SELECT ts, symbol, action, qty, price, pnl_usd, order_id, notes
        FROM trades
        WHERE broker='ibkr_stocks'
        ORDER BY ts DESC
        LIMIT ?
        """,
        (limit,),
    )
    return rows or []
