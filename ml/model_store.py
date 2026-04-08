"""
ml/model_store.py — Loads trained XGBoost + LightGBM regression models.

Models trained by walk_forward_trainer.py are saved as:
  ml/models/{pair}_{direction}_xgb.pkl
  ml/models/{pair}_{direction}_lgbm.pkl
  ml/models/{pair}_{direction}_meta.pkl  ← {'pnl_scale': float}

Inference:
  predicted_pnl = 0.6 * xgb.predict(X) + 0.4 * lgbm.predict(X)
  score = 50 + 50 * tanh(predicted_pnl / pnl_scale)

This maps any dollar PnL prediction onto a 0-100 scale:
  predicted = 0    → score 50 (neutral)
  predicted > 0    → score > 50 (bullish)
  predicted >> 0   → score → 100 (strong conviction)
  predicted < 0    → score < 50 (bearish)
"""

import logging
import os
import math
import pickle
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")

_PAIR_MAP = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
}

# Cache: pair_direction → (xgb_model, lgbm_model, pnl_scale)
_model_cache: Dict[str, Tuple] = {}
_cache_mtime: Dict[str, float] = {}


def _get_pair_key(symbol: str) -> str:
    return _PAIR_MAP.get(symbol.upper(), "GENERIC")


def _model_path(pair_key: str, direction: str, model_type: str) -> str:
    return os.path.join(MODELS_DIR, f"{pair_key}_{direction}_{model_type}.pkl")


def _load_model_pair(
    pair_key: str, direction: str
) -> Tuple[Optional[object], Optional[object], float]:
    """
    Load XGB + LGBM regressors and pnl_scale for a pair/direction.
    Returns (xgb_model, lgbm_model, pnl_scale).
    Any component may be None if the file doesn't exist.
    """
    xgb_path = _model_path(pair_key, direction, "xgb")
    lgbm_path = _model_path(pair_key, direction, "lgbm")
    meta_path = _model_path(pair_key, direction, "meta")

    xgb_model = None
    lgbm_model = None
    pnl_scale = 1.0  # safe fallback: tanh(predicted/1.0)

    try:
        if os.path.exists(xgb_path):
            with open(xgb_path, "rb") as f:
                xgb_model = pickle.load(f)
    except Exception as e:
        logger.debug(f"[model_store] xgb load error {pair_key}_{direction}: {e}")

    try:
        if os.path.exists(lgbm_path):
            with open(lgbm_path, "rb") as f:
                lgbm_model = pickle.load(f)
    except Exception as e:
        logger.debug(f"[model_store] lgbm load error {pair_key}_{direction}: {e}")

    try:
        if os.path.exists(meta_path):
            with open(meta_path, "rb") as f:
                meta = pickle.load(f)
            pnl_scale = float(meta.get("pnl_scale", 1.0))
    except Exception as e:
        logger.debug(f"[model_store] meta load error {pair_key}_{direction}: {e}")

    return xgb_model, lgbm_model, pnl_scale


def _get_cached(pair_key: str, direction: str) -> Tuple:
    """Return cached model triple, reloading if files have changed."""
    cache_key = f"{pair_key}_{direction}"
    xgb_path = _model_path(pair_key, direction, "xgb")

    current_mtime = 0.0
    if os.path.exists(xgb_path):
        current_mtime = os.path.getmtime(xgb_path)

    if (
        cache_key not in _model_cache
        or _cache_mtime.get(cache_key, 0.0) != current_mtime
    ):
        _model_cache[cache_key] = _load_model_pair(pair_key, direction)
        _cache_mtime[cache_key] = current_mtime

    return _model_cache[cache_key]


class ModelStore:
    """
    Thin wrapper around saved walk-forward regression models.

    Usage:
        ms = ModelStore()
        score = ms.predict_ml_score(features_dict, direction='LONG', symbol='BTCUSDT')
        # score is 0-100, 50 = neutral
    """

    def predict_ml_score(
        self,
        features: Dict,
        direction: str,
        symbol: str = "",
    ) -> Optional[float]:
        """
        Run ensemble regression inference and return a 0-100 ML score.
        Returns None if no models are available.

        score = 50 + 50 * tanh(predicted_pnl / pnl_scale)
        """
        from ml.feature_builder import FEATURE_NAMES

        pair_key = _get_pair_key(symbol)
        xgb_model, lgbm_model, pnl_scale = _get_cached(pair_key, direction)

        if xgb_model is None and lgbm_model is None:
            # No models trained yet — caller will substitute 50.0 (neutral)
            return None

        # Build feature vector in canonical FEATURE_NAMES order
        try:
            X = np.array(
                [float(features.get(name, 0.0)) for name in FEATURE_NAMES],
                dtype=np.float32,
            ).reshape(1, -1)
        except Exception as e:
            logger.debug(f"[model_store] feature vector error: {e}")
            return None

        # Ensemble prediction
        predictions = []
        weights = []
        try:
            if xgb_model is not None:
                xgb_pred = float(xgb_model.predict(X)[0])
                predictions.append(xgb_pred)
                weights.append(0.6)
        except Exception as e:
            logger.debug(f"[model_store] xgb predict error: {e}")

        try:
            if lgbm_model is not None:
                lgbm_pred = float(lgbm_model.predict(X)[0])
                predictions.append(lgbm_pred)
                weights.append(0.4)
        except Exception as e:
            logger.debug(f"[model_store] lgbm predict error: {e}")

        if not predictions:
            return None

        # Weighted ensemble (renormalize weights if one model missing)
        total_w = sum(weights)
        predicted_pnl = sum(p * w for p, w in zip(predictions, weights)) / total_w

        # Normalize to 0-100 via tanh
        if pnl_scale <= 0:
            pnl_scale = 1.0
        score = 50.0 + 50.0 * math.tanh(predicted_pnl / pnl_scale)
        return float(np.clip(score, 0.0, 100.0))

    def predict_proba(
        self, features: Dict, direction: str, symbol: str = ""
    ) -> Optional[float]:
        """
        Legacy interface — returns 0-1 probability equivalent.
        New code should use predict_ml_score() directly.
        """
        score = self.predict_ml_score(features, direction, symbol)
        if score is None:
            return None
        return score / 100.0
