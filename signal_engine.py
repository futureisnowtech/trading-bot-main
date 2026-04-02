"""
signal_engine.py — Two-tower signal composition engine.

Tower 1: Technical Score (0-100)
  Applies the spec's point system to all indicator signals.
  Long score / short score computed independently.

Tower 2: ML Score (0-100)
  calibrated_xgb_output × 100 × regime_multiplier
  Falls back to 50 (neutral) if model not trained yet.

Composite = technical_weight × tech_score + ml_weight × ml_score

Weight schedule (shifts as trade data accumulates):
  < 30 days live: 80% technical / 20% ML
  30-100 days: 50/50
  > 100 days: 30% technical / 70% ML

Entry thresholds (regime-adjusted):
  TRENDING:    62
  RANGING:     68
  HIGH_VOL:    72
  LOW_VOL:     58
  default:     65
"""

import logging
import time
import math
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── Regime multipliers for ML tower ─────────────────────────────────────────
_REGIME_ML_MULT = {
    'TRENDING_UP':   {'LONG': 1.15, 'SHORT': 0.85},
    'TRENDING_DOWN': {'LONG': 0.85, 'SHORT': 1.15},
    'RANGING':       {'LONG': 0.90, 'SHORT': 0.90},
    'HIGH_VOL':      {'LONG': 0.80, 'SHORT': 0.80},
    'ACCUMULATION':  {'LONG': 1.10, 'SHORT': 0.90},
    'DISTRIBUTION':  {'LONG': 0.90, 'SHORT': 1.10},
    'UNKNOWN':       {'LONG': 1.00, 'SHORT': 1.00},
}

# ── Entry thresholds by regime ────────────────────────────────────────────────
_ENTRY_THRESHOLDS = {
    # All thresholds temporarily lowered for OHLCV-only operation.
    # Max achievable score with OHLCV alone: ~54 (MACD+RSI+no-funding-penalty).
    # Raise back to 58/62/65/etc once CVD, OB, whale, options, liq feeds are wired.
    'TRENDING_UP':   47,
    'TRENDING_DOWN': 47,
    'RANGING':       47,
    'HIGH_VOL':      52,    # keep slightly higher — high-vol entries need stronger signal
    'LOW_VOL':       47,
    'ACCUMULATION':  47,
    'DISTRIBUTION':  47,
    'UNKNOWN':       47,
}


