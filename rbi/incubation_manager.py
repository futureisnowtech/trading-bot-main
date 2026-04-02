"""
rbi/incubation_manager.py — Live incubation of RBI-promoted strategies.

Incubation parameters:
  - Position size: 25% of normal (conservative while proving edge)
  - Target: 20 live trades before graduation decision
  - Graduate if: WR >= 50% AND PF >= 1.20
  - Kill if: drawdown > 2× backtest max DD

Graduation → production: signal added to technical scoring components.
Production review: every 30 days. Demote if WR dropped > 8%.
"""

import logging
import time
import json
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_INCUBATION_SIZE_PCT = 0.25   # 25% of normal size
_TARGET_TRADES       = 20
_GRADUATE_WR         = 0.50
_GRADUATE_PF         = 1.20
_KILL_DD_MULT        = 2.0    # kill if DD > 2× backtest max DD
_PRODUCTION_REVIEW_DAYS = 30
_DEMOTE_WR_DROP      = 0.08   # demote if WR dropped > 8%


def get_active_incubations() -> List[Dict]:
    """Return all combos currently in incubation."""
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        rows = db.conn.execute("""
            SELECT id, symbol, feature_combo, size_pct, target_trades,
                   actual_trades, wins, status, backtest_mean_wr, ts_started
            FROM rbi_incubation
            WHERE status = 'incubating'
        """).fetchall()

        return [
            {
                'id': r[0], 'symbol': r[1],
                'feature_combo': json.loads(r[2]),
                'size_pct': r[3], 'target_trades': r[4],
                'actual_trades': r[5], 'wins': r[6],
                'status': r[7], 'backtest_mean_wr': r[8],
                'ts_started': r[9],
            }
            for r in rows
        ]
    except Exception as e:
        logger.debug(f'[incubation] load error: {e}')
        return []


def record_incubation_trade(incubation_id: int, won: bool, pnl_usd: float):
    """Record a trade result for an incubating strategy."""
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        db.conn.execute("""
            UPDATE rbi_incubation
            SET actual_trades = actual_trades + 1,
                wins = wins + ?,
                ts_last_trade = ?
            WHERE id = ?
        """, (1 if won else 0, time.time(), incubation_id))
        db.conn.commit()
        _check_graduation_or_kill(incubation_id)
    except Exception as e:
        logger.debug(f'[incubation] record error: {e}')


def _check_graduation_or_kill(incubation_id: int):
    """Evaluate graduation or kill criteria after each trade."""
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        row = db.conn.execute("""
            SELECT symbol, feature_combo, actual_trades, wins,
                   backtest_mean_wr, status
            FROM rbi_incubation WHERE id=?
        """, (incubation_id,)).fetchone()

        if not row:
            return

        symbol, combo_json, actual, wins, bt_wr, status = row
        if status != 'incubating':
            return

        if actual < _TARGET_TRADES:
            return   # not enough trades yet

        wr = wins / actual if actual > 0 else 0
        pnl_rows = db.conn.execute("""
            SELECT pnl_usd FROM trades
            WHERE symbol LIKE ? AND action='SELL'
            ORDER BY ts DESC LIMIT ?
        """, (f'%{symbol.replace("USDT","")}%', actual)).fetchall()

        pnls = [float(r[0]) for r in pnl_rows]
        if pnls:
            import numpy as np
            wins_usd  = sum(p for p in pnls if p > 0)
            losses_usd = sum(abs(p) for p in pnls if p <= 0)
            pf = wins_usd / (losses_usd + 1e-9)

            # Drawdown check
            cum = np.cumsum(pnls)
            peak = np.maximum.accumulate(np.maximum(cum, 0))
            dd = float((peak - cum).max() / (peak.max() + 1e-9))
        else:
            pf = 0
            dd = 0

        max_allowed_dd = (1 - bt_wr) * _KILL_DD_MULT

        if dd > max_allowed_dd:
            _update_status(db, incubation_id, 'killed',
                          f'DD {dd:.1%} > {max_allowed_dd:.1%} (2× backtest max)')
            logger.warning(f'[incubation] KILLED id={incubation_id}: DD breach')
            return

        if wr >= _GRADUATE_WR and pf >= _GRADUATE_PF:
            _update_status(db, incubation_id, 'graduated',
                          f'WR={wr:.1%} PF={pf:.2f} after {actual} trades')
            logger.info(f'[incubation] GRADUATED id={incubation_id}: WR={wr:.1%}')
        else:
            logger.info(f'[incubation] id={incubation_id}: {actual} trades, '
                       f'WR={wr:.1%} (need {_GRADUATE_WR:.0%}), PF={pf:.2f}')

    except Exception as e:
        logger.debug(f'[incubation] check error: {e}')


def _update_status(db, incubation_id: int, status: str, reason: str):
    db.conn.execute("""
        UPDATE rbi_incubation
        SET status=?, status_reason=?, ts_decided=?
        WHERE id=?
    """, (status, reason, time.time(), incubation_id))
    db.conn.commit()


def get_size_multiplier(symbol: str, feature_combo: List[str]) -> float:
    """
    Return size multiplier for a signal combo.
    Returns 0.25 if in incubation, 1.0 if graduated (in production), 0.0 if killed.
    """
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        row = db.conn.execute("""
            SELECT status, size_pct FROM rbi_incubation
            WHERE symbol=? AND feature_combo=?
            ORDER BY ts_started DESC LIMIT 1
        """, (symbol, json.dumps(sorted(feature_combo)))).fetchone()

        if not row:
            return 1.0  # not tracked, use full size

        status, size_pct = row
        if status == 'incubating':
            return float(size_pct)
        elif status == 'graduated':
            return 1.0
        else:
            return 0.0  # killed or recycled
    except Exception:
        return 1.0


def run_production_review():
    """
    Monthly review of graduated strategies.
    Demote if WR dropped > 8% from incubation WR.
    """
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        rows = db.conn.execute("""
            SELECT id, symbol, feature_combo, backtest_mean_wr, ts_decided
            FROM rbi_incubation
            WHERE status='graduated'
        """).fetchall()

        cutoff = time.time() - _PRODUCTION_REVIEW_DAYS * 86400

        for row in rows:
            id_, symbol, combo_json, bt_wr, ts_decided = row
            if ts_decided and ts_decided > cutoff:
                continue  # reviewed recently

            # Get recent trades for this strategy
            recent = db.conn.execute("""
                SELECT won FROM trades
                WHERE symbol LIKE ? AND action='SELL' AND ts > ?
            """, (f'%{symbol.replace("USDT","")}%', cutoff)).fetchall()

            if len(recent) < 10:
                continue

            recent_wr = sum(1 for r in recent if r[0]) / len(recent)

            if recent_wr < (bt_wr - _DEMOTE_WR_DROP):
                _update_status(db, id_, 'demoted',
                              f'WR dropped from {bt_wr:.1%} to {recent_wr:.1%}')
                logger.warning(f'[production_review] DEMOTED id={id_}: '
                              f'WR {bt_wr:.1%}→{recent_wr:.1%}')

    except Exception as e:
        logger.debug(f'[production_review] error: {e}')


def get_incubation_summary() -> Dict:
    """Summary stats for dashboard."""
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        rows = db.conn.execute("""
            SELECT status, COUNT(*) FROM rbi_incubation GROUP BY status
        """).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}
