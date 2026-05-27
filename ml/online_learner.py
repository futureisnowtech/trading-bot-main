"""
ml/online_learner.py — Rolling perceptron for fast adaptation between weekly retrains.

Updates after every trade close.
Modulates XGBoost score: final = xgb_score + (online_adj * 0.15)
Online adjustment range: -0.15 to +0.15 (so total adjustment bounded)

Architecture:
  - 57-feature rolling perceptron (sklearn SGDClassifier)
  - Updates incrementally: model.partial_fit(features, [won])
  - Bounded weight norms to prevent drift
  - Expiry: reset if > 7 days since last trade
"""

import logging
import os
import pickle
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
_ONLINE_MODULATION = 0.15   # max ±15% adjustment to base ML score
_RESET_AFTER_DAYS  = 7      # reset if no trades for 7 days

try:
    from sklearn.linear_model import SGDClassifier
    _SKLEARN_OK = True
except ImportError:
    _SKLEARN_OK = False


class OnlineLearner:
    """Incremental perceptron that modulates XGBoost output."""

    def __init__(self, direction: str = 'LONG', pair_key: str = 'GENERIC'):
        self.direction = direction
        self.pair_key  = pair_key
        self.key       = f'{pair_key}_{direction}'
        self._model    = None
        self._last_update_ts = 0.0
        self._n_updates = 0
        self._load()

    def _model_path(self) -> str:
        return os.path.join(MODELS_DIR, f'{self.key}_online.pkl')

    def _load(self):
        path = self._model_path()
        if os.path.exists(path):
            try:
                with open(path, 'rb') as f:
                    data = pickle.load(f)
                self._model = data.get('model')
                self._last_update_ts = data.get('last_update_ts', 0.0)
                self._n_updates = data.get('n_updates', 0)
            except Exception:
                pass

    def _save(self):
        os.makedirs(MODELS_DIR, exist_ok=True)
        try:
            with open(self._model_path(), 'wb') as f:
                pickle.dump({
                    'model': self._model,
                    'last_update_ts': self._last_update_ts,
                    'n_updates': self._n_updates,
                }, f)
        except Exception as e:
            logger.debug(f'[online] save error: {e}')

    def _check_expiry(self):
        """Reset if > 7 days since last trade."""
        if self._last_update_ts > 0:
            days_since = (time.time() - self._last_update_ts) / 86400
            if days_since > _RESET_AFTER_DAYS:
                logger.info(f'[online] {self.key}: {days_since:.0f}d since last update — resetting')
                self._model = None
                self._n_updates = 0

    def update(self, features: np.ndarray, won: int):
        """
        Online update after a trade close.

        Args:
            features: 57-dim feature array (from feature_builder.to_array())
            won:      1 if trade won, 0 if lost
        """
        if not _SKLEARN_OK:
            return

        self._check_expiry()

        try:
            if self._model is None:
                self._model = SGDClassifier(
                    loss='log_loss',
                    alpha=0.001,
                    max_iter=1,
                    tol=None,
                    warm_start=True,
                    random_state=42,
                )
                # Initialize with dummy fit to set classes
                self._model.partial_fit(
                    features.reshape(1, -1), [won], classes=[0, 1]
                )
            else:
                self._model.partial_fit(features.reshape(1, -1), [won])

            # Bound weight norms to prevent drift
            if hasattr(self._model, 'coef_') and self._model.coef_ is not None:
                norm = np.linalg.norm(self._model.coef_)
                if norm > 10.0:
                    self._model.coef_ /= norm / 10.0

            self._last_update_ts = time.time()
            self._n_updates += 1
            self._save()

        except Exception as e:
            logger.debug(f'[online] update error: {e}')

    def get_adjustment(self, features: np.ndarray) -> float:
        """
        Get modulation adjustment for a new signal.
        Returns value in [-_ONLINE_MODULATION, +_ONLINE_MODULATION].
        """
        if not _SKLEARN_OK or self._model is None or self._n_updates < 5:
            return 0.0

        self._check_expiry()
        if self._model is None:
            return 0.0

        try:
            prob = float(self._model.predict_proba(features.reshape(1, -1))[0, 1])
            # Scale 0-1 to -0.15 to +0.15
            adj = (prob - 0.5) * 2 * _ONLINE_MODULATION
            return float(np.clip(adj, -_ONLINE_MODULATION, _ONLINE_MODULATION))
        except Exception:
            return 0.0

    @property
    def n_updates(self) -> int:
        return self._n_updates


# Singleton registry
_learners: dict = {}


def get_learner(direction: str, symbol: str = 'GENERIC') -> OnlineLearner:
    """Get or create online learner for a direction/pair combo."""
    from ml.walk_forward_trainer import _get_pair_key
    pair_key = _get_pair_key(symbol)
    key = f'{pair_key}_{direction.upper()}'
    if key not in _learners:
        _learners[key] = OnlineLearner(direction.upper(), pair_key)
    return _learners[key]


def record_outcome(features: np.ndarray, won: bool,
                    direction: str, symbol: str = 'GENERIC'):
    """Convenience: update online learner after trade close."""
    learner = get_learner(direction, symbol)
    learner.update(features, int(won))


def get_online_adjustment(features: np.ndarray,
                           direction: str, symbol: str = 'GENERIC') -> float:
    """Convenience: get modulation for new trade."""
    learner = get_learner(direction, symbol)
    return learner.get_adjustment(features)
