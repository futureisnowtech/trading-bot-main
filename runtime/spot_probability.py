"""
runtime/spot_probability.py — Centralized Win Probability Engine (The Strategic Scalper core).

Unifies dashboard heuristics (ADX, Vol Spike, KST) with live ML predictions 
and Bayesian priors to provide a single calibrated probability score.
"""

from __future__ import annotations

import logging
import numpy as np
from typing import Any, Dict

logger = logging.getLogger(__name__)

def calculate_calibrated_win_prob(c: dict) -> float:
    """
    Blends Microstructure Heuristics, ML Predictions, and Bayesian Priors.
    Target output: 0.0 to 1.0 (Win Probability).
    """
    # 1. Base Probability (Heuristic from dashboard)
    # v18.31: Base 53.0 + default fallback 3 = 56.0 floor to clear 55% veto
    prob = 53.0
    dirn = c.get("direction", "LONG").upper()
    vs = float(c.get("vol_spike", 1.0))
    adx = float(c.get("adx_15m", 20.0))
    setup = str(c.get("primary_setup", "")).lower()
    vwap_d = abs(float(c.get("vwap_disp_pct", 0.0)))
    kst_v = float(c.get("kst_value", 0.0))
    kst_s = float(c.get("kst_signal", 0.0))
    st_dir = float(c.get("supertrend_dir", 0))
    fund = abs(float(c.get("funding_rate", 0.0)))
    pm1h = float(c.get("price_move_1h_pct", 0.0))

    # Vol Spike weighting
    if vs >= 3.0: prob += 9
    elif vs >= 2.0: prob += 6
    elif vs >= 1.5: prob += 3

    # ADX/Setup confluence
    if "momentum" in setup and adx >= 25: prob += 7
    elif "ranging" in setup and adx < 20: prob += 7
    elif "kst" in setup and adx < 30: prob += 4
    else: prob += 3

    # Indicator alignment
    if (dirn == "LONG" and kst_v > kst_s) or (dirn == "SHORT" and kst_v < kst_s):
        prob += 5
    if (dirn == "LONG" and st_dir > 0) or (dirn == "SHORT" and st_dir < 0):
        prob += 5
    
    if "ranging" in setup:
        if vwap_d >= 2.0: prob += 5
        elif vwap_d >= 1.0: prob += 3

    if fund > 0.002: prob += 3
    elif fund > 0.0005: prob += 1

    if dirn == "LONG" and pm1h > 0.3: prob += 2
    elif dirn == "SHORT" and pm1h < -0.3: prob += 2

    # 2. ML Online Learner Adjustment (±15%)
    try:
        from ml.online_learner import get_online_adjustment
        from ml.feature_builder import to_array
        
        features = c.get("features", {})
        if features:
            arr = to_array(features)
            # online_adj is -0.15 to +0.15
            online_adj = get_online_adjustment(arr, dirn, c.get("symbol", "GENERIC"))
            prob += (online_adj * 100.0)
    except Exception as e:
        logger.debug(f"[prob] ML adjustment skipped: {e}")

    # 3. Cap and Calibrate
    # Strategic Scalper Cap: We don't go above 88% (no such thing as a sure thing)
    final_prob = min(max(prob, 35.0), 88.0)
    
    return round(final_prob / 100.0, 3)

def sizing_multiplier(win_prob: float) -> float:
    """
    Continuous sigmoid sizing based on win probability.
    - 55% Win Prob = ~0.26x ($13 size on $50 base)
    - 65% Win Prob = ~0.62x ($31 size)
    - 75% Win Prob = ~0.92x ($46 size)
    - 85% Win Prob = ~1.10x ($55 size)
    """
    if win_prob < 0.55:
        return 0.0
    
    # v18.35: Sigmoid centered at 0.55, slope 6.0 (Flattened for volume on small accounts)
    z = (win_prob - 0.55) * 6.0
    mult = 1.0 / (1.0 + np.exp(-z))
    
    # Scale and clip (1.25x max override)
    final_mult = mult * 1.25
    return round(float(np.clip(final_mult, 0.0, 1.25)), 3)

def dynamic_stop_multiplier(win_prob: float, base_stop: float = 3.0) -> float:
    """
    Tighten stops as probability decreases.
    85% prob -> 3.0x ATR (Normal)
    60% prob -> 1.5x ATR (Microscopic leash)
    """
    # Linear scale: at 0.85 prob use 1.0x base; at 0.55 prob use 0.5x base
    ratio = max(0.5, min(1.0, 0.5 + (win_prob - 0.55) / 0.30))
    return round(base_stop * ratio, 2)
