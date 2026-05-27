"""
rbi/research_loop.py — Nightly research: tests 575 signal combinations.

Runs at 2am ET every night.
Tests: singles (57) + pairs (57×56/2=1596) + triples (subset, ~500) → ~575 total tested
Realistic 575 by testing only the most promising combinations based on prior Bayesian scores.

Promotion criteria (all must pass):
  WR > 56%, PF > 1.4, Sharpe > 0.8, DD < 20%, trades > 30, p < 0.05

Promoted signals are written to rbi_research table and queued for backtest_loop.
"""

import logging
import time
import itertools
import random
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Promotion criteria
_MIN_WR     = 0.56
_MIN_PF     = 1.40
_MIN_SHARPE = 0.80
_MAX_DD     = 0.20
_MIN_TRADES = 30
_MAX_P_VAL  = 0.05

# How many combinations to test per run
_TARGET_COMBINATIONS = 575

# Lookback for research
_LOOKBACK_DAYS = 90


def _get_feature_names() -> List[str]:
    from ml.feature_builder import FEATURE_NAMES
    return FEATURE_NAMES


def _load_historical_data(symbol: str = 'BTCUSDT',
                            days: int = _LOOKBACK_DAYS) -> Optional[object]:
    """Load OHLCV DataFrame for research period."""
    try:
        from data.historical_data import get_candles
        df = get_candles(symbol, '1h', days * 24)
        return df
    except Exception:
        return None


def _build_feature_matrix(symbol: str, days: int = _LOOKBACK_DAYS):
    """
    Build feature matrix and labels from historical data.
    Returns (X, y, timestamps) or (None, None, None).
    """
    df = _load_historical_data(symbol, days)
    if df is None or len(df) < 200:
        return None, None, None

    try:
        from ml.feature_builder import build_features, to_array, FEATURE_NAMES
        from indicators.atr_regime import compute_atr_regime

        rows = []
        labels = []
        timestamps = []

        # Rolling window: build features at each 4h interval
        step = 4
        for i in range(50, len(df) - 10, step):
            window = df.iloc[max(0, i-60):i+1]
            if len(window) < 20:
                continue

            try:
                features = build_features(window, symbol)
                arr = to_array(features)
                rows.append(arr)
                timestamps.append(float(df.index[i].timestamp()))

                # Label: did price go up > 1% in next 4h?
                future_return = (df['close'].iloc[min(i+4, len(df)-1)] -
                                  df['close'].iloc[i]) / df['close'].iloc[i]
                labels.append(1 if future_return > 0.01 else 0)
            except Exception:
                continue

        if len(rows) < _MIN_TRADES:
            return None, None, None

        return np.array(rows), np.array(labels), np.array(timestamps)

    except Exception as e:
        logger.debug(f'[research] feature matrix error: {e}')
        return None, None, None


def _p_value_binomial(n: int, k: int, p0: float = 0.5) -> float:
    """
    One-tailed binomial p-value: P(X >= k | n, p0).
    Used to check if win rate is statistically significant.
    """
    from scipy import stats
    try:
        return float(stats.binom_test(k, n, p0, alternative='greater'))
    except Exception:
        # Fallback: normal approximation
        mu = n * p0
        sigma = np.sqrt(n * p0 * (1 - p0))
        z = (k - mu) / (sigma + 1e-9)
        return float(1 - 0.5 * (1 + np.sign(z) * (1 - np.exp(-z**2 / 2))))


def _evaluate_signal_combo(X: np.ndarray, y: np.ndarray,
                             feature_indices: List[int],
                             direction: str = 'LONG') -> Optional[Dict]:
    """
    Evaluate a signal combination using threshold-based entry rules.
    Returns metrics dict or None if insufficient data.
    """
    if len(feature_indices) == 0:
        return None

    from ml.feature_builder import FEATURE_NAMES

    # Build composite signal: mean of selected feature values
    # Normalize: features > 0.6 = bullish signal
    sub_X = X[:, feature_indices]
    composite = sub_X.mean(axis=1)

    # Entry when composite signal is in top 30%
    threshold = np.percentile(composite, 70)
    entry_mask = composite >= threshold

    if entry_mask.sum() < _MIN_TRADES:
        return None

    y_sub = y[entry_mask]
    wr = float(y_sub.mean())

    wins  = y_sub.sum()
    losses = len(y_sub) - wins

    # Simplified P&L (win=+1R, loss=-1R)
    pnls = np.where(y_sub == 1, 1.0, -1.0)
    pf = float(wins / (losses + 1e-9))

    if len(pnls) > 1:
        sharpe = float(np.mean(pnls) / (np.std(pnls) + 1e-9) * np.sqrt(252))
    else:
        sharpe = 0.0

    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd_series = (peak - cum) / (peak + 1e-9)
    max_dd = float(dd_series.max())

    # Binomial p-value
    p_val = _p_value_binomial(len(y_sub), int(wins))

    return {
        'n_trades': int(entry_mask.sum()),
        'win_rate': round(wr, 4),
        'profit_factor': round(pf, 4),
        'sharpe': round(sharpe, 4),
        'max_dd': round(max_dd, 4),
        'p_value': round(p_val, 4),
    }


