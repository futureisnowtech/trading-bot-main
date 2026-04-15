"""
dashboard/data/account.py — Account balance, P&L, equity curve, drawdown, trade log.
"""

from datetime import datetime

from db import _q, _q1, LAUNCH_DATE, _runtime_paper_flag
from data.positions import get_open_positions, get_live_prices


def get_account():
    try:
        from config import ACCOUNT_SIZE

        base = float(ACCOUNT_SIZE)
    except Exception:
        base = 10000.0

    paper_flag = _runtime_paper_flag()
    paper = bool(paper_flag)
    r = _q1(
        """SELECT SUM(pnl_usd) - SUM(COALESCE(fee_usd,0)) AS net_pnl FROM trades
           WHERE ts >= ? AND paper=?
             AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper','paper_v10'))""",
        (LAUNCH_DATE, paper_flag),
    )
    realized = r.get("net_pnl") or 0.0
    unrealized = 0.0
    try:
        open_pos = get_open_positions()
        if open_pos:
            syms = [p["symbol"] for p in open_pos]
            prices = get_live_prices(syms)
            for p in open_pos:
                now = prices.get(p["symbol"], 0)
                if now <= 0:
                    continue
                qty = float(p["qty"] or 0)
                entry = float(p["entry"] or 0)
                if p["direction"] == "LONG":
                    unrealized += (now - entry) * qty
                else:
                    unrealized += (entry - now) * qty
    except Exception:
        pass
    return base + realized + unrealized, paper, base


def _paper_flag() -> int:
    return _runtime_paper_flag()


def get_today_pnl():
    today = datetime.now().strftime("%Y-%m-%d")
    r = _q1(
        """SELECT SUM(pnl_usd) v FROM trades
           WHERE ts >= ? AND paper=? AND broker NOT LIKE '%bybit%' AND pnl_usd != 0
             AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper','paper_v10'))
             AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')""",
        (today, _paper_flag()),
    )
    return r.get("v") or 0.0


def get_equity_curve():
    return _q(
        """SELECT ts, SUM(pnl_usd) OVER (ORDER BY ts) AS cum_pnl
           FROM trades
           WHERE ts >= ? AND paper=? AND broker NOT LIKE '%bybit%'
             AND pnl_usd != 0
             AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper','paper_v10'))
             AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')
           ORDER BY ts""",
        (LAUNCH_DATE, _paper_flag()),
    )


def get_drawdown():
    curve = get_equity_curve()
    if len(curve) < 2:
        return {
            "max_dd_usd": 0.0,
            "max_dd_pct": 0.0,
            "current_dd_usd": 0.0,
            "current_dd_pct": 0.0,
        }
    pnls = [r["cum_pnl"] for r in curve if r["cum_pnl"] is not None]
    if not pnls:
        return {
            "max_dd_usd": 0.0,
            "max_dd_pct": 0.0,
            "current_dd_usd": 0.0,
            "current_dd_pct": 0.0,
        }
    peak = pnls[0]
    max_dd = 0.0
    for p in pnls:
        if p > peak:
            peak = p
        dd = peak - p
        if dd > max_dd:
            max_dd = dd
    current_peak = max(pnls)
    current_val = pnls[-1]
    current_dd = max(0.0, current_peak - current_val)
    try:
        from config import ACCOUNT_SIZE

        base = float(ACCOUNT_SIZE)
    except Exception:
        base = 10000.0
    return {
        "max_dd_usd": max_dd,
        "max_dd_pct": max_dd / base * 100 if base else 0.0,
        "current_dd_usd": current_dd,
        "current_dd_pct": current_dd / base * 100 if base else 0.0,
    }


def get_trade_log(limit=50):
    return _q(
        """SELECT ts, symbol, action, qty, price, pnl_usd, fee_usd, notes
           FROM trades
           WHERE ts >= ? AND paper=? AND broker NOT LIKE '%bybit%'
             AND pnl_usd != 0
             AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper','paper_v10'))
             AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')
           ORDER BY ts DESC LIMIT ?""",
        (LAUNCH_DATE, _paper_flag(), limit),
    )
