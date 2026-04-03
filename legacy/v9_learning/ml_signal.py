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
import pickle
import subprocess
import sys
import time
import sqlite3
import numpy as np
from typing import Optional, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH
from logging_db.trade_logger import log_event

# Path to offline-trained model artifact (written by ml_trainer.py)
_MODEL_PKL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'logs', 'ml_model.pkl')
_MODEL_PKL_MAX_AGE_SECONDS = 6 * 3600  # treat pkl as stale after 6h

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

# Same extended feature list as ml_trainer.py
ALL_FEATURES = SIGNAL_FEATURES + ['regime_encoded', 'hour_et_norm', 'session_norm']


def _get_current_time_features() -> tuple:
    """Return (hour_et_norm, session_norm) for current Eastern Time."""
    try:
        import datetime
        import pytz
        now = datetime.datetime.now(pytz.timezone('US/Eastern'))
        hour = now.hour
        if hour < 8:
            session = 0
        elif hour < 13:
            session = 1
        elif hour < 18:
            session = 2
        else:
            session = 3
        return hour / 23.0, session / 3.0
    except Exception:
        return 0.5, 0.5


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
                SELECT signals_json, regime, won, entry_ts
                FROM trade_attribution
                WHERE won IS NOT NULL
                  AND signals_json IS NOT NULL
                  AND created_at > ?
                  AND source = 'live'
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
            # Time features
            try:
                import datetime, pytz as _pytz
                _ts = str(r['entry_ts'] or '')
                _dt = datetime.datetime.fromisoformat(_ts)
                if _dt.tzinfo is None:
                    _dt = _dt.replace(tzinfo=_pytz.UTC)
                _et = _dt.astimezone(_pytz.timezone('US/Eastern'))
                _h = _et.hour
                _s = 0 if _h < 8 else (1 if _h < 13 else (2 if _h < 18 else 3))
                row.append(_h / 23.0)
                row.append(_s / 3.0)
            except Exception:
                row.append(0.5)
                row.append(0.5)
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
            return c.execute("SELECT COUNT(*) FROM trade_attribution WHERE won IS NOT NULL AND source = 'live'").fetchone()[0]
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


def _load_from_pkl() -> bool:
    """
    Try to load a pre-trained model from logs/ml_model.pkl (written by ml_trainer.py).
    Returns True if a fresh-enough model was loaded successfully.
    """
    global _model, _feature_cols, _last_retrain_ts

    if not os.path.exists(_MODEL_PKL_PATH):
        return False

    try:
        age = time.time() - os.path.getmtime(_MODEL_PKL_PATH)
        if age > _MODEL_PKL_MAX_AGE_SECONDS:
            print(f"[ml_signal] pkl exists but is {age/3600:.1f}h old (max {_MODEL_PKL_MAX_AGE_SECONDS/3600:.0f}h) — will retrain")
            return False

        with open(_MODEL_PKL_PATH, 'rb') as f:
            payload = pickle.load(f)

        _model = payload['model']
        _feature_cols = payload.get('feature_cols', SIGNAL_FEATURES + ['regime_encoded'])
        _last_retrain_ts = payload.get('trained_at', time.time())

        n = payload.get('n_trades', '?')
        wr = payload.get('win_rate', 0.0)
        mtype = payload.get('model_type', type(_model).__name__)
        print(f"[ml_signal] Loaded pkl model — {n} trades | WR={wr:.1%} | model={mtype} | age={age/60:.0f}min")
        return True

    except Exception as e:
        print(f"[ml_signal] pkl load error: {e}")
        return False


