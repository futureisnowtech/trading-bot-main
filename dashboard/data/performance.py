"""
dashboard/data/performance.py — Trade performance stats, rolling PF, per-symbol, Bayesian signals.
"""

from datetime import datetime, timedelta

import db as _db

_q = _db._q
_q1 = _db._q1
LAUNCH_DATE = _db.LAUNCH_DATE
get_current_strategy_start_date = getattr(
    _db,
    "get_current_strategy_start_date",
    lambda normalized=True: LAUNCH_DATE if normalized else LAUNCH_DATE,
)


def _metrics_start(*, current_only: bool = False) -> str:
    return (
        get_current_strategy_start_date(normalized=True)
        if current_only
        else LAUNCH_DATE
    )


def get_performance_stats(*, current_only: bool = False):
    r = _q1(
        """SELECT
            COUNT(CASE WHEN won IS NOT NULL THEN 1 END)      AS closes,
            SUM(CASE WHEN won=1 THEN 1 ELSE 0 END)           AS wins,
            SUM(CASE WHEN won=0 THEN 1 ELSE 0 END)           AS losses,
            SUM(pnl_usd - fee_usd)                           AS total_net_pnl,
            SUM(CASE WHEN won=1 THEN pnl_usd - fee_usd ELSE 0 END) AS net_wins_sum,
            SUM(CASE WHEN won=0 THEN ABS(pnl_usd - fee_usd) ELSE 0 END) AS net_losses_sum,
            SUM(fee_usd)                                     AS total_fees,
            AVG(CASE WHEN won=1 THEN pnl_usd - fee_usd END)  AS avg_win,
            AVG(CASE WHEN won=0 THEN ABS(pnl_usd - fee_usd) END) AS avg_loss
        FROM trades
        WHERE ts >= ? AND paper=0 AND broker NOT LIKE '%bybit%'
          AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))
          AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')""",
        (_metrics_start(current_only=current_only),),
    )
    closes = r.get("closes") or 0
    wins = r.get("wins") or 0
    gw = r.get("net_wins_sum") or 0.0
    gl = r.get("net_losses_sum") or 0.0
    avg_win = r.get("avg_win") or 0.0
    avg_loss = r.get("avg_loss") or 0.0
    return {
        "closes": closes,
        "wins": wins,
        "losses": r.get("losses") or 0,
        "win_rate": wins / closes * 100 if closes else 0.0,
        "total_pnl": r.get("total_net_pnl") or 0.0,
        "profit_factor": gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "gross_wins": gw,
        "gross_losses": gl,
        "total_fees": r.get("total_fees") or 0.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr_realized": avg_win / avg_loss if avg_loss > 0 else 0.0,
    }


def get_rolling_pf(days=7, *, current_only: bool = False):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    if current_only:
        try:
            floor = get_current_strategy_start_date(normalized=True)
            cutoff = max(cutoff, floor)
        except Exception:
            pass
    r = _q1(
        """SELECT
            SUM(CASE WHEN won=1 THEN pnl_usd - fee_usd ELSE 0 END) AS gw,
            SUM(CASE WHEN won=0 THEN ABS(pnl_usd - fee_usd) ELSE 0 END) AS gl,
            COUNT(CASE WHEN won IS NOT NULL THEN 1 END) AS closes,
            SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) AS wins
        FROM trades
        WHERE ts >= ? AND paper=0 AND broker NOT LIKE '%bybit%'
          AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))
          AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')""",
        (cutoff,),
    )
    gw = r.get("gw") or 0.0
    gl = r.get("gl") or 0.0
    closes = r.get("closes") or 0
    wins = r.get("wins") or 0
    return {
        "profit_factor": gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "closes": closes,
        "win_rate": wins / closes * 100 if closes else 0.0,
    }


def get_per_symbol_stats(*, current_only: bool = False):
    return _q(
        """SELECT symbol,
            COUNT(*) AS trades,
            SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(100.0 * SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
            ROUND(SUM(pnl_usd), 2) AS total_pnl,
            ROUND(AVG(pnl_usd), 2) AS avg_pnl,
            ROUND(MAX(pnl_usd), 2) AS best,
            ROUND(MIN(pnl_usd), 2) AS worst
        FROM trades
        WHERE ts >= ? AND paper=0 AND broker NOT LIKE '%bybit%'
          AND pnl_usd != 0
          AND (source IS NULL OR source NOT IN ('backtest','pre_v10_contaminated','bybit_paper'))
          AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')
        GROUP BY symbol ORDER BY total_pnl DESC""",
        (_metrics_start(current_only=current_only),),
    )


def get_signal_bayesian_stats():
    return _q("""
        SELECT signal_name, regime, fires, wins,
               ROUND(win_rate * 100, 1) AS win_rate_pct,
               ROUND(bayesian_pts, 2) AS bayesian_pts,
               ROUND(prior_pts, 2) AS prior_pts,
               ROUND(bayesian_pts - prior_pts, 2) AS pts_drift,
               ROUND(avg_pnl, 2) AS avg_pnl,
               last_updated
        FROM signal_stats WHERE regime = 'any'
        ORDER BY fires DESC, bayesian_pts DESC
    """)
