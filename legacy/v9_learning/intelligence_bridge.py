"""
learning/intelligence_bridge.py — Connects backtest results to the live signal stats table.

Extracts the same per-trade attribution from backtest runs as live trades produce.
Both feed into the same signal_stats table (source='backtest' or 'combined').
Bayesian priors become evidence-backed before the first live trade runs.

Called by:
  - backtesting/backtest_engine.py after each run
  - scripts/seed_intelligence.py for the initial seed run
"""
import json
import os
import sys
import hashlib
from datetime import datetime, timezone
from typing import Optional
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from learning.signal_performance import record_trade_attribution, SIGNAL_PRIOR_PTS
from config import DB_PATH
import sqlite3


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def ingest_backtest_trades(
    trades_df: pd.DataFrame,
    symbol: str,
    strategy_name: str,
    strategy_variant: str,
    params: dict,
    timeframe: str = 'ONE_MINUTE',
    regime_col: Optional[str] = None,
) -> int:
    """
    Ingest a backtest trades DataFrame into the attribution system.

    trades_df must have columns:
        entry_ts, exit_ts, entry_price, exit_price, pnl_usd, fee_usd, won
        Optional: regime, signals_json (pre-extracted), hold_minutes

    Returns number of trades ingested.
    """
    if trades_df is None or trades_df.empty:
        return 0

    ingested = 0
    for _, row in trades_df.iterrows():
        try:
            # Extract signals — if pre-computed use them, else infer from price action
            if 'signals_json' in row and row['signals_json']:
                signals = json.loads(row['signals_json'])
            else:
                signals = _infer_signals_from_row(row, params, strategy_name=strategy_name)

            regime = str(row.get(regime_col or 'regime', 'unknown') or 'unknown').lower()
            won = bool(row.get('won', (row.get('pnl_usd', 0) - row.get('fee_usd', 0)) > 0))
            pnl = float(row.get('pnl_usd', 0))
            fee = float(row.get('fee_usd', 0))
            pnl_pct = float(row.get('pnl_pct', 0))
            hold = float(row.get('hold_minutes', 0))

            record_trade_attribution(
                symbol=symbol,
                strategy=strategy_name,
                regime=regime,
                signals=signals,
                won=won,
                pnl_usd=pnl,
                pnl_pct=pnl_pct,
                fee_usd=fee,
                entry_price=float(row.get('entry_price', 0)),
                exit_price=float(row.get('exit_price', 0)),
                entry_ts=str(row.get('entry_ts', '')),
                exit_ts=str(row.get('exit_ts', '')),
                exit_reason=str(row.get('exit_reason', 'backtest_exit')),
                hold_minutes=hold,
                source='backtest',
                paper=True,
                trade_ref=f"bt_{symbol}_{strategy_name}_{ingested}",
            )
            ingested += 1
        except Exception as e:
            print(f"[intelligence_bridge] ingest error row {ingested}: {e}")
            continue

    return ingested