def _passes_promotion(metrics: Dict) -> bool:
    return (
        metrics['win_rate'] >= _MIN_WR and
        metrics['profit_factor'] >= _MIN_PF and
        metrics['sharpe'] >= _MIN_SHARPE and
        metrics['max_dd'] <= _MAX_DD and
        metrics['n_trades'] >= _MIN_TRADES and
        metrics['p_value'] <= _MAX_P_VAL
    )


def _get_promising_indices(X: np.ndarray, y: np.ndarray, n: int = 30) -> List[int]:
    """
    Find top N features most correlated with the label.
    Used to focus testing on promising combinations.
    """
    correlations = []
    for i in range(X.shape[1]):
        corr = abs(float(np.corrcoef(X[:, i], y)[0, 1])) if len(X) > 5 else 0.0
        correlations.append((i, corr))
    top = sorted(correlations, key=lambda x: x[1], reverse=True)[:n]
    return [i for i, _ in top]


def run_research(symbol: str = 'BTCUSDT',
                  paper: bool = True) -> List[Dict]:
    """
    Run nightly research loop. Test ~575 signal combinations.

    Returns:
        List of promoted signal dicts (written to rbi_research table).
    """
    logger.info(f'[research] Starting nightly research for {symbol}...')
    t_start = time.time()

    X, y, timestamps = _build_feature_matrix(symbol)
    if X is None:
        logger.warning(f'[research] Insufficient data for {symbol}')
        return []

    from ml.feature_builder import FEATURE_NAMES
    n_features = len(FEATURE_NAMES)

    # Get most promising feature indices to focus testing
    promising = _get_promising_indices(X, y, n=30)

    # Generate combinations to test
    combos = []

    # Singles: all 57 features
    combos.extend([[i] for i in range(n_features)])

    # Pairs: from top 30 promising
    combos.extend([[a, b] for a, b in itertools.combinations(promising, 2)])

    # Triples: random sample from top 20
    top20 = promising[:20]
    triple_pool = list(itertools.combinations(top20, 3))
    random.seed(42)
    sampled_triples = random.sample(triple_pool, min(300, len(triple_pool)))
    combos.extend(sampled_triples)

    # Trim to target
    random.shuffle(combos)
    combos = combos[:_TARGET_COMBINATIONS]

    logger.info(f'[research] Testing {len(combos)} signal combinations on {len(X)} samples...')

    promoted = []
    tested = 0

    for combo in combos:
        metrics = _evaluate_signal_combo(X, y, combo, 'LONG')
        if metrics and _passes_promotion(metrics):
            feature_names = [FEATURE_NAMES[i] for i in combo]
            result = {
                'symbol': symbol,
                'feature_combo': feature_names,
                'feature_indices': combo,
                **metrics,
                'ts': time.time(),
                'status': 'promoted',
            }
            promoted.append(result)
        tested += 1

    elapsed = time.time() - t_start
    logger.info(f'[research] Complete: tested={tested}, promoted={len(promoted)} in {elapsed:.1f}s')

    # Write to DB
    _save_promoted(promoted)

    return promoted


def _save_promoted(promoted: List[Dict]):
    """Write promoted signal combos to rbi_research table."""
    if not promoted:
        return
    try:
        from logging_db.trade_logger import get_logger
        import json
        db = get_logger()
        for r in promoted:
            db.conn.execute("""
                INSERT OR REPLACE INTO rbi_research
                (symbol, feature_combo, win_rate, profit_factor, sharpe,
                 max_dd, n_trades, p_value, ts, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r['symbol'],
                json.dumps(r['feature_combo']),
                r['win_rate'], r['profit_factor'], r['sharpe'],
                r['max_dd'], r['n_trades'], r['p_value'],
                r['ts'], r['status'],
            ))
        db.conn.commit()
        logger.info(f'[research] Saved {len(promoted)} promoted combos to DB')
    except Exception as e:
        logger.debug(f'[research] DB save error: {e}')


def get_promoted_combos(symbol: str, limit: int = 20) -> List[Dict]:
    """Get top promoted combos from rbi_research table."""
    try:
        from logging_db.trade_logger import get_logger
        import json
        db = get_logger()
        rows = db.conn.execute("""
            SELECT symbol, feature_combo, win_rate, profit_factor, sharpe,
                   max_dd, n_trades, p_value, ts, status
            FROM rbi_research
            WHERE symbol=? AND status='promoted'
            ORDER BY win_rate DESC LIMIT ?
        """, (symbol, limit)).fetchall()

        return [
            {
                'symbol': r[0],
                'feature_combo': json.loads(r[1]),
                'win_rate': r[2], 'profit_factor': r[3], 'sharpe': r[4],
                'max_dd': r[5], 'n_trades': r[6], 'p_value': r[7],
                'ts': r[8], 'status': r[9],
            }
            for r in rows
        ]
    except Exception:
        return []
