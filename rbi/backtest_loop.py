"""
rbi/backtest_loop.py — Walk-forward backtesting for RBI promoted signal combos.

Takes promoted combos from research_loop.py and validates them more rigorously.

Backtest spec:
  63-day train / 27-day test (rolling)
  Pass criteria:
    Mean WR >= 54% across all windows
    Worst window WR >= 48%
    Worst window DD <= 18%

Results written to rbi_backtest table.
Passing combos move to rbi_incubation for live trading at 25% size.
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Backtest criteria
_MEAN_WR_MIN     = 0.54
_WORST_WR_MIN    = 0.48
_WORST_DD_MAX    = 0.18
_TRAIN_DAYS      = 63
_TEST_DAYS       = 27
_MIN_TEST_TRADES = 15


def _simulate_strategy(X: np.ndarray, y: np.ndarray,
                         feature_indices: List[int],
                         train_idx: np.ndarray,
                         test_idx: np.ndarray) -> Optional[Dict]:
    """
    Train a simple threshold model on train set, evaluate on test set.
    Returns test metrics or None.
    """
    if len(feature_indices) == 0:
        return None

    X_tr = X[train_idx]
    y_tr = y[train_idx]
    X_te = X[test_idx]
    y_te = y[test_idx]

    if len(X_tr) < 20 or len(X_te) < _MIN_TEST_TRADES:
        return None

    # Learn threshold from training set
    sub_tr = X_tr[:, feature_indices].mean(axis=1)
    threshold = np.percentile(sub_tr, 70)

    # Apply to test set
    sub_te = X_te[:, feature_indices].mean(axis=1)
    entry_mask = sub_te >= threshold

    if entry_mask.sum() < _MIN_TEST_TRADES:
        return None

    y_eval = y_te[entry_mask]
    pnls = np.where(y_eval == 1, 1.0, -1.0)

    wr    = float(y_eval.mean())
    wins  = y_eval.sum()
    losses = len(y_eval) - wins
    pf    = float(wins / (losses + 1e-9))

    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(np.maximum(cum, 0))
    dd_series = (peak - cum) / (peak + 1e-9)
    max_dd = float(dd_series.max())

    return {
        'wr': round(wr, 4),
        'pf': round(pf, 4),
        'max_dd': round(max_dd, 4),
        'n_trades': int(entry_mask.sum()),
    }


def run_backtest(combo: Dict, symbol: str = 'BTCUSDT') -> Dict:
    """
    Run walk-forward backtest for a single signal combo.

    Args:
        combo: from research_loop.get_promoted_combos()
        symbol: which symbol to backtest on

    Returns:
        {
          'passed': bool,
          'mean_wr': float,
          'worst_wr': float,
          'worst_dd': float,
          'n_windows': int,
          'reason': str,
        }
    """
    result = {
        'passed': False,
        'mean_wr': 0.0,
        'worst_wr': 0.0,
        'worst_dd': 0.0,
        'n_windows': 0,
        'reason': 'not_run',
    }

    try:
        from rbi.research_loop import _build_feature_matrix
        feature_indices = combo.get('feature_indices', [])
        if not feature_indices:
            result['reason'] = 'no_feature_indices'
            return result

        X, y, timestamps = _build_feature_matrix(symbol, days=90)
        if X is None or len(X) < 80:
            result['reason'] = f'insufficient_data: {0 if X is None else len(X)} samples'
            return result

        # Create date-indexed arrays
        ts_arr = timestamps
        min_ts = ts_arr.min()
        max_ts = ts_arr.max()

        window_metrics = []
        train_s = _TRAIN_DAYS * 86400
        test_s  = _TEST_DAYS  * 86400
        step_s  = test_s

        current_start = min_ts
        while current_start + train_s + test_s <= max_ts:
            train_end = current_start + train_s
            test_end  = train_end + test_s

            train_mask = (ts_arr >= current_start) & (ts_arr < train_end)
            test_mask  = (ts_arr >= train_end) & (ts_arr < test_end)

            train_idx = np.where(train_mask)[0]
            test_idx  = np.where(test_mask)[0]

            metrics = _simulate_strategy(X, y, feature_indices, train_idx, test_idx)
            if metrics:
                window_metrics.append(metrics)

            current_start += step_s

        if not window_metrics:
            result['reason'] = 'no_valid_windows'
            return result

        wrs = [m['wr'] for m in window_metrics]
        dds = [m['max_dd'] for m in window_metrics]

        mean_wr  = float(np.mean(wrs))
        worst_wr = float(np.min(wrs))
        worst_dd = float(np.max(dds))

        passed = (
            mean_wr  >= _MEAN_WR_MIN and
            worst_wr >= _WORST_WR_MIN and
            worst_dd <= _WORST_DD_MAX
        )

        result.update({
            'passed': passed,
            'mean_wr': round(mean_wr, 4),
            'worst_wr': round(worst_wr, 4),
            'worst_dd': round(worst_dd, 4),
            'n_windows': len(window_metrics),
            'reason': (f'passed {len(window_metrics)} windows: '
                      f'mean_wr={mean_wr:.1%}, worst_wr={worst_wr:.1%}, worst_dd={worst_dd:.1%}')
            if passed else
            (f'failed: mean_wr={mean_wr:.1%} (need {_MEAN_WR_MIN:.0%}), '
             f'worst_wr={worst_wr:.1%} (need {_WORST_WR_MIN:.0%}), '
             f'worst_dd={worst_dd:.1%} (max {_WORST_DD_MAX:.0%})')
        })

    except Exception as e:
        result['reason'] = f'error: {e}'
        logger.error(f'[backtest] error: {e}', exc_info=True)

    return result


def run_all_pending(symbol: str = 'BTCUSDT') -> int:
    """
    Run backtest on all promoted combos not yet backtested.
    Returns count of passing combos sent to incubation.
    """
    from rbi.research_loop import get_promoted_combos

    combos = get_promoted_combos(symbol, limit=50)
    if not combos:
        logger.info('[backtest] No promoted combos to test')
        return 0

    logger.info(f'[backtest] Running {len(combos)} promoted combos...')
    passed_count = 0

    for combo in combos:
        try:
            result = run_backtest(combo, symbol)
            _save_result(combo, result)

            if result['passed']:
                passed_count += 1
                _queue_for_incubation(combo, result)
                logger.info(f'[backtest] PASSED: {combo["feature_combo"][:3]} '
                           f'mean_wr={result["mean_wr"]:.1%}')
            else:
                logger.info(f'[backtest] failed: {result["reason"][:60]}')
        except Exception as e:
            logger.error(f'[backtest] combo error: {e}')

    return passed_count


def _save_result(combo: Dict, result: Dict):
    try:
        import json
        from logging_db.trade_logger import get_logger
        db = get_logger()
        db.conn.execute("""
            INSERT OR REPLACE INTO rbi_backtest
            (symbol, feature_combo, mean_wr, worst_wr, worst_dd,
             n_windows, passed, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            combo.get('symbol', ''),
            json.dumps(combo.get('feature_combo', [])),
            result['mean_wr'], result['worst_wr'], result['worst_dd'],
            result['n_windows'], 1 if result['passed'] else 0,
            time.time(),
        ))
        db.conn.commit()
    except Exception as e:
        logger.debug(f'[backtest] save error: {e}')


def _queue_for_incubation(combo: Dict, backtest_result: Dict):
    """Write passing combo to rbi_incubation table at 25% size."""
    try:
        import json
        from logging_db.trade_logger import get_logger
        db = get_logger()
        db.conn.execute("""
            INSERT OR REPLACE INTO rbi_incubation
            (symbol, feature_combo, size_pct, target_trades, actual_trades,
             wins, status, backtest_mean_wr, ts_started)
            VALUES (?, ?, 0.25, 20, 0, 0, 'incubating', ?, ?)
        """, (
            combo.get('symbol', ''),
            json.dumps(combo.get('feature_combo', [])),
            backtest_result['mean_wr'],
            time.time(),
        ))
        db.conn.commit()
        logger.info(f'[backtest] Queued for incubation: {combo.get("feature_combo", [])[:3]}')
    except Exception as e:
        logger.debug(f'[backtest] incubation queue error: {e}')