def _technical_long_score(f: Dict) -> Tuple[float, Dict]:
    """
    Compute technical LONG score from feature dict.
    Returns (raw_score, component_breakdown).
    Raw score range: approximately -115 to +150.
    Normalised to 0-100 by the caller.
    """
    score = 0
    components = {}

    # CVD bullish divergence: +25
    if f.get('cvd_divergence', 0) > 0:
        score += 25
        components['cvd_bull_div'] = 25

    # MACD multi-variant aligned long: +20
    if f.get('mom_macd_long_aligned', 0) > 0:
        score += 20
        components['macd_aligned'] = 20
    elif f.get('mom_macd_hist_fast', 0) > 0:
        score += 8
        components['macd_fast_pos'] = 8

    # RSI bullish divergence: +15
    if f.get('mom_rsi_divergence', 0) > 0:
        score += 15
        components['rsi_bull_div'] = 15

    # Funding rate squeeze setup (very negative funding = longs cheaply financed): +15
    funding = f.get('deriv_funding_rate', 0)
    if funding < -0.3:   # normalized: < -0.3 means very negative actual funding
        score += 15
        components['funding_squeeze'] = 15
    elif funding < -0.1:
        score += 8
        components['funding_favorable'] = 8

    # VWAP reclaim on volume: +15
    if f.get('vwap_reclaim', 0) > 0:
        score += 15
        components['vwap_reclaim'] = 15

    # OB imbalance bullish (L5): +10
    l5 = f.get('ob_imbalance_l5', 0.5)
    if l5 > 0.60:
        score += 10
        components['ob_bull'] = 10
    elif l5 > 0.55:
        score += 5
        components['ob_bull_weak'] = 5

    # Williams %R oversold exit: +10
    # wr feature is (wr+100)/100, so wr < -70 → feature < 0.30
    wr_feat = f.get('mom_williams_r', 0.5)
    if wr_feat < 0.20:   # WR < -80
        score += 10
        components['wr_oversold'] = 10
    elif wr_feat < 0.30:
        score += 5
        components['wr_oversold_weak'] = 5

    # Liquidation cascade completed (liq_cascade_risk high AND liq_magnet long): +15
    cascade = f.get('liq_cascade_risk', 0)
    long_dist = f.get('liq_long_dist_pct', 1.0)
    if cascade > 0.5 and long_dist < 0.2:
        score += 15
        components['liq_cascade_long'] = 15

    # Whale accumulation: +10
    if f.get('onchain_whale_signal', 0) > 0:
        score += 10
        components['whale_accum'] = 10

    # Options skew bullish: +10
    if f.get('deriv_skew_direction', 0) > 0:
        score += 10
        components['skew_bull'] = 10

    # Volume spike confirmation: +5
    if f.get('vol_spike_5c', 1.0) > 1.5:
        score += 5
        components['vol_spike'] = 5

    # RSI not overbought: +5 bonus
    rsi = f.get('mom_rsi_14', 0.5)
    if rsi < 0.60:
        score += 5
        components['rsi_not_overbought'] = 5

    # ── Deductions ────────────────────────────────────────────
    # Price at 2σ+ above VWAP: -25
    band_pos = f.get('vwap_band_position', 0)
    if band_pos >= 2:
        score -= 25
        components['vwap_extended'] = -25
    elif band_pos >= 1:
        score -= 10
        components['vwap_above'] = -10

    # Extreme positive funding (longs paying heavily): -20
    if funding > 0.5:
        score -= 20
        components['funding_extreme'] = -20
    elif funding > 0.3:
        score -= 10
        components['funding_high'] = -10

    # RSI bearish divergence: -15
    if f.get('mom_rsi_divergence', 0) < 0:
        score -= 15
        components['rsi_bear_div'] = -15

    # CVD bearish divergence: -20
    if f.get('cvd_divergence', 0) < 0:
        score -= 20
        components['cvd_bear_div'] = -20

    # High cascade risk (generalized): -15
    if cascade > 0.70:
        score -= 15
        components['cascade_risk'] = -15

    # OB bearish pressure: -10
    if l5 < 0.40:
        score -= 10
        components['ob_bear'] = -10
    elif l5 < 0.45:
        score -= 5
        components['ob_bear_weak'] = -5

    # Fear & Greed extreme (>80 = euphoria, bad for longs): -10
    fg = f.get('regime_fg_current', 0.5)
    if fg > 0.85:
        score -= 10
        components['fg_euphoria'] = -10

    # Whale distributing: -15
    if f.get('onchain_whale_signal', 0) < 0:
        score -= 15
        components['whale_dist'] = -15

    return float(score), components


