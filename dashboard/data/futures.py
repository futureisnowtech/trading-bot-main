"""
dashboard/data/futures.py — MES futures state, trades, P&L, all-time stats.
"""

import json
from datetime import datetime

from db import _q, _q1


def get_mes_state() -> dict:
    row = _q1(
        "SELECT ts, message FROM system_events WHERE source='mes_state' ORDER BY rowid DESC LIMIT 1"
    )
    if not row:
        return {}
    try:
        state = json.loads(row.get("message", "{}"))
        state["ts"] = row.get("ts", "")
        return state
    except Exception:
        return {}


def get_mes_trades_today() -> list:
    today = datetime.now().strftime("%Y-%m-%d")
    return _q(
        "SELECT ts, action, qty, price, pnl_usd, notes FROM trades WHERE ts >= ? AND symbol = 'MES' ORDER BY ts DESC",
        (today,),
    )


def get_mes_daily_pnl() -> float:
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1(
        "SELECT SUM(pnl_usd) v FROM trades WHERE ts >= ? AND symbol = 'MES' AND pnl_usd != 0",
        (today,),
    )
    return r.get("v") or 0.0


def get_mes_all_time_stats() -> dict:
    r = _q1("""
        SELECT COUNT(*) AS closes,
               SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(pnl_usd) AS total_pnl,
               SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) AS gross_wins,
               SUM(CASE WHEN pnl_usd < 0 THEN ABS(pnl_usd) ELSE 0 END) AS gross_losses
        FROM trades WHERE symbol='MES' AND pnl_usd!=0
          AND ts >= '2026-04-02'
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper')
    """)
    closes = r.get("closes") or 0
    wins = r.get("wins") or 0
    gw = r.get("gross_wins") or 0.0
    gl = r.get("gross_losses") or 0.0
    return {
        "closes": closes,
        "wins": wins,
        "win_rate": wins / closes * 100 if closes else 0.0,
        "total_pnl": r.get("total_pnl") or 0.0,
        "profit_factor": gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0),
    }
