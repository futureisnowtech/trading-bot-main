"""
learning/calibration.py — Platt / Beta probability calibration (numpy only).

Phase 0: N < 10          → passthrough (raw_score / 100)
Phase 1: 10 ≤ N < 100   → Platt scaling (logistic regression, gradient descent)
Phase 2: N ≥ 100        → Beta calibration (3-param sigmoid on log-odds)
"""

import os
import sys
import time
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_MIN_TRADES_PLATT = 10
_MIN_TRADES_BETA = 100
_CACHE_MAX_AGE = 600  # 10 minutes

# Cache: {strategy: (params, phase, fitted_at)}
_CALIB_CACHE: dict = {}


# ── Fitting helpers ───────────────────────────────────────────────────────────


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def _fit_platt(scores: np.ndarray, outcomes: np.ndarray) -> list[float]:
    """Logistic regression via gradient descent. Returns [a, b]."""
    a, b = 0.0, 0.0
    lr = 0.01
    for _ in range(2000):
        p = _sigmoid(a * scores + b)
        err = p - outcomes
        da = float(np.dot(err, scores)) / len(scores)
        db = float(np.mean(err))
        a -= lr * da
        b -= lr * db
    return [float(a), float(b)]


def _fit_beta(scores: np.ndarray, outcomes: np.ndarray) -> list[float]:
    """Beta calibration: P(y=1|f) = sigmoid(a*ln(f) + b*ln(1-f) + c). Returns [a, b, c]."""
    eps = 1e-7
    s = np.clip(scores, eps, 1 - eps)
    ln_s = np.log(s)
    ln_1ms = np.log(1 - s)
    a, b, c = 1.0, 1.0, 0.0
    lr = 0.01
    for _ in range(3000):
        p = _sigmoid(a * ln_s + b * ln_1ms + c)
        err = p - outcomes
        da = float(np.dot(err, ln_s)) / len(outcomes)
        db = float(np.dot(err, ln_1ms)) / len(outcomes)
        dc = float(np.mean(err))
        a -= lr * da
        b -= lr * db
        c -= lr * dc
    return [float(a), float(b), float(c)]


# ── Data loading ──────────────────────────────────────────────────────────────


def _load_training_data(strategy: str) -> tuple[np.ndarray, np.ndarray]:
    """Read (ml_p_win * 100, won) from trade_attribution. Returns (scores, outcomes)."""
    import sqlite3

    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs",
        "trades.db",
    )
    try:
        con = sqlite3.connect(db_path, timeout=5)
        cur = con.cursor()
        cur.execute(
            """
            SELECT ml_p_win, won FROM trade_attribution
            WHERE strategy = ? AND ml_p_win > 0 AND won IS NOT NULL
            ORDER BY id DESC LIMIT 2000
            """,
            (strategy,),
        )
        rows = cur.fetchall()
        con.close()
    except Exception:
        return np.array([]), np.array([])
    if not rows:
        return np.array([]), np.array([])
    scores = np.array([float(r[0]) * 100 for r in rows], dtype=float)
    outcomes = np.array([float(r[1]) for r in rows], dtype=float)
    return scores, outcomes


# ── Public API ────────────────────────────────────────────────────────────────


def fit_calibrator(strategy: str) -> tuple[list, int]:
    """
    Fit calibrator for strategy. Returns (params, phase).
    Phase 0: passthrough  Phase 1: Platt  Phase 2: Beta
    Caches result in _CALIB_CACHE.
    """
    scores, outcomes = _load_training_data(strategy)
    n = len(scores)
    if n < _MIN_TRADES_PLATT:
        params, phase = [], 0
    elif n < _MIN_TRADES_BETA:
        s_norm = scores / 100.0
        params = _fit_platt(s_norm, outcomes)
        phase = 1
    else:
        s_norm = scores / 100.0
        params = _fit_beta(s_norm, outcomes)
        phase = 2
    _CALIB_CACHE[strategy] = (params, phase, time.time())
    return params, phase


def get_calibrated_probability(raw_score: float, strategy: str) -> float:
    """
    Convert raw ML score (0-100) to calibrated probability [0, 1].
    Auto-refits if cache is stale. Fails open to raw_score / 100.
    """
    try:
        cached = _CALIB_CACHE.get(strategy)
        if cached is None or (time.time() - cached[2]) > _CACHE_MAX_AGE:
            cached = fit_calibrator(strategy)
            # fit_calibrator stores in cache; re-read
            cached = _CALIB_CACHE.get(strategy, ([], 0, 0))

        params, phase, _ = cached
        f = float(raw_score) / 100.0
        f = max(1e-7, min(1 - 1e-7, f))

        if phase == 0 or not params:
            return f
        elif phase == 1:
            a, b = params
            return float(_sigmoid(np.array(a * f + b)))
        else:
            a, b, c = params
            eps = 1e-7
            f_c = max(eps, min(1 - eps, f))
            return float(_sigmoid(np.array(a * np.log(f_c) + b * np.log(1 - f_c) + c)))
    except Exception:
        return float(raw_score) / 100.0