def train() -> bool:
    """
    Train the ML model on current attribution data. Returns True on success.

    Execution order:
    1. Try loading from logs/ml_model.pkl (fast, non-blocking)
    2. If pkl missing or stale: fall back to inline training (existing behaviour)
    """
    global _model, _feature_cols, _last_trade_count, _last_retrain_ts

    # Fast path: use pre-trained pkl from background trainer
    if _load_from_pkl():
        _last_trade_count = _get_trade_count()
        return True

    # Slow path: inline train (blocks for 3-10s — acceptable at startup only)
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
        _feature_cols = ALL_FEATURES
        _last_trade_count = _get_trade_count()
        _last_retrain_ts = time.time()
        win_rate = y.mean()
        print(f"[ml_signal] trained on {len(X)} trades | WR={win_rate:.1%} | "
              f"model={type(clf).__name__}")

        # Log feature importances after fit
        importances = getattr(clf, 'feature_importances_', None)
        if importances is not None:
            all_cols = ALL_FEATURES
            ranked = sorted(zip(all_cols, importances), key=lambda x: x[1], reverse=True)
            top5 = [(name, round(score, 4)) for name, score in ranked[:5]]
            print(f"[ml_signal] Top features: {top5}")
            try:
                log_event('INFO', 'ml_signal', f"Top features: {top5}")
            except Exception:
                pass  # non-fatal — DB may not be initialised yet

        return True
    except Exception as e:
        print(f"[ml_signal] train error: {e}")
        return False


def maybe_retrain() -> None:
    """
    Call after every trade close. When RETRAIN_INTERVAL new trades have accumulated,
    launches ml_trainer.py as a background subprocess — scan cycle is never blocked.
    Rate-limited to RETRAIN_COOLDOWN to prevent duplicate spawns on rapid closes.
    """
    global _last_trade_count

    if time.time() - _last_retrain_ts < _RETRAIN_COOLDOWN:
        return

    current_count = _get_trade_count()
    if current_count - _last_trade_count >= RETRAIN_INTERVAL:
        _trigger_background_retrain()
        # Optimistically advance the counter so we don't re-trigger immediately.
        # The pkl reload in the next get_ml_signal() call will pick up the fresh model.
        _last_trade_count = current_count


def _trigger_background_retrain() -> None:
    """
    Spawn ml_trainer.py as a detached background process (fire-and-forget).
    Uses a lock file to prevent multiple concurrent trainers from racing on the pkl file.
    """
    trainer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ml_trainer.py')
    lock_path = os.path.join(os.path.dirname(_MODEL_PKL_PATH), 'ml_trainer.lock')
    try:
        # Check if a trainer is already running via lock file
        if os.path.exists(lock_path):
            lock_age = time.time() - os.path.getmtime(lock_path)
            if lock_age < 300:   # lock file < 5 min old = trainer likely still running
                print(f"[ml_signal] Background retrain skipped — trainer already running "
                      f"(lock age {lock_age:.0f}s)")
                return
            # Lock file is stale (>5 min) — previous trainer died, safe to remove
            os.remove(lock_path)

        # Write lock file before spawning so next call sees it immediately
        with open(lock_path, 'w') as f:
            f.write(str(os.getpid()))

        subprocess.Popen(
            [sys.executable, trainer_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,   # detach from parent process group
        )
        print("[ml_signal] Background retrain triggered")
    except Exception as e:
        print(f"[ml_signal] Failed to spawn background trainer: {e}")


def get_ml_signal(market_data: dict) -> Tuple[float, str]:
    """
    Compute P(win) for the current market_data snapshot.

    Returns:
        (p_win: float 0-1, confidence_label: str)
        confidence_label: 'strong' (>0.65), 'moderate' (0.55-0.65), 'weak' (<0.55)

    Falls back to (0.5, 'no_model') if model not trained yet.
    """
    global _model

    # Lazy load on first call. If pkl exists and is fresh, use it.
    # Otherwise fall back to inline training.
    if _model is None:
        train()

    # Hot-reload: if a fresh pkl appeared (background trainer finished), pick it up
    if _model is not None and os.path.exists(_MODEL_PKL_PATH):
        pkl_mtime = os.path.getmtime(_MODEL_PKL_PATH)
        if pkl_mtime > _last_retrain_ts:
            _load_from_pkl()

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
    # Time-of-day features (matches ml_trainer.py)
    hour_norm, session_norm = _get_current_time_features()
    row.append(hour_norm)
    row.append(session_norm)

    # Feature count guard: if pkl was trained without time features, truncate
    expected_len = len(_feature_cols) if _feature_cols else len(ALL_FEATURES)
    if len(row) > expected_len:
        row = row[:expected_len]
    elif len(row) < expected_len:
        row.extend([0.5] * (expected_len - len(row)))

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
        cols = ALL_FEATURES
        return dict(sorted(
            zip(cols, importances.tolist()),
            key=lambda x: x[1], reverse=True
        ))
    except Exception:
        return {}