def _technical_short_score(f: Dict) -> Tuple[float, Dict]:
    """Mirror of long score for SHORT direction."""
    score = 0
    components = {}

    # CVD bearish divergence: +25
    if f.get('cvd_divergence', 0) < 0:
        score += 25
        components['cvd_bear_div'] = 25

    # MACD aligned short (all histograms negative): +20
    macd_fast = f.get('mom_macd_hist_fast', 0)
    macd_slow = f.get('mom_macd_hist_slow', 0)
    if macd_fast < 0 and macd_slow < 0 and f.get('mom_macd_long_aligned', 0) == 0:
        score += 20
        components['macd_aligned_short'] = 20
    elif macd_fast < 0:
        score += 8
        components['macd_fast_neg'] = 8

    # RSI bearish divergence: +15
    if f.get('mom_rsi_divergence', 0) < 0:
        score += 15
        components['rsi_bear_div'] = 15

    # Extreme positive funding (shorts profiting from funding): +15
    funding = f.get('deriv_funding_rate', 0)
    if funding > 0.5:
        score += 15
        components['funding_overheated'] = 15
    elif funding > 0.3:
        score += 8
        components['funding_high'] = 8

    # Price failed VWAP reclaim: +10
    band_pos = f.get('vwap_band_position', 0)
    if band_pos >= 2:
        score += 10
        components['vwap_extended_short'] = 10

    # OB imbalance bearish: +10
    l5 = f.get('ob_imbalance_l5', 0.5)
    if l5 < 0.40:
        score += 10
        components['ob_bear'] = 10
    elif l5 < 0.45:
        score += 5
        components['ob_bear_weak'] = 5

    # Williams %R overbought: +10
    wr_feat = f.get('mom_williams_r', 0.5)
    if wr_feat > 0.90:   # WR > -10
        score += 10
        components['wr_overbought'] = 10
    elif wr_feat > 0.80:
        score += 5
        components['wr_overbought_weak'] = 5

    # Whale distributing: +15
    if f.get('onchain_whale_signal', 0) < 0:
        score += 15
        components['whale_dist'] = 15

    # Options skew bearish: +10
    if f.get('deriv_skew_direction', 0) < 0:
        score += 10
        components['skew_bear'] = 10

    # Volume spike on down move: +5
    ret_5c = f.get('price_return_5c', 0)
    if f.get('vol_spike_5c', 1.0) > 1.5 and ret_5c < 0:
        score += 5
        components['vol_spike_down'] = 5

    # ── Deductions ──────────────────────────────────────────
    # CVD bullish divergence: -20
    if f.get('cvd_divergence', 0) > 0:
        score -= 20
        components['cvd_bull_div'] = -20

    # RSI oversold: -15
    rsi = f.get('mom_rsi_14', 0.5)
    if rsi < 0.30:
        score -= 15
        components['rsi_oversold'] = -15

    # Very negative funding (shorts paying): -20
    if funding < -0.5:
        score -= 20
        components['funding_negative'] = -20

    # VWAP reclaim signal (bullish): -15
    if f.get('vwap_reclaim', 0) > 0:
        score -= 15
        components['vwap_reclaim'] = -15

    # Whale accumulating: -15
    if f.get('onchain_whale_signal', 0) > 0:
        score -= 15
        components['whale_accum'] = -15

    # Fear & Greed extreme fear (<15 = potential reversal bullish): -10
    fg = f.get('regime_fg_current', 0.5)
    if fg < 0.15:
        score -= 10
        components['fg_extreme_fear'] = -10

    return float(score), components


def _normalise_tech_score(raw: float) -> float:
    """
    Normalise raw technical score (-115 to +150) → 0-100.
    0 maps to score=0 (neutral), min/max clipped.
    """
    # Shift so 0 → 50, then scale
    clamped = float(np.clip(raw, -115, 150))
    normalised = (clamped + 115) / (150 + 115) * 100
    return round(float(normalised), 2)


def _get_ml_score(features: Dict, direction: str, regime: str,
                  model_store=None) -> float:
    """
    Get ML score from trained models (if available).
    Falls back to 50.0 (neutral) if no model yet.
    """
    if model_store is None:
        return 50.0

    try:
        raw = model_store.predict_proba(features, direction)
        if raw is None:
            return 50.0
        mult = _REGIME_ML_MULT.get(regime, {}).get(direction, 1.0)
        return float(np.clip(raw * 100 * mult, 0, 100))
    except Exception as e:
        logger.debug(f'[signal_engine] ML score error: {e}')
        return 50.0


def _composite_weights(live_trade_days: int) -> Tuple[float, float]:
    """
    Return (tech_weight, ml_weight) based on live trade data history.
    Shifts from tech-heavy → ML-heavy as data accumulates.
    """
    if live_trade_days < 30:
        return 0.80, 0.20
    elif live_trade_days < 100:
        # Linear interpolation: 50/50 at 30d, 30/70 at 100d
        t = (live_trade_days - 30) / 70
        tech_w = 0.80 - t * (0.80 - 0.30)
        return round(tech_w, 3), round(1 - tech_w, 3)
    else:
        return 0.30, 0.70


def _live_trade_days() -> int:
    """Count how many days since first live trade in DB."""
    try:
        from logging_db.trade_logger import get_logger
        db = get_logger()
        row = db.conn.execute(
            "SELECT MIN(ts) FROM trades WHERE paper=1 AND action='SELL'"
        ).fetchone()
        if row and row[0]:
            days = (time.time() - float(row[0])) / 86400
            return int(days)
    except Exception:
        pass
    return 0


