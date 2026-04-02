"""
ml/calibration.py — Platt scaling calibration for model probability outputs.

Target: Brier score < 0.20.
Auto-recalibrate trigger: Brier score > 0.22.

Stores calibration state in ml_calibration SQLite table.
"""

import logging
import time
import pickle
import os
from typing import Optional, Tuple, List

import numpy as np

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
_BRIER_TARGET   = 0.20
_BRIER_RECALIB  = 0.22

try:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression
    _SKLEARN_OK = True
except ImportError:
    _SKLEARN_OK = False
    logger.warning('[calibration] sklearn not available')


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """Compute Brier score: mean((y_true - y_prob)^2). Lower is better."""
    return float(np.mean((y_true.astype(float) - y_prob) ** 2))


def platt_calibrate(raw_probs: np.ndarray,
                     y_true: np.ndarray) -> Tuple[Optional[object], float]:
    """
    Fit Platt scaling (logistic regression on log-odds).

    Args:
        raw_probs: uncalibrated model probabilities
        y_true:    ground truth labels (0/1)

    Returns:
        (calibrator, brier_score_after) or (None, pre_brier_score)
    """
    if not _SKLEARN_OK or len(raw_probs) < 20:
        bs = brier_score(y_true, raw_probs)
        return None, bs

    try:
        # Platt scaling: fit logistic regression on raw_probs
        log_odds = np.log(np.clip(raw_probs, 1e-7, 1-1e-7) /
                          (1 - np.clip(raw_probs, 1e-7, 1-1e-7)))
        X = log_odds.reshape(-1, 1)

        calibrator = LogisticRegression(C=1.0, solver='lbfgs')
        calibrator.fit(X, y_true)

        calibrated_probs = calibrator.predict_proba(X)[:, 1]
        bs_after = brier_score(y_true, calibrated_probs)

        return calibrator, bs_after
    except Exception as e:
        logger.debug(f'[calibration] platt error: {e}')
        bs = brier_score(y_true, raw_probs)
        return None, bs


def apply_calibration(calibrator, raw_prob: float) -> float:
    """
    Apply fitted Platt calibrator to a single probability.
    Returns calibrated probability.
    """
    if calibrator is None:
        return raw_prob

    try:
        log_odds = np.log(max(1e-7, raw_prob) / max(1e-7, 1 - raw_prob))
        X = np.array([[log_odds]])
        calibrated = calibrator.predict_proba(X)[0, 1]
        return float(np.clip(calibrated, 0.01, 0.99))
    except Exception:
        return raw_prob


def _load_calibrator(pair_key: str, direction: str) -> Optional[object]:
    path = os.path.join(MODELS_DIR, f'{pair_key}_{direction}_calibrator.pkl')
    if os.path.exists(path):
        try:
            with open(path, 'rb') as f:
                return pickle.load(f)
        except Exception:
            pass
    return None


def _save_calibrator(calibrator, pair_key: str, direction: str):
    path = os.path.join(MODELS_DIR, f'{pair_key}_{direction}_calibrator.pkl')
    try:
        with open(path, 'wb') as f:
            pickle.dump(calibrator, f)
    except Exception as e:
        logger.debug(f'[calibration] save error: {e}')


def _record_brier(pair_key: str, direction: str, brier: float,
                   n_samples: int):
    """Write Brier score to ml_calibration table."""
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        db.conn.execute("""
            INSERT OR REPLACE INTO ml_calibration
            (pair_key, direction, brier_score, n_samples, ts)
            VALUES (?, ?, ?, ?, ?)
        """, (pair_key, direction, brier, n_samples, time.time()))
        db.conn.commit()
    except Exception as e:
        logger.debug(f'[calibration] DB write error: {e}')


def get_brier_score(pair_key: str, direction: str) -> Optional[float]:
    """Get last recorded Brier score for a model."""
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        row = db.conn.execute("""
            SELECT brier_score FROM ml_calibration
            WHERE pair_key=? AND direction=?
            ORDER BY ts DESC LIMIT 1
        """, (pair_key, direction)).fetchone()
        return float(row[0]) if row else None
    except Exception:
        return None


