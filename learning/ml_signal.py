"""
learning/ml_signal.py — Rolling ML signal layer.

Trains a LightGBM (or sklearn GradientBoosting fallback) classifier on the
last 90 days of trade_attribution data. Features = the 19 signal flags + regime
encoding. Target = won (1/0).

Returns P(win) for the current market_data snapshot. Used as a gate before
the 3-agent debate: if p_win < ML_SIGNAL_MIN_PROB, skip debate entirely.

Retrains every RETRAIN_INTERVAL new trades (default 50).

Why this helps:
- Closes the backtest-to-live gap: the ML model is trained on LIVE trade outcomes,
  not math-only backtests. It learns the system's actual win conditions.
- Replaces the conviction scoring soup with a single evidence-based probability.
- Same feature pipeline at train time and inference time (no leakage).

Usage:
    from learning.ml_signal import get_ml_signal, maybe_retrain
    p_win, confidence_label = get_ml_signal(market_data)
    maybe_retrain()   # call after every trade close
"""
import os
import sys
import time
import sqlite3
import numpy as np
from typing import Optional, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

RETRAIN_INTERVAL = 50      # retrain after every N new trade closes
ML_MIN_TRAIN_SAMPLES = 30  # don't train if fewer than this many labeled trades
LOOKBACK_DAYS = 90         # only use last N days of attribution data

# ── Module state ──────────────────────────────────────────────────────────────
_model = None
_feature_cols: list = []
_last_trade_count: int = 0
_last_retrain_ts: float = 0
_RETRAIN_COOLDOWN = 300    # at most once per 5 minutes even if many trades close fast

# ── Signal feature columns (same order as market_data_to_signals in dynamic_weights.py)
SIGNAL_FEATURES = [
    'macd_consensus',
    'williams_r',
    'momentum_volume',
    'squeeze_fired',
    'rv_expansion',
    'kalman_deviation',
    'avwap_deviation',
    'ou_halflife',
    'kyle_lambda',
    'supertrend_bullish',
    'wavetrend_cross',
    'ichimoku_bullish',
    'fisher_cross_up',
    'lrsi_oversold',
    'wae_bullish_exploding',
    'wae_bullish',
    'chop_trending',
    'lrsi_mild_oversold',
    'tradingview_signal',
]