def score(
    features: Dict,
    direction: str = 'LONG',
    regime: str = 'UNKNOWN',
    model_store=None,
    live_trade_days: Optional[int] = None,
) -> Dict:
    """
    Compute two-tower composite score.

    Args:
        features:       57-feature dict from ml/feature_builder.py
        direction:      'LONG' or 'SHORT'
        regime:         current regime string
        model_store:    ML model store object with predict_proba(features, direction)
        live_trade_days: override for weight schedule (auto-computed from DB if None)

    Returns:
        {
          'technical_score':  float 0-100,
          'ml_score':         float 0-100,
          'composite_score':  float 0-100,
          'technical_weight': float,
          'ml_weight':        float,
          'entry_threshold':  float,
          'should_enter':     bool,
          'direction':        str,
          'regime':           str,
          'components':       dict (technical breakdown),
          'signal_description': str,
        }
    """
    direction = direction.upper()

    # Tower 1: Technical
    if direction == 'LONG':
        raw_tech, components = _technical_long_score(features)
    else:
        raw_tech, components = _technical_short_score(features)
    tech_score = _normalise_tech_score(raw_tech)

    # Tower 2: ML
    if live_trade_days is None:
        live_trade_days = _live_trade_days()
    ml_score = _get_ml_score(features, direction, regime, model_store)

    # Composite
    tech_w, ml_w = _composite_weights(live_trade_days)
    composite = round(tech_w * tech_score + ml_w * ml_score, 2)

    # Same threshold in paper and live — paper must meet live standards
    threshold = _ENTRY_THRESHOLDS.get(regime, 65)
    should_enter = composite >= threshold

    # Human-readable summary
    top_components = sorted(components.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
    comp_str = ', '.join(f'{k}({v:+d})' for k, v in top_components)
    signal_description = (
        f'{direction} composite={composite:.1f} '
        f'(tech={tech_score:.1f}×{tech_w:.0%} + ml={ml_score:.1f}×{ml_w:.0%}) '
        f'threshold={threshold} → {"ENTER" if should_enter else "SKIP"} | {comp_str}'
    )

    return {
        'technical_score':  tech_score,
        'ml_score':         ml_score,
        'composite_score':  composite,
        'technical_weight': tech_w,
        'ml_weight':        ml_w,
        'entry_threshold':  threshold,
        'should_enter':     should_enter,
        'direction':        direction,
        'regime':           regime,
        'components':       components,
        'signal_description': signal_description,
        'live_trade_days':  live_trade_days,
    }


def score_both_directions(
    features: Dict,
    regime: str = 'UNKNOWN',
    model_store=None,
    live_trade_days: Optional[int] = None,
) -> Dict:
    """
    Score both LONG and SHORT, return the stronger one (or neither).

    Returns:
        {
          'best_direction':  'LONG' | 'SHORT' | 'NONE',
          'best_score':      float,
          'long':            score() result dict,
          'short':           score() result dict,
          'should_enter':    bool,
        }
    """
    ltd = live_trade_days if live_trade_days is not None else _live_trade_days()

    long_result  = score(features, 'LONG',  regime, model_store, ltd)
    short_result = score(features, 'SHORT', regime, model_store, ltd)

    if long_result['should_enter'] and short_result['should_enter']:
        # Both pass: take the stronger one
        if long_result['composite_score'] >= short_result['composite_score']:
            best = 'LONG'
            best_score = long_result['composite_score']
        else:
            best = 'SHORT'
            best_score = short_result['composite_score']
    elif long_result['should_enter']:
        best = 'LONG'
        best_score = long_result['composite_score']
    elif short_result['should_enter']:
        best = 'SHORT'
        best_score = short_result['composite_score']
    else:
        best = 'NONE'
        best_score = max(long_result['composite_score'], short_result['composite_score'])

    return {
        'best_direction': best,
        'best_score':     best_score,
        'long':           long_result,
        'short':          short_result,
        'should_enter':   best != 'NONE',
    }


def thesis_still_valid(
    entry_composite_score: float,
    current_features: Dict,
    direction: str,
    regime: str,
    model_store=None,
) -> Tuple[bool, float, str]:
    """
    Priority 3 exit: thesis score check.
    Returns (still_valid, current_score, reason).

    Thesis fails when: current_score < entry_score × 0.45
    """
    current_result = score(current_features, direction, regime, model_store)
    current_score = current_result['composite_score']
    threshold = entry_composite_score * 0.45

    if current_score < threshold:
        reason = (
            f'Thesis degraded: entry={entry_composite_score:.1f} → '
            f'current={current_score:.1f} (< {threshold:.1f} = 45% of entry)'
        )
        return False, current_score, reason

    return True, current_score, 'Thesis intact'
