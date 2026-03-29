"""
learning/ml_trainer.py — Offline ML trainer. Runs in background, never blocks scan cycle.

Trains the LightGBM model (or sklearn GradientBoosting fallback) on rolling 90-day
trade_attribution data. Saves model artifact to logs/ml_model.pkl.

Called by:
  - launchd job (every 4h via com.algotrading.mltrainer.plist — see scripts/)
  - ml_signal.py triggers this as a background subprocess after 50 trade closes

Usage:
    python3 learning/ml_trainer.py
    python3 learning/ml_trainer.py --min-trades 30   # lower threshold for testing
"""
import argparse
import json as _json
import os
import pickle
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from config import DB_PATH

# ── Constants (must stay in sync with ml_signal.py) ───────────────────────────
LOOKBACK_DAYS = 90
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'logs', 'ml_model.pkl')

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


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _conn():
    import sqlite3
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _load_training_data(min_trades: int):
    """
    Load last LOOKBACK_DAYS of trade attributions from trade_attribution table.
    Returns (X, y, n_rows) or (None, None, 0) if insufficient data.
    """
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
            return None, None, 0

        X_rows, y_rows = [], []
        for r in rows:
            try:
                sigs = (_json.loads(r['signals_json'])
                        if isinstance(r['signals_json'], str)
                        else r['signals_json'])
            except Exception:
                continue
            row = [1.0 if sigs.get(s, False) else 0.0 for s in SIGNAL_FEATURES]
            regime_str = str(r['regime'] or 'any').lower()
            row.append(float(REGIME_MAP.get(regime_str, 1)))
            X_rows.append(row)
            y_rows.append(int(bool(r['won'])))

        n = len(X_rows)
        if n < min_trades:
            return None, None, n

        return np.array(X_rows, dtype=float), np.array(y_rows, dtype=int), n

    except Exception as e:
        print(f"[ml_trainer] load error: {e}")
        return None, None, 0


# ── Model builder (identical to ml_signal.py) ─────────────────────────────────

def _build_model():
    """Try LightGBM first, fall back to sklearn GradientBoosting, then LogReg."""
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
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(class_weight='balanced', max_iter=500, random_state=42)


# ── Main training function ─────────────────────────────────────────────────────

def run_training(min_trades: int = 30) -> bool:
    """
    Load data, train, save pkl. Returns True on success, False on failure.
    Exit code mirrors the return value (0=success, 1=failure) when called as __main__.
    """
    print(f"[ml_trainer] Starting — looking for ≥{min_trades} labeled trades "
          f"in last {LOOKBACK_DAYS} days")

    X, y, n_rows = _load_training_data(min_trades)

    if X is None:
        print(f"[ml_trainer] Insufficient data: {n_rows} trades found, need {min_trades}. "
              f"Exiting without writing model.")
        return False

    # Need both wins and losses to train a binary classifier
    if y.sum() == 0 or y.sum() == len(y):
        print(f"[ml_trainer] Only one class in {n_rows} trades "
              f"(wins={y.sum()}, losses={len(y)-y.sum()}) — cannot train. Exiting.")
        return False

    try:
        clf = _build_model()
        t0 = time.time()
        clf.fit(X, y)
        elapsed = time.time() - t0

        win_rate = y.mean()

        # Feature importances
        importances = getattr(clf, 'feature_importances_', None)
        top_features = []
        if importances is not None:
            all_cols = SIGNAL_FEATURES + ['regime_encoded']
            ranked = sorted(zip(all_cols, importances), key=lambda x: x[1], reverse=True)
            top_features = [(name, round(score, 4)) for name, score in ranked[:5]]

        # Save artifact
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        payload = {
            'model': clf,
            'feature_cols': SIGNAL_FEATURES + ['regime_encoded'],
            'trained_at': time.time(),
            'n_trades': n_rows,
            'win_rate': win_rate,
            'model_type': type(clf).__name__,
        }
        with open(MODEL_PATH, 'wb') as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)

        print(f"[ml_trainer] Trained on {n_rows} trades | WR={win_rate:.1%} | "
              f"model={type(clf).__name__} | elapsed={elapsed:.1f}s")
        if top_features:
            print(f"[ml_trainer] top features: {top_features}")
        print(f"[ml_trainer] Saved to {MODEL_PATH}")
        return True

    except Exception as e:
        print(f"[ml_trainer] Training error: {e}")
        return False


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Offline ML trainer — saves model to logs/ml_model.pkl'
    )
    parser.add_argument(
        '--min-trades', type=int, default=30,
        help='Minimum number of labeled trades required to train (default: 30)'
    )
    args = parser.parse_args()

    success = run_training(min_trades=args.min_trades)
    sys.exit(0 if success else 1)