def _infer_signals_from_row(row: pd.Series, params: dict,
                             strategy_name: str = '') -> dict[str, bool]:
    """
    Infer signal states from a backtest trade row when explicit signals aren't stored.
    Uses column values that the backtest engine records.
    Strategy-aware: returns strategy-specific signals in addition to base signals.
    """
    def _f(key, default=0.0):
        v = row.get(key, default)
        try:
            return float(v) if v is not None else default
        except Exception:
            return default

    def _b(key, default=False):
        v = row.get(key, default)
        return bool(v) if v is not None else default

    # Base signals (crypto MACD path — applies to all strategies as defaults)
    base = {
        'macd_consensus':       _b('macd_signal'),
        'williams_r':           _f('williams_r', 0) <= -80,
        'momentum_volume':      _f('vol_spike', 1) > 1.3,
        'squeeze_fired':        _b('squeeze_fired'),
        'rv_expansion':         _f('rv_ratio', 0) >= 1.3,
        'kalman_deviation':     _f('kalman_dev', 0) <= -0.01,
        'avwap_deviation':      _f('avwap_dev', 0) <= -0.005,
        'ou_halflife':          3 <= _f('ou_halflife_minutes', 0) <= 60,
        'kyle_lambda':          0 < _f('kyle_lambda_pct', 100) <= 30,
        'supertrend_bullish':   _b('supertrend_bullish'),
        'wavetrend_cross':      _b('wt_oversold_cross'),
        'ichimoku_bullish':     _b('cloud_bullish'),
        'fisher_cross_up':      _b('fisher_cross_up'),
        'lrsi_oversold':        _f('lrsi', 0.5) < 0.15,
        'wae_bullish_exploding': _b('wae_bullish') and _b('wae_exploding'),
        'wae_bullish':          _b('wae_bullish') and not _b('wae_exploding'),
        'chop_trending':        _b('chop_trending'),
        'lrsi_mild_oversold':   0.15 <= _f('lrsi', 0.5) < 0.25,
        'tradingview_signal':   False,  # TV signals not present in backtest
    }

    # Strategy-specific signal enrichment
    if 'mean_reversion' in strategy_name:
        base.update({
            'kalman_deviation':  _f('kalman_dev', 0) <= -0.008,
            'avwap_deviation':   _f('avwap_dev', 0) <= -0.005,
            'bb_proximity':      _f('bb_proximity_pct', 1.0) <= 0.012,
            'autocorr_negative': _f('autocorr_ret', 0.0) < 0.0,
            'mean_rev_kalman':   _f('kalman_dev', 0) <= -0.008,
            'macd_consensus':    False,  # not used in mean reversion
        })
    elif strategy_name == 'futures_scalper' or 'futures' in strategy_name:
        base.update({
            'orb_breakout_long':  _b('orb_long'),
            'orb_breakout_short': _b('orb_short'),
            'htf_bullish_bias':   _b('htf_bullish'),
            'htf_bearish_bias':   _b('htf_bearish'),
            'futures_adx_trend':  _f('adx', 0) > 18,
            'momentum_volume':    _f('vol_spike', 1) > 1.2,
            'macd_consensus':     False,
        })
    elif 'perp' in strategy_name:
        base.update({
            'perp_long_breakout':    _b('perp_long'),
            'perp_short_breakout':   _b('perp_short'),
            'rsi_bullish_momentum':  _f('rsi', 50) > 55,
            'rsi_bearish_momentum':  _f('rsi', 50) < 45,
            'funding_rate_favorable': _b('funding_favorable'),
            'momentum_volume':       _f('vol_spike', 1) > 1.2,
            'macd_consensus':        False,
        })
    elif 'equity' in strategy_name:
        base.update({
            'equity_macd_positive': _f('macd_hist', 0) > 0,
            'equity_kst_cross':     _b('kst_cross_up'),
            'equity_vwap_above':    _b('above_vwap'),
            'equity_vol_spike':     _f('vol_spike', 1) >= 1.5,
            'equity_rsi_range':     35 <= _f('rsi', 50) <= 65,
            'macd_consensus':       _f('macd_hist', 0) > 0,
        })

    return base


def archive_backtest_result(
    strategy_name: str,
    symbol: str,
    params: dict,
    stats: dict,
    passed: bool,
    variant: str = '',
    timeframe: str = 'ONE_MINUTE',
    period_start: str = '',
    period_end: str = '',
    notes: str = '',
) -> int:
    """
    Store a backtest result summary in backtest_results table.
    Returns the row ID.
    """
    param_hash = hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
    now = datetime.now(timezone.utc).isoformat()

    with _conn() as c:
        cur = c.execute("""
            INSERT INTO backtest_results
                (strategy_name, variant, symbol, timeframe,
                 period_start, period_end, param_hash, params_json,
                 total_trades, win_rate, total_pnl, sharpe,
                 max_drawdown, avg_pnl, profit_factor,
                 passed, archived_at, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            strategy_name, variant, symbol, timeframe,
            period_start, period_end, param_hash, json.dumps(params),
            stats.get('total_trades', 0),
            stats.get('win_rate'),
            stats.get('total_pnl'),
            stats.get('sharpe'),
            stats.get('max_drawdown'),
            stats.get('avg_pnl'),
            stats.get('profit_factor'),
            int(passed), now, notes,
        ))
        return cur.lastrowid


def get_backtest_history(strategy_name: str = None, symbol: str = None) -> list[dict]:
    """Retrieve backtest result history."""
    with _conn() as c:
        if strategy_name and symbol:
            rows = c.execute("""
                SELECT * FROM backtest_results
                WHERE strategy_name=? AND symbol=?
                ORDER BY archived_at DESC LIMIT 50
            """, (strategy_name, symbol)).fetchall()
        elif strategy_name:
            rows = c.execute("""
                SELECT * FROM backtest_results WHERE strategy_name=?
                ORDER BY archived_at DESC LIMIT 50
            """, (strategy_name,)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM backtest_results ORDER BY archived_at DESC LIMIT 100
            """).fetchall()
    return [dict(r) for r in rows]


def get_best_backtest(strategy_name: str, symbol: str) -> Optional[dict]:
    """Return the highest-Sharpe archived backtest for a strategy/symbol."""
    try:
        with _conn() as c:
            row = c.execute("""
                SELECT * FROM backtest_results
                WHERE strategy_name=? AND symbol=? AND passed=1
                ORDER BY sharpe DESC LIMIT 1
            """, (strategy_name, symbol)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