REGIME_MAP = {'trending': 0, 'ranging': 1, 'volatile': 2, 'any': 1}


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _load_training_data() -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Load last LOOKBACK_DAYS of trade attributions from trade_attribution table.
    Schema: trade_ref, signals_json (dict of {signal_name: bool}), regime, won.
    Returns (X, y) numpy arrays, or (None, None) if insufficient data.
    """
    import json as _json
    try:
        cutoff = time.strftime('%Y-%m-%dT%H:%M:%S',
                               time.gmtime(time.time() - LOOKBACK_DAYS * 86400))
        with _conn() as c:
            rows = c.execute("""
                SELECT signals_json, regime, won
                FROM trade_attribution
                WHERE won IS NOT NULL
                  AND signals_json IS NOT NULL
                  AND created_at > ?
            """, (cutoff,)).fetchall()

        if not rows:
            return None, None

        X_rows, y_rows = [], []
        for r in rows:
            try:
                sigs = _json.loads(r['signals_json']) if isinstance(r['signals_json'], str) else r['signals_json']
            except Exception:
                continue
            row = [1.0 if sigs.get(s, False) else 0.0 for s in SIGNAL_FEATURES]
            regime_str = str(r['regime'] or 'any').lower()
            row.append(float(REGIME_MAP.get(regime_str, 1)))
            X_rows.append(row)
            y_rows.append(int(bool(r['won'])))

        if len(X_rows) < ML_MIN_TRAIN_SAMPLES:
            return None, None

        return np.array(X_rows, dtype=float), np.array(y_rows, dtype=int)

    except Exception as e:
        print(f"[ml_signal] load error: {e}")
        return None, None


def _get_trade_count() -> int:
    """Fast row count for trade_attribution."""
    try:
        with _conn() as c:
            return c.execute("SELECT COUNT(*) FROM trade_attribution WHERE won IS NOT NULL").fetchone()[0]
    except Exception:
        return 0


def _build_model():
    """Try LightGBM first, fall back to sklearn GradientBoosting."""
    try:
        import lightgbm as lgb
        return lgb.LGBMClassifier(
            n_estimators=100, learning_rate=0.05, max_depth=4,
            num_leaves=15, min_child_samples=5, subsample=0.8,
            class_weight='balanced', random_state=42, verbose=-1,
        )
    except ImportError:
        pass
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        return GradientBoostingClassifier(
            n_estimators=80, learning_rate=0.05, max_depth=3,
            subsample=0.8, random_state=42,
        )
    except ImportError:
        pass
    # Last resort: logistic regression
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(class_weight='balanced', max_iter=500, random_state=42)


def train() -> bool:
    """Train the ML model on current attribution data. Returns True on success."""
    global _model, _feature_cols, _last_trade_count, _last_retrain_ts

    X, y = _load_training_data()
    if X is None or len(X) < ML_MIN_TRAIN_SAMPLES:
        print(f"[ml_signal] insufficient data ({0 if X is None else len(X)} trades) — using priors")
        return False

    # Check class balance — need both wins and losses
    if y.sum() == 0 or y.sum() == len(y):
        print("[ml_signal] only one class in training data — skipping")
        return False

    try:
        clf = _build_model()
        clf.fit(X, y)
        _model = clf
        _feature_cols = SIGNAL_FEATURES + ['regime_encoded']
        _last_trade_count = _get_trade_count()
        _last_retrain_ts = time.time()
        win_rate = y.mean()
        print(f"[ml_signal] ✅ trained on {len(X)} trades | WR={win_rate:.1%} | "
              f"model={type(clf).__name__}")
        return True
    except Exception as e:
        print(f"[ml_signal] train error: {e}")
        return False


def maybe_retrain() -> None:
    """
    Call after every trade close. Retrains if RETRAIN_INTERVAL new trades
    have accumulated since last training. Rate-limited to RETRAIN_COOLDOWN.
    Non-blocking (returns immediately if not needed).
    """
    global _last_trade_count

    if time.time() - _last_retrain_ts < _RETRAIN_COOLDOWN:
        return

    current_count = _get_trade_count()
    if current_count - _last_trade_count >= RETRAIN_INTERVAL:
        train()


def get_ml_signal(market_data: dict) -> Tuple[float, str]:
    """
    Compute P(win) for the current market_data snapshot.

    Returns:
        (p_win: float 0-1, confidence_label: str)
        confidence_label: 'strong' (>0.65), 'moderate' (0.55-0.65), 'weak' (<0.55)

    Falls back to (0.5, 'no_model') if model not trained yet.
    """
    global _model

    # Lazy train on first call if model not loaded
    if _model is None:
        train()

    if _model is None:
        return 0.5, 'no_model'

    # Build feature vector from market_data
    try:
        from learning.dynamic_weights import market_data_to_signals
        signals = market_data_to_signals(market_data)
    except Exception:
        signals = {}

    row = [1.0 if signals.get(s, False) else 0.0 for s in SIGNAL_FEATURES]
    regime_str = str(market_data.get('regime', 'any') or 'any').lower()
    row.append(float(REGIME_MAP.get(regime_str, 1)))

    try:
        X = np.array([row], dtype=float)
        proba = _model.predict_proba(X)
        # proba shape: (1, 2) for binary classifier — index 1 = P(win)
        p_win = float(proba[0][1]) if proba.shape[1] > 1 else float(proba[0][0])
        p_win = max(0.0, min(1.0, p_win))

        if p_win >= 0.65:
            label = 'strong'
        elif p_win >= 0.55:
            label = 'moderate'
        else:
            label = 'weak'

        return round(p_win, 3), label

    except Exception as e:
        print(f"[ml_signal] inference error: {e}")
        return 0.5, 'error'


def get_feature_importance() -> dict:
    """Return feature importances for dashboard/debugging."""
    if _model is None:
        return {}
    try:
        importances = getattr(_model, 'feature_importances_', None)
        if importances is None:
            return {}
        cols = SIGNAL_FEATURES + ['regime_encoded']
        return dict(sorted(
            zip(cols, importances.tolist()),
            key=lambda x: x[1], reverse=True
        ))
    except Exception:
        return {}
