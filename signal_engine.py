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


def _tv_signal_score() -> float:
    try:
        import config as _cfg

        if (
            str(getattr(_cfg, "TV_SIGNAL_MODE", "context_filter")).lower()
            != "synthetic_candidate"
        ):
            return 0.0
        return float(getattr(_cfg, "TV_SIGNAL_BOOST_CONVICTION", 0.0) or 0.0)
    except Exception:
        return 0.0


# ── Regime multipliers for ML tower ─────────────────────────────────────────
_REGIME_ML_MULT = {
    "TRENDING_UP": {"LONG": 1.15, "SHORT": 0.85},
    "TRENDING_DOWN": {"LONG": 0.85, "SHORT": 1.15},
    "RANGING": {"LONG": 0.90, "SHORT": 0.90},
    "HIGH_VOL": {"LONG": 0.80, "SHORT": 0.80},
    "ACCUMULATION": {"LONG": 1.10, "SHORT": 0.90},
    "DISTRIBUTION": {"LONG": 0.90, "SHORT": 1.10},
    "UNKNOWN": {"LONG": 1.00, "SHORT": 1.00},
}

# ── Entry thresholds by regime ────────────────────────────────────────────────
_ENTRY_THRESHOLDS = {
    # v13: Raised from 50 → 58 for all regimes (except HIGH_VOL already at 54 → 60).
    # Data: 88 parent trades at score 50-57 showed WR=47%, avg_pnl=-$0.27 (negative edge).
    # 11 parent trades at score >= 58 showed WR=64%, avg_pnl=+$0.23 (positive edge).
    # NOTE: In v10_runner.py the Tier 2 gate (composite >= 58) is the live entry gate.
    # This dict is used by signal_engine.score() 'should_enter' field (informational)
    # and by thesis exits. Keep it consistent with the live gate.
    "TRENDING_UP": 58,
    "TRENDING_DOWN": 58,
    "RANGING": 58,
    "HIGH_VOL": 60,  # high-vol: stricter — require extra signal alignment
    "LOW_VOL": 56,  # low-vol: slightly looser — less noise in calm markets
    "ACCUMULATION": 58,
    "DISTRIBUTION": 58,
    "UNKNOWN": 58,
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
    if f.get("cvd_divergence", 0) > 0:
        score += 25
        components["cvd_bull_div"] = 25

    # MACD multi-variant aligned long: +20
    if f.get("mom_macd_long_aligned", 0) > 0:
        score += 20
        components["macd_aligned"] = 20
    elif f.get("mom_macd_hist_fast", 0) > 0:
        score += 8
        components["macd_fast_pos"] = 8

    # RSI bullish divergence: +15
    if f.get("mom_rsi_divergence", 0) > 0:
        score += 15
        components["rsi_bull_div"] = 15

    # Funding rate squeeze setup (very negative funding = longs cheaply financed): +15
    funding = f.get("deriv_funding_rate", 0)
    if funding < -0.3:  # normalized: < -0.3 means very negative actual funding
        score += 15
        components["funding_squeeze"] = 15
    elif funding < -0.1:
        score += 8
        components["funding_favorable"] = 8

    # VWAP reclaim on volume: +15
    if f.get("vwap_reclaim", 0) > 0:
        score += 15
        components["vwap_reclaim"] = 15

    # OB imbalance bullish (L5): +10
    l5 = f.get("ob_imbalance_l5", 0.5)
    if l5 > 0.60:
        score += 10
        components["ob_bull"] = 10
    elif l5 > 0.55:
        score += 5
        components["ob_bull_weak"] = 5

    # Williams %R oversold exit: +10
    # wr feature is (wr+100)/100, so wr < -70 → feature < 0.30
    wr_feat = f.get("mom_williams_r", 0.5)
    if wr_feat < 0.20:  # WR < -80
        score += 10
        components["wr_oversold"] = 10
    elif wr_feat < 0.30:
        score += 5
        components["wr_oversold_weak"] = 5

    # Liquidation cascade completed (liq_cascade_risk high AND liq_magnet long): +15
    cascade = f.get("liq_cascade_risk", 0)
    long_dist = f.get("liq_long_dist_pct", 1.0)
    if cascade > 0.5 and long_dist < 0.2:
        score += 15
        components["liq_cascade_long"] = 15

    # Whale accumulation: +10
    if f.get("onchain_whale_signal", 0) > 0:
        score += 10
        components["whale_accum"] = 10

    # Options skew bullish: +10
    if f.get("deriv_skew_direction", 0) > 0:
        score += 10
        components["skew_bull"] = 10

    # Volume spike confirmation: +5
    if f.get("vol_spike_5c", 1.0) > 1.5:
        score += 5
        components["vol_spike"] = 5

    # RSI not overbought: +5 bonus
    rsi = f.get("mom_rsi_14", 0.5)
    if rsi < 0.60:
        score += 5
        components["rsi_not_overbought"] = 5

    # ── v4.3 indicator suite (SuperTrend, Ichimoku, WAE, Fisher, Chop, WaveTrend, Laguerre) ──
    # SuperTrend bullish (ATR 10, mult 3.0): +12
    if f.get("supertrend_bullish", 0) > 0:
        score += 12
        components["supertrend_bull"] = 12

    # Ichimoku cloud bullish (price above cloud): +8
    if f.get("cloud_bullish", 0) > 0:
        score += 8
        components["cloud_bull"] = 8

    # Waddah Attar Explosion bullish: +10 (bullish + exploding), +5 (bullish only)
    if f.get("wae_bullish", 0) > 0 and f.get("wae_exploding", 0) > 0:
        score += 10
        components["wae_bull_exploding"] = 10
    elif f.get("wae_bullish", 0) > 0:
        score += 5
        components["wae_bull"] = 5

    # Ehlers Fisher Transform cross-up from negative: +8
    if f.get("fisher_cross_up", 0) > 0:
        score += 8
        components["fisher_cross_up"] = 8

    # Choppiness Index: strongly trending (< 38.2): +5
    if f.get("chop_trending", 0) > 0:
        score += 5
        components["chop_trending"] = 5

    # WaveTrend oversold cross: +12
    if f.get("wt_oversold_cross", 0) > 0:
        score += 12
        components["wt_oversold_cross"] = 12

    # Laguerre RSI deeply oversold: +8 (< 0.15), +4 (< 0.25)
    lrsi = f.get("lrsi_value", 0.5)
    if lrsi < 0.15:
        score += 8
        components["lrsi_deep_oversold"] = 8
    elif lrsi < 0.25:
        score += 4
        components["lrsi_oversold"] = 4

    # KST (Know Sure Thing) oscillator above its signal line: +8
    if f.get("kst_bullish", 0) > 0:
        score += 8
        components["kst_bullish"] = 8

    # TradingView synthetic-candidate boost (disabled in HTF context-filter mode)
    _tv_pts = _tv_signal_score()
    if f.get("tv_signal", 0) > 0 and _tv_pts > 0:
        score += _tv_pts
        components["tv_signal"] = _tv_pts

    # ── Deductions ────────────────────────────────────────────
    # Price at 2σ+ above VWAP: -25
    band_pos = f.get("vwap_band_position", 0)
    if band_pos >= 2:
        score -= 25
        components["vwap_extended"] = -25
    elif band_pos >= 1:
        score -= 10
        components["vwap_above"] = -10

    # Extreme positive funding (longs paying heavily): -20
    if funding > 0.5:
        score -= 20
        components["funding_extreme"] = -20
    elif funding > 0.3:
        score -= 10
        components["funding_high"] = -10

    # RSI bearish divergence: -15
    if f.get("mom_rsi_divergence", 0) < 0:
        score -= 15
        components["rsi_bear_div"] = -15

    # CVD bearish divergence: -20
    if f.get("cvd_divergence", 0) < 0:
        score -= 20
        components["cvd_bear_div"] = -20

    # High cascade risk (generalized): -15
    if cascade > 0.70:
        score -= 15
        components["cascade_risk"] = -15

    # OB bearish pressure: -10
    if l5 < 0.40:
        score -= 10
        components["ob_bear"] = -10
    elif l5 < 0.45:
        score -= 5
        components["ob_bear_weak"] = -5

    # Fear & Greed extreme (>80 = euphoria, bad for longs): -10
    fg = f.get("regime_fg_current", 0.5)
    if fg > 0.85:
        score -= 10
        components["fg_euphoria"] = -10

    # Whale distributing: -15
    if f.get("onchain_whale_signal", 0) < 0:
        score -= 15
        components["whale_dist"] = -15

    return float(score), components


def _technical_short_score(f: Dict) -> Tuple[float, Dict]:
    """Mirror of long score for SHORT direction."""
    score = 0
    components = {}

    # CVD bearish divergence: +25
    if f.get("cvd_divergence", 0) < 0:
        score += 25
        components["cvd_bear_div"] = 25

    # MACD aligned short (all histograms negative): +20
    macd_fast = f.get("mom_macd_hist_fast", 0)
    macd_slow = f.get("mom_macd_hist_slow", 0)
    if macd_fast < 0 and macd_slow < 0 and f.get("mom_macd_long_aligned", 0) == 0:
        score += 20
        components["macd_aligned_short"] = 20
    elif macd_fast < 0:
        score += 8
        components["macd_fast_neg"] = 8

    # RSI bearish divergence: +15
    if f.get("mom_rsi_divergence", 0) < 0:
        score += 15
        components["rsi_bear_div"] = 15

    # Extreme positive funding (shorts profiting from funding): +15
    funding = f.get("deriv_funding_rate", 0)
    if funding > 0.5:
        score += 15
        components["funding_overheated"] = 15
    elif funding > 0.3:
        score += 8
        components["funding_high"] = 8

    # Price failed VWAP reclaim: +10
    band_pos = f.get("vwap_band_position", 0)
    if band_pos >= 2:
        score += 10
        components["vwap_extended_short"] = 10

    # OB imbalance bearish: +10
    l5 = f.get("ob_imbalance_l5", 0.5)
    if l5 < 0.40:
        score += 10
        components["ob_bear"] = 10
    elif l5 < 0.45:
        score += 5
        components["ob_bear_weak"] = 5

    # Williams %R overbought: +10
    wr_feat = f.get("mom_williams_r", 0.5)
    if wr_feat > 0.90:  # WR > -10
        score += 10
        components["wr_overbought"] = 10
    elif wr_feat > 0.80:
        score += 5
        components["wr_overbought_weak"] = 5

    # Whale distributing: +15
    if f.get("onchain_whale_signal", 0) < 0:
        score += 15
        components["whale_dist"] = 15

    # Options skew bearish: +10
    if f.get("deriv_skew_direction", 0) < 0:
        score += 10
        components["skew_bear"] = 10

    # Volume spike on down move: +5
    ret_5c = f.get("price_return_5c", 0)
    if f.get("vol_spike_5c", 1.0) > 1.5 and ret_5c < 0:
        score += 5
        components["vol_spike_down"] = 5

    # ── v4.3 indicator suite (bearish mirror) ───────────────
    # SuperTrend bearish: +12
    if f.get("supertrend_bearish", 0) > 0:
        score += 12
        components["supertrend_bear"] = 12

    # Ichimoku cloud bearish (price below cloud): +8
    if f.get("cloud_bearish", 0) > 0:
        score += 8
        components["cloud_bear"] = 8

    # WAE bearish (trend_down > 0): +10 (+ exploding), +5 (bearish only)
    if f.get("wae_bearish", 0) > 0 and f.get("wae_exploding", 0) > 0:
        score += 10
        components["wae_bear_exploding"] = 10
    elif f.get("wae_bearish", 0) > 0:
        score += 5
        components["wae_bear"] = 5

    # Fisher cross-down from positive: +8
    if f.get("fisher_cross_down", 0) > 0:
        score += 8
        components["fisher_cross_down"] = 8

    # Choppiness trending (same filter applies both directions): +5
    if f.get("chop_trending", 0) > 0:
        score += 5
        components["chop_trending"] = 5

    # WaveTrend overbought (WT1 > 53): +12
    if f.get("wt_overbought", 0) > 0:
        score += 12
        components["wt_overbought"] = 12

    # Laguerre RSI deeply overbought: +8 (> 0.85), +4 (> 0.75)
    lrsi = f.get("lrsi_value", 0.5)
    if lrsi > 0.85:
        score += 8
        components["lrsi_deep_overbought"] = 8
    elif lrsi > 0.75:
        score += 4
        components["lrsi_overbought"] = 4

    # KST below its signal line (bearish momentum): +8
    # Guard: require kst_bullish=0 AND kst_value < 0 (KST in negative territory).
    # kst_bullish=0 alone fires on ~50% of bars (any time KST is below its signal
    # line including neutral oscillations).  Adding kst_value < 0 restricts scoring
    # to confirmed negative KST momentum — not just a mild oscillation below signal.
    # kst_value injected by v10_runner as features['kst_value'].
    if (
        "kst_bullish" in f
        and f.get("kst_bullish", 0) == 0
        and f.get("kst_value", 1.0) < 0
    ):
        score += 8
        components["kst_bearish"] = 8

    # TradingView synthetic-candidate boost (disabled in HTF context-filter mode)
    _tv_pts = _tv_signal_score()
    if f.get("tv_signal", 0) > 0 and _tv_pts > 0:
        score += _tv_pts
        components["tv_signal"] = _tv_pts

    # ── Deductions ──────────────────────────────────────────
    # CVD bullish divergence: -20
    if f.get("cvd_divergence", 0) > 0:
        score -= 20
        components["cvd_bull_div"] = -20

    # RSI oversold: -15
    rsi = f.get("mom_rsi_14", 0.5)
    if rsi < 0.30:
        score -= 15
        components["rsi_oversold"] = -15

    # Very negative funding (shorts paying): -20
    if funding < -0.5:
        score -= 20
        components["funding_negative"] = -20

    # VWAP reclaim signal (bullish): -15
    if f.get("vwap_reclaim", 0) > 0:
        score -= 15
        components["vwap_reclaim"] = -15

    # Whale accumulating: -15
    if f.get("onchain_whale_signal", 0) > 0:
        score -= 15
        components["whale_accum"] = -15

    # Fear & Greed extreme fear (<15 = potential reversal bullish): -10
    fg = f.get("regime_fg_current", 0.5)
    if fg < 0.15:
        score -= 10
        components["fg_extreme_fear"] = -10

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


def _get_ml_score(
    features: Dict, direction: str, regime: str, model_store=None
) -> float:
    """
    Get ML score from trained models (if available).
    Falls back to 50.0 (neutral) if no model yet.
    """
    if model_store is None:
        return 50.0

    try:
        symbol_hint = str(
            features.get("symbol")
            or features.get("base_asset")
            or features.get("executed_symbol")
            or ""
        ).strip()
        # predict_ml_score returns 0-100 directly (tanh-normalized PnL regression)
        raw_score = model_store.predict_ml_score(
            features, direction, symbol=symbol_hint
        )
        if raw_score is None:
            return 50.0
        mult = _REGIME_ML_MULT.get(regime, {}).get(direction, 1.0)
        return float(np.clip(raw_score * mult, 0, 100))
    except Exception as e:
        logger.debug(f"[signal_engine] ML score error: {e}")
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
    """Count how many days since first clean paper_v10 close in DB."""
    try:
        import sqlite3 as _sq
        from datetime import datetime as _dt

        conn = _sq.connect("logs/trades.db")
        row = conn.execute(
            "SELECT MIN(ts) FROM trades WHERE paper=1 AND won IS NOT NULL AND source='clean_paper_v10'"
        ).fetchone()
        conn.close()
        if row and row[0]:
            # ts column is ISO-format text (e.g. "2026-04-02T11:30:00.123456-04:00")
            _ts_str = str(row[0]).replace("Z", "+00:00")
            try:
                first_dt = _dt.fromisoformat(_ts_str).replace(tzinfo=None)
            except ValueError:
                # Fallback: strip timezone suffix and try again
                first_dt = _dt.fromisoformat(_ts_str[:26])
            days = (_dt.now() - first_dt).total_seconds() / 86400
            return max(0, int(days))
    except Exception:
        pass
    return 0


def score(
    features: Dict,
    direction: str = "LONG",
    regime: str = "UNKNOWN",
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
    if direction == "LONG":
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
    top_components = sorted(components.items(), key=lambda x: abs(x[1]), reverse=True)[
        :5
    ]
    comp_str = ", ".join(f"{k}({v:+d})" for k, v in top_components)
    signal_description = (
        f"{direction} composite={composite:.1f} "
        f"(tech={tech_score:.1f}×{tech_w:.0%} + ml={ml_score:.1f}×{ml_w:.0%}) "
        f"threshold={threshold} → {'ENTER' if should_enter else 'SKIP'} | {comp_str}"
    )

    return {
        "technical_score": tech_score,
        "ml_score": ml_score,
        "composite_score": composite,
        "technical_weight": tech_w,
        "ml_weight": ml_w,
        "entry_threshold": threshold,
        "should_enter": should_enter,
        "direction": direction,
        "regime": regime,
        "components": components,
        "signal_description": signal_description,
        "live_trade_days": live_trade_days,
    }


def score_both_directions(
    features: Dict,
    regime: str = "UNKNOWN",
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

    long_result = score(features, "LONG", regime, model_store, ltd)
    short_result = score(features, "SHORT", regime, model_store, ltd)

    if long_result["should_enter"] and short_result["should_enter"]:
        # Both pass: take the stronger one
        if long_result["composite_score"] >= short_result["composite_score"]:
            best = "LONG"
            best_score = long_result["composite_score"]
        else:
            best = "SHORT"
            best_score = short_result["composite_score"]
    elif long_result["should_enter"]:
        best = "LONG"
        best_score = long_result["composite_score"]
    elif short_result["should_enter"]:
        best = "SHORT"
        best_score = short_result["composite_score"]
    else:
        best = "NONE"
        best_score = max(
            long_result["composite_score"], short_result["composite_score"]
        )

    return {
        "best_direction": best,
        "best_score": best_score,
        "long": long_result,
        "short": short_result,
        "should_enter": best != "NONE",
    }


# Regime-conditional thesis exit thresholds.
# TRENDING: slightly looser (trends can pause without dying)
# RANGING: tighter (ranging setups are fragile; mean-reversion can flip fast)
# HIGH_VOL: loosest (noisy signal, avoid churn)
# UNKNOWN: default mid-point
_THESIS_THRESHOLDS: Dict[str, float] = {
    "TRENDING": 0.30,
    "RANGING": 0.15,
    "HIGH_VOL": 0.35,
    "UNKNOWN": 0.25,
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

    Thesis fails when: current_score < entry_score × regime_threshold
    Thresholds: TRENDING=30%, RANGING=15%, HIGH_VOL=35%, UNKNOWN=25%
    """
    current_result = score(current_features, direction, regime, model_store)
    current_score = current_result["composite_score"]
    pct = _THESIS_THRESHOLDS.get(regime.upper() if regime else "UNKNOWN", 0.25)
    threshold = entry_composite_score * pct

    if current_score < threshold:
        reason = (
            f"Thesis degraded: entry={entry_composite_score:.1f} → "
            f"current={current_score:.1f} (< {threshold:.1f} = {pct * 100:.0f}% of entry, regime={regime})"
        )
        return False, current_score, reason

    return True, current_score, "Thesis intact"


# ── Tier 1 Primary Setup Definitions ─────────────────────────────────────────
# These are specific indicator combinations that trigger entry unconditionally.
# Composite score is used only for position sizing, not as an entry gate.
# Each setup has a 'check' (entry condition) and 'invalidate' (thesis exit condition).

_LONG_SETUPS = [
    # ── Momentum setups (require chop_ranging == 0 — don't trade breakouts in a box) ──
    {
        "name": "wt_reversal",
        "label": "WaveTrend Reversal from Oversold",
        # WT1 crosses WT2 from below -53 AND SuperTrend bullish AND market NOT ranging
        "check": lambda f: (
            f.get("wt_oversold_cross", 0) > 0
            and f.get("supertrend_bullish", 0) > 0
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: f.get("supertrend_bearish", 0) > 0,
    },
    {
        "name": "squeeze_breakout",
        "label": "BB-Keltner Squeeze Breakout Long",
        # Squeeze fires, direction up, volume confirming AND market NOT ranging
        # (a squeeze firing while CHOP is high is a false breakout — skip it)
        "check": lambda f: (
            f.get("squeeze_fired", 0) > 0
            and f.get("squeeze_direction", 0) > 0
            and f.get("vol_spike_5c", 1.0) > 1.3
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: (
            f.get("supertrend_bearish", 0) > 0 or f.get("wae_bullish", 0) == 0
        ),
    },
    {
        "name": "wae_explosion",
        "label": "WAE Momentum Explosion Long",
        # WAE bullish + exploding + at least one MACD timeframe confirming + NOT ranging.
        # OR condition (fast OR slow MACD) since WAE already confirms sustained momentum;
        # requiring both was double-filtering the same signal and blocking valid setups.
        "check": lambda f: (
            f.get("wae_bullish", 0) > 0
            and f.get("wae_exploding", 0) > 0
            and (
                f.get("mom_macd_hist_fast", 0) > 0 or f.get("mom_macd_hist_slow", 0) > 0
            )
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: f.get("wae_bullish", 0) == 0,
    },
    {
        "name": "tv_confirmed_long",
        "label": "TradingView Alert + Indicator Confirmed Long",
        # TV alert + confirming indicator — allowed in any regime
        "check": lambda f: (
            _tv_signal_score() > 0
            and f.get("tv_signal", 0) > 0
            and (
                f.get("supertrend_bullish", 0) > 0
                or f.get("wae_bullish", 0) > 0
                or f.get("wt_oversold_cross", 0) > 0
            )
        ),
        "invalidate": lambda f: (
            f.get("supertrend_bearish", 0) > 0 and f.get("wae_bullish", 0) == 0
        ),
    },
    # ── SuperTrend flip: trend just turned bullish — high-conviction directional shift ──
    {
        "name": "supertrend_cross_long",
        "label": "SuperTrend Bullish Cross",
        # ST direction just flipped from -1 → +1 on this bar.
        # Requires KST OR MACD confirming the flip (either momentum signal agrees).
        # Requiring both was too strict — they often lag each other by 1-2 bars.
        # Blocked in confirmed ranging (chop > 61.8) — ST crosses in a box are noise.
        "check": lambda f: (
            f.get("supertrend_cross_up", 0) > 0
            and (f.get("kst_bullish", 0) > 0 or f.get("mom_macd_hist_fast", 0) > 0)
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: f.get("supertrend_bearish", 0) > 0,
    },
    # ── KST cross: momentum oscillator just crossed its signal line bullish ──
    {
        "name": "kst_cross_long",
        "label": "KST Bullish Cross + Trend Confirm",
        # KST just crossed above its signal line AND value positive (above zero line)
        # confirming that momentum is both turning and has real upside direction.
        # SuperTrend must agree — prevents trading KST crosses in downtrends.
        "check": lambda f: (
            f.get("kst_cross_up", 0) > 0
            and f.get("kst_value", 0) > 0
            and f.get("supertrend_bullish", 0) > 0
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: (
            f.get("kst_bullish", 0) == 0 or f.get("supertrend_bearish", 0) > 0
        ),
    },
    # ── Ichimoku cloud breakout: price just pushed above kumo — structural bullish shift ──
    {
        "name": "ichimoku_cloud_breakout_long",
        "label": "Ichimoku Cloud Breakout Long",
        # Price just crossed above the kumo top (Senkou A/B max) — rare, high-conviction.
        # Require SuperTrend bullish + MACD positive to filter fake breaks.
        # TK cross up (Tenkan above Kijun) can also confirm without requiring cloud cross.
        "check": lambda f: (
            (
                f.get("cloud_cross_up", 0) > 0
                or (f.get("cloud_bullish", 0) > 0 and f.get("tk_cross_up", 0) > 0)
            )
            and f.get("supertrend_bullish", 0) > 0
            and f.get("mom_macd_hist_fast", 0) > 0
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: (
            f.get("cloud_bearish", 0) > 0 or f.get("supertrend_bearish", 0) > 0
        ),
    },
    # ── Mean-reversion setups (require chop_ranging — only trade in confirmed ranges) ──
    {
        "name": "ranging_mr_long",
        "label": "Ranging Mean-Reversion Long (VWAP reclaim)",
        # CHOP confirms ranging AND price stretched below VWAP AND Laguerre oversold
        # AND SuperTrend not actively bearish (don't catch a falling knife in a downtrend)
        # VWAP threshold restored to -0.20%: 0.30% was too rare, missing most MR setups.
        # 0.20% is clearly outside spread noise while still being an actionable dislocation.
        "check": lambda f: (
            f.get("chop_ranging", 0) > 0
            and f.get("vwap_session_dist_pct", 0) < -0.20
            and f.get("lrsi_value", 0.5) < 0.25
            and f.get("supertrend_bearish", 0) == 0
        ),
        # Thesis: price returns to VWAP OR regime shifts to trending OR ST flips down
        "invalidate": lambda f: (
            f.get("vwap_session_dist_pct", 0) > 0.05
            or f.get("chop_ranging", 0) == 0
            or f.get("supertrend_bearish", 0) > 0
        ),
    },
]

_SHORT_SETUPS = [
    # ── Momentum setups (require chop_ranging == 0) ──────────────────────────
    {
        "name": "wt_overbought_reversal",
        "label": "WaveTrend Reversal from Overbought",
        "check": lambda f: (
            f.get("wt_overbought", 0) > 0
            and f.get("supertrend_bearish", 0) > 0
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: f.get("supertrend_bullish", 0) > 0,
    },
    {
        "name": "squeeze_breakout_short",
        "label": "BB-Keltner Squeeze Breakout Short",
        "check": lambda f: (
            f.get("squeeze_fired", 0) > 0
            and f.get("squeeze_direction", 0) < 0
            and f.get("vol_spike_5c", 1.0) > 1.3
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: (
            f.get("supertrend_bullish", 0) > 0 or f.get("wae_bearish", 0) == 0
        ),
    },
    {
        "name": "wae_explosion_short",
        "label": "WAE Momentum Explosion Short",
        # Mirror of long: require both fast AND slow MACD negative.
        # Data: wae_explosion_short at fast-only gate had WR=8% across 37 parent
        # trades — essentially no edge. Adding slow MACD (6,20,5 < 0) filters
        # single-bar oscillation and requires sustained downside momentum. v18.16 change.
        "check": lambda f: (
            f.get("wae_bearish", 0) > 0
            and f.get("wae_exploding", 0) > 0
            and f.get("mom_macd_hist_fast", 0) < 0
            and f.get("mom_macd_hist_slow", 0) < 0
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: f.get("wae_bearish", 0) == 0,
    },
    {
        "name": "tv_confirmed_short",
        "label": "TradingView Alert + Indicator Confirmed Short",
        "check": lambda f: (
            _tv_signal_score() > 0
            and f.get("tv_signal", 0) > 0
            and (
                f.get("supertrend_bearish", 0) > 0
                or f.get("wae_bearish", 0) > 0
                or f.get("wt_overbought", 0) > 0
            )
        ),
        "invalidate": lambda f: (
            f.get("supertrend_bullish", 0) > 0 and f.get("wae_bearish", 0) == 0
        ),
    },
    # ── SuperTrend flip: trend just turned bearish ────────────────────────────
    {
        "name": "supertrend_cross_short",
        "label": "SuperTrend Bearish Cross",
        "check": lambda f: (
            f.get("supertrend_cross_down", 0) > 0
            and f.get("kst_bullish", 0) == 0
            and f.get("mom_macd_hist_fast", 0) < 0
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: f.get("supertrend_bullish", 0) > 0,
    },
    # ── KST cross: momentum oscillator just crossed bearish ──────────────────
    {
        "name": "kst_cross_short",
        "label": "KST Bearish Cross + Trend Confirm",
        "check": lambda f: (
            f.get("kst_cross_down", 0) > 0
            and f.get("kst_value", 0) < 0
            and f.get("supertrend_bearish", 0) > 0
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: (
            f.get("kst_bullish", 0) > 0 or f.get("supertrend_bullish", 0) > 0
        ),
    },
    # ── Ichimoku cloud breakdown: price just fell below kumo ─────────────────
    {
        "name": "ichimoku_cloud_breakout_short",
        "label": "Ichimoku Cloud Breakdown Short",
        "check": lambda f: (
            (
                f.get("cloud_cross_down", 0) > 0
                or (f.get("cloud_bearish", 0) > 0 and f.get("tk_cross_down", 0) > 0)
            )
            and f.get("supertrend_bearish", 0) > 0
            and f.get("mom_macd_hist_fast", 0) < 0
            and f.get("chop_ranging", 0) == 0
        ),
        "invalidate": lambda f: (
            f.get("cloud_bullish", 0) > 0 or f.get("supertrend_bullish", 0) > 0
        ),
    },
    # ── Mean-reversion setups (require chop_ranging) ──────────────────────────
    {
        "name": "ranging_mr_short",
        "label": "Ranging Mean-Reversion Short (VWAP fade)",
        # CHOP confirms ranging AND price stretched above VWAP AND Laguerre overbought
        # AND SuperTrend not actively bullish
        # VWAP threshold restored to 0.20% (symmetric with long side fix).
        "check": lambda f: (
            f.get("chop_ranging", 0) > 0
            and f.get("vwap_session_dist_pct", 0) > 0.20
            and f.get("lrsi_value", 0.5) > 0.75
            and f.get("supertrend_bullish", 0) == 0
        ),
        # Thesis: price returns to VWAP OR regime shifts OR ST flips up
        "invalidate": lambda f: (
            f.get("vwap_session_dist_pct", 0) < -0.05
            or f.get("chop_ranging", 0) == 0
            or f.get("supertrend_bullish", 0) > 0
        ),
    },
]


def detect_primary_setup(features: Dict, direction: str = "LONG") -> Optional[Dict]:
    """
    Scan features for a Tier 1 primary setup.

    Returns {'name', 'label', 'tier': 1} if a setup matches, else None.
    A Tier 1 match triggers entry regardless of composite score.
    Composite score is used only for position sizing.
    """
    setups = _LONG_SETUPS if direction == "LONG" else _SHORT_SETUPS
    for setup in setups:
        try:
            if setup["check"](features):
                return {"name": setup["name"], "label": setup["label"], "tier": 1}
        except Exception:
            continue
    return None


def check_setup_still_valid(
    setup_name: str,
    features: Dict,
    direction: str,
) -> Tuple[Optional[bool], str]:
    """
    Check if the Tier 1 setup that triggered entry is still intact.
    Called by Priority 3 thesis exit in position_manager.

    Returns:
        (False, reason)  — setup invalidated, exit now
        (True, 'intact') — setup still valid, hold
        (None, 'unknown setup') — name not found, caller falls back to score comparison
    """
    setups = _LONG_SETUPS if direction == "LONG" else _SHORT_SETUPS
    for setup in setups:
        if setup["name"] == setup_name:
            try:
                if setup["invalidate"](features):
                    return False, f"{setup['label']} — setup conditions invalidated"
                return True, "setup intact"
            except Exception:
                return True, "invalidate check error — holding"
    return None, "unknown setup"