def recalibrate_if_needed(pair_key: str, direction: str,
                            raw_probs: np.ndarray,
                            y_true: np.ndarray) -> Tuple[float, bool]:
    """
    Check current Brier score. If > 0.22, re-fit Platt scaling.

    Returns:
        (brier_score, recalibrated)
    """
    current_brier = get_brier_score(pair_key, direction)

    if current_brier is not None and current_brier <= _BRIER_RECALIB:
        # No recalibration needed
        return current_brier, False

    logger.info(f'[calibration] Recalibrating {pair_key}_{direction} '
               f'(brier={current_brier or "unknown"})')

    calibrator, brier_after = platt_calibrate(raw_probs, y_true)

    if calibrator is not None:
        _save_calibrator(calibrator, pair_key, direction)

    _record_brier(pair_key, direction, brier_after, len(y_true))

    if brier_after > _BRIER_TARGET:
        logger.warning(f'[calibration] {pair_key}_{direction}: '
                      f'Brier {brier_after:.3f} > target {_BRIER_TARGET}')
    else:
        logger.info(f'[calibration] {pair_key}_{direction}: '
                   f'Brier {brier_after:.3f} ≤ target {_BRIER_TARGET} ✓')

    return brier_after, True


class ModelStore:
    """
    Unified access to XGBoost + LightGBM models + calibrators.
    Used by signal_engine.py to get ML scores.
    """

    def __init__(self, paper: bool = True):
        self.paper = paper
        self._xgb_models = {}
        self._lgb_models = {}
        self._calibrators = {}
        self._load_all()

    def _load_all(self):
        """Load all available models from disk."""
        if not os.path.exists(MODELS_DIR):
            return

        pairs = ['BTC', 'ETH', 'SOL', 'GENERIC']
        directions = ['LONG', 'SHORT']

        for pair in pairs:
            for direction in directions:
                key = f'{pair}_{direction}'

                xgb_path = os.path.join(MODELS_DIR, f'{key}_xgb.pkl')
                if os.path.exists(xgb_path):
                    try:
                        with open(xgb_path, 'rb') as f:
                            self._xgb_models[key] = pickle.load(f)
                    except Exception:
                        pass

                lgb_path = os.path.join(MODELS_DIR, f'{key}_lgbm.pkl')
                if os.path.exists(lgb_path):
                    try:
                        with open(lgb_path, 'rb') as f:
                            self._lgb_models[key] = pickle.load(f)
                    except Exception:
                        pass

                cal = _load_calibrator(pair, direction)
                if cal is not None:
                    self._calibrators[key] = cal

        logger.info(f'[model_store] Loaded: {len(self._xgb_models)} XGB, '
                   f'{len(self._lgb_models)} LGB, {len(self._calibrators)} calibrators')

    def predict_proba(self, features: dict, direction: str,
                       symbol: str = 'GENERIC') -> Optional[float]:
        """
        Get calibrated ensemble probability for a trade.

        Returns:
            Probability 0-1, or None if no model available.
        """
        from ml.walk_forward_trainer import _get_pair_key
        from ml.feature_builder import FEATURE_NAMES, to_array

        pair_key = _get_pair_key(symbol)
        key = f'{pair_key}_{direction.upper()}'
        generic_key = f'GENERIC_{direction.upper()}'

        # Build feature array
        try:
            arr = to_array(features).reshape(1, -1)
        except Exception:
            return None

        xgb_prob = None
        lgb_prob  = None

        xgb_model = self._xgb_models.get(key) or self._xgb_models.get(generic_key)
        lgb_model  = self._lgb_models.get(key)  or self._lgb_models.get(generic_key)

        # Models trained on 3-column proxy — try full features first, fallback to proxy
        try:
            if xgb_model is not None:
                # Try full features; if shape mismatch, use proxy
                try:
                    xgb_prob = float(xgb_model.predict_proba(arr)[0, 1])
                except Exception:
                    proxy = arr[:, :3]  # first 3 features as proxy
                    xgb_prob = float(xgb_model.predict_proba(proxy)[0, 1])

            if lgb_model is not None:
                try:
                    lgb_prob = float(lgb_model.predict_proba(arr)[0, 1])
                except Exception:
                    proxy = arr[:, :3]
                    lgb_prob = float(lgb_model.predict_proba(proxy)[0, 1])
        except Exception as e:
            logger.debug(f'[model_store] predict error: {e}')
            return None

        if xgb_prob is None and lgb_prob is None:
            return None

        # Ensemble: 60/40 XGB/LGB
        if xgb_prob is not None and lgb_prob is not None:
            raw_prob = 0.6 * xgb_prob + 0.4 * lgb_prob
        elif xgb_prob is not None:
            raw_prob = xgb_prob
        else:
            raw_prob = lgb_prob

        # Apply calibration
        cal = self._calibrators.get(key) or self._calibrators.get(generic_key)
        calibrated = apply_calibration(cal, raw_prob)

        return float(np.clip(calibrated, 0.01, 0.99))

    def is_ready(self) -> bool:
        """True if at least one model is loaded."""
        return len(self._xgb_models) > 0 or len(self._lgb_models) > 0

    def reload(self):
        """Reload models from disk (call after retrain)."""
        self._xgb_models.clear()
        self._lgb_models.clear()
        self._calibrators.clear()
        self._load_all()
