"""
dashboard/data/performance.py — Ledgerless Performance Layer (v19.1.ARCH)
Strictly filters for trades executed after the v19.1 Ledgerless deployment.
"""

from datetime import datetime, timedelta
import db as _db

_q = _db._q
_q1 = _db._q1

# Ledgerless Cutoff: Any trade before this is considered "Legacy/Contaminated"
LEDGERLESS_CUTOFF = "2026-05-26 00:00:00"

def get_performance_stats(*, current_only: bool = False):
    """Stats based strictly on ledgerless-era trades."""
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
        WHERE ts >= ? AND paper=0 AND pnl_usd != 0""",
        (LEDGERLESS_CUTOFF,),
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
    """Rolling PF within the ledgerless window."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    # Ensure we never look back before the ledgerless deployment
    cutoff = max(cutoff, LEDGERLESS_CUTOFF)
    
    r = _q1(
        """SELECT
            SUM(CASE WHEN won=1 THEN pnl_usd - fee_usd ELSE 0 END) AS gw,
            SUM(CASE WHEN won=0 THEN ABS(pnl_usd - fee_usd) ELSE 0 END) AS gl,
            COUNT(CASE WHEN won IS NOT NULL THEN 1 END) AS closes,
            SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) AS wins
        FROM trades
        WHERE ts >= ? AND paper=0""",
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
    """Symbol stats for the ledgerless era."""
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
        WHERE ts >= ? AND paper=0 AND pnl_usd != 0
        GROUP BY symbol ORDER BY total_pnl DESC""",
        (LEDGERLESS_CUTOFF,),
    )

def get_signal_bayesian_stats():
    """Bayesian stats (these may remain broader if signal_stats table is curated)."""
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
