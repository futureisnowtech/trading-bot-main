"""
ml/regime_classifier.py — Market regime classification.

Regimes: TRENDING_UP / TRENDING_DOWN / RANGING / HIGH_VOL / ACCUMULATION / DISTRIBUTION / UNKNOWN

Classification inputs:
  - ATR ratio (current ATR / 20-bar average ATR)
  - ADX value
  - 20-bar price return
  - Volume trend slope
  - Funding rate direction (crypto-specific)

Used by signal_engine.py for entry threshold and ML regime multiplier selection.
"""

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REGIMES = [
    'TRENDING_UP',
    'TRENDING_DOWN',
    'RANGING',
    'HIGH_VOL',
    'ACCUMULATION',
    'DISTRIBUTION',
    'UNKNOWN',
]


def classify_regime(df: pd.DataFrame,
                     funding_rate: float = 0.0,
                     oi_change_pct: float = 0.0) -> str:
    """
    Classify current market regime from OHLCV data.

    Args:
        df:               OHLCV DataFrame with at least 40 bars
        funding_rate:     current funding rate (raw, e.g. 0.0001)
        oi_change_pct:    OI change over 4h as % (from open_interest indicator)

    Returns:
        regime string
    """
    if df is None or len(df) < 20:
        return 'UNKNOWN'

    try:
        closes  = df['close'].values.astype(float)
        volumes = df['volume'].values.astype(float)
        highs   = df['high'].values.astype(float)
        lows    = df['low'].values.astype(float)

        # True Range for ATR
        n = len(closes)
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1]),
            )

        atr_14 = float(np.mean(tr[-14:])) if n >= 14 else float(np.mean(tr[1:]))
        atr_hist = float(np.mean(tr[-40:-14])) if n >= 40 else atr_14
        atr_ratio = atr_14 / (atr_hist + 1e-9)

        # ADX (simplified)
        adx_14 = 25.0  # default
        if n >= 20:
            dm_plus  = np.maximum(np.diff(highs), 0)
            dm_minus = np.maximum(-np.diff(lows), 0)
            # Zero out where other direction is larger
            dm_plus_  = np.where(dm_plus > dm_minus, dm_plus, 0)
            dm_minus_ = np.where(dm_minus > dm_plus, dm_minus, 0)
            tr_s  = np.mean(tr[1:][-14:]) + 1e-9
            di_plus  = 100 * np.mean(dm_plus_[-14:]) / tr_s
            di_minus = 100 * np.mean(dm_minus_[-14:]) / tr_s
            dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus + 1e-9)
            adx_14 = float(dx)

        # Price return over 20 bars
        price_ret_20 = (closes[-1] - closes[-20]) / (closes[-20] + 1e-9) if n >= 20 else 0.0

        # Volume trend
        if n >= 20:
            x = np.arange(20)
            y = volumes[-20:]
            coef = np.polyfit(x, y, 1)
            vol_slope = coef[0] / (np.mean(y) + 1e-9)
        else:
            vol_slope = 0.0

        # ── Classification rules ─────────────────────────────────────────
        # HIGH_VOL: expanding volatility
        if atr_ratio > 1.8 or (atr_ratio > 1.5 and adx_14 > 30):
            return 'HIGH_VOL'

        # TRENDING: strong directional movement
        if adx_14 > 30 and abs(price_ret_20) > 0.03:
            if price_ret_20 > 0:
                # Check for distribution at top (high funding, OI declining)
                if funding_rate > 0.001 and oi_change_pct < -2:
                    return 'DISTRIBUTION'
                return 'TRENDING_UP'
            else:
                return 'TRENDING_DOWN'

        # ACCUMULATION: rising volume, tight range, low volatility
        if atr_ratio < 0.8 and vol_slope > 0.2 and abs(price_ret_20) < 0.02:
            if oi_change_pct > 3:
                return 'ACCUMULATION'

        # DISTRIBUTION: high funding + falling OI
        if funding_rate > 0.0008 and oi_change_pct < -3:
            return 'DISTRIBUTION'

        # RANGING: weak trend
        if adx_14 < 25 and atr_ratio < 1.2:
            return 'RANGING'

        # Default trending if strong move but ADX borderline
        if abs(price_ret_20) > 0.05:
            return 'TRENDING_UP' if price_ret_20 > 0 else 'TRENDING_DOWN'

        return 'RANGING'

    except Exception as e:
        logger.debug(f'[regime] classification error: {e}')
        return 'UNKNOWN'


def classify_from_features(features: Dict) -> str:
    """
    Classify regime from a feature dict (for use without raw OHLCV).
    Less accurate but fast.
    """
    if not features:
        return 'UNKNOWN'

    vol_mult = features.get('regime_vol_mult', 1.0)
    adx = features.get('regime_adx_normalized', 0.5) * 100
    ret_5c = features.get('price_return_5c', 0.0)
    ret_15c = features.get('price_return_15c', 0.0)
    funding = features.get('deriv_funding_rate', 0.0)
    oi_sig = features.get('deriv_oi_signal', 0.0)

    if vol_mult > 1.3:
        return 'HIGH_VOL'

    if adx > 30 and abs(ret_15c) > 0.03:
        return 'TRENDING_UP' if ret_15c > 0 else 'TRENDING_DOWN'

    if funding > 0.3 and oi_sig < -0.3:
        return 'DISTRIBUTION'

    if funding < -0.3 and oi_sig > 0.3:
        return 'ACCUMULATION'

    if adx < 25 and vol_mult < 1.2:
        return 'RANGING'

    return 'RANGING'
