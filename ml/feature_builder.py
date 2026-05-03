"""
ml/feature_builder.py — 57-feature engineering for the ML pipeline.

Feature groups (11 groups, 57 features total):
Note: The original spec said "47 features" but the group sizes sum to 57 — this is the
correct count. All 11 groups are fully implemented.
  price       (8): returns, ATR distances, VWAP distances, regime location
  volume      (6): buy ratio, volume spikes, dollar vol normalized, trend slope
  cvd         (5): CVD value, slope 5c/20c, divergence type, trend aligned
  momentum    (7): MACD hist×2, RSI 14/21, Williams %R, MACD aligned, RSI div
  vwap        (4): session/weekly dist pct, band position, reclaim signal
  orderbook   (5): L1/L5/L20 imbalance, wall above/below pct
  derivatives (6): funding rate, carry annual, OI signal, LS ratio, skew direction, IV rank
  liquidation (3): long/short liq dist pct, cascade risk score
  regime      (5): vol regime mult, fg current, fg momentum, RSI centerline, ADX normalized
  time        (4): hour_sin, hour_cos, day_of_week, session (0=ASIA,1=LON,2=NY)
  onchain     (4): whale_signal, whale_strength, exchange_flow, onchain_score
"""

import logging
import time
import math
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Feature name registry (in canonical order for model training)
FEATURE_NAMES = [
    # --- price (8) ---
    "price_return_1c",
    "price_return_5c",
    "price_return_15c",
    "price_atr_normalized",
    "price_vs_session_vwap_pct",
    "price_vs_weekly_vwap_pct",
    "price_high_low_range_pct",
    "price_bb_position",  # 0-1 where price sits in 20-bar BB
    # --- volume (6) ---
    "vol_buy_ratio",
    "vol_spike_5c",
    "vol_spike_20c",
    "vol_dollar_normalized",
    "vol_trend_slope",
    "vol_at_price_pct",
    # --- cvd (5) ---
    "cvd_value_normalized",
    "cvd_slope_5c",
    "cvd_slope_20c",
    "cvd_divergence",  # 1=bullish, -1=bearish, 0=none
    "cvd_trend_aligned",  # 1=aligned, 0=not
    # --- momentum (7) ---
    "mom_macd_hist_fast",  # MACD(3,15,3) histogram
    "mom_macd_hist_slow",  # MACD(6,20,5) histogram
    "mom_rsi_14",
    "mom_rsi_21",
    "mom_williams_r",
    "mom_macd_long_aligned",  # 1 if all 3 variants agree long
    "mom_rsi_divergence",  # 1=bullish_div, -1=bearish_div, 0=none
    # --- vwap (4) ---
    "vwap_session_dist_pct",
    "vwap_weekly_dist_pct",
    "vwap_band_position",  # -3 to +3 sigma
    "vwap_reclaim",  # 1=reclaim signal active
    # --- orderbook (5) ---
    "ob_imbalance_l1",
    "ob_imbalance_l5",
    "ob_imbalance_l20",
    "ob_wall_above_pct",
    "ob_wall_below_pct",
    # --- derivatives (6) ---
    "deriv_funding_rate",
    "deriv_carry_annual",
    "deriv_oi_signal",  # encoded: bull=1, squeeze=0.5, neutral=0, bear=-1
    "deriv_ls_ratio",
    "deriv_skew_direction",  # 1=bullish, -1=bearish, 0=neutral
    "deriv_iv_rank",  # 0-100 normalized to 0-1
    # --- liquidation (3) ---
    "liq_long_dist_pct",
    "liq_short_dist_pct",
    "liq_cascade_risk",  # 0-100 normalized to 0-1
    # --- regime/sentiment (5) ---
    "regime_vol_mult",  # ATR regime multiplier (0.7-1.3)
    "regime_fg_current",  # Fear & Greed 0-100 normalized 0-1
    "regime_fg_momentum_7d",  # FG change last 7d, normalized
    "regime_rsi_centerline",  # 1 if RSI crossed 50 upward, -1 down, 0 neutral
    "regime_adx_normalized",  # ADX/100
    # --- time (4) ---
    "time_hour_sin",  # sin(2π*hour/24)
    "time_hour_cos",  # cos(2π*hour/24)
    "time_day_of_week",  # 0=Mon, 6=Sun normalized 0-1
    "time_session",  # 0=ASIA, 0.5=LONDON, 1.0=NY
    # --- onchain (4) ---
    "onchain_whale_signal",  # 1=accumulating, -1=distributing, 0=neutral
    "onchain_whale_strength",  # 0-1
    "onchain_exchange_flow",  # positive=inflow (bearish), negative=outflow (bullish)
    "onchain_score",  # composite 0-1
]

assert len(FEATURE_NAMES) == 57, f"Expected 57 features, got {len(FEATURE_NAMES)}"

_OI_SIGNAL_MAP = {
    "strong_bull": 1.0,
    "short_squeeze": 0.5,
    "long_squeeze": -0.5,
    "strong_bear": -1.0,
    "neutral": 0.0,
}

_SKEW_MAP = {
    "bullish": 1.0,
    "bearish": -1.0,
    "neutral": 0.0,
}

_WHALE_DIR_MAP = {
    "accumulating": 1.0,
    "distributing": -1.0,
    "neutral": 0.0,
}

_DIV_MAP = {
    "bullish_div": 1.0,
    "bullish": 1.0,
    "bearish_div": -1.0,
    "bearish": -1.0,
    "none": 0.0,
}


def _bb_position(df: pd.DataFrame, window: int = 20) -> float:
    """0-1 position of current close within Bollinger Band."""
    if len(df) < window:
        return 0.5
    closes = df["close"].tail(window)
    mean = float(closes.mean())
    std = float(closes.std())
    if std < 1e-9:
        return 0.5
    current = float(df["close"].iloc[-1])
    pos = (current - (mean - 2 * std)) / (4 * std + 1e-9)
    return float(np.clip(pos, 0.0, 1.0))


def build_features(
    df: pd.DataFrame,
    symbol: str,
    indicator_cache: Optional[Dict] = None,
    sentiment_cache: Optional[Dict] = None,
    feeds=None,
) -> Dict[str, float]:
    """
    Build the full 47-feature vector for ML inference or training.

    Args:
        df:               OHLCV DataFrame with DatetimeIndex (UTC), at least 60 bars
        symbol:           e.g. 'BTCUSDT'
        indicator_cache:  pre-computed indicator dict (avoids re-computing each)
        sentiment_cache:  pre-computed sentiment dict from data/sentiment_data.py
        feeds:            RealtimeFeeds instance (optional, for live orderbook data)

    Returns:
        dict mapping each feature name → float value, NaN-safe (fills with neutral).
        Also includes 'feature_ts' and 'symbol' keys (not in FEATURE_NAMES).
    """
    result: Dict[str, float] = {}

    # ── Collect all indicator signals ─────────────────────────────────────
    cache = indicator_cache or {}

    # Helper: get from cache or compute lazily
    def _ind(key):
        return cache.get(key, {})

    # Price-based indicators
    from indicators.atr_regime import compute_atr_regime
    from indicators.vwap_mtf import compute_vwap_mtf
    from indicators.orderflow import compute_orderflow
    from indicators.cvd import get_cvd_signal
    from indicators.macd_advanced import compute_macd_advanced
    from indicators.rsi_advanced import compute_rsi_advanced
    from indicators.williams_r import compute_williams_r
    from indicators.orderbook import get_orderbook_signal
    from indicators.funding_rate import get_funding_signal
    from indicators.open_interest import get_oi_signal
    from indicators.liquidation_levels import get_liquidation_signal
    from indicators.microstructure import compute_microstructure

    try:
        atr = _ind("atr") or compute_atr_regime(df)
    except Exception:
        atr = {}

    try:
        vwap = _ind("vwap") or compute_vwap_mtf(df)
    except Exception:
        vwap = {}

    try:
        of = _ind("orderflow") or compute_orderflow(df)
    except Exception:
        of = {}

    try:
        cvd = _ind("cvd") or get_cvd_signal(symbol, df)
    except Exception:
        cvd = {}

    try:
        macd = _ind("macd") or compute_macd_advanced(df)
    except Exception:
        macd = {}

    try:
        rsi = _ind("rsi") or compute_rsi_advanced(df)
    except Exception:
        rsi = {}

    try:
        wr = _ind("williams_r") or compute_williams_r(df)
    except Exception:
        wr = {}

    try:
        ob = _ind("orderbook") or get_orderbook_signal(
            symbol, float(df["close"].iloc[-1]), feeds
        )
    except Exception:
        ob = {}

    try:
        fr = _ind("funding") or get_funding_signal(symbol)
    except Exception:
        fr = {}

    try:
        oi = _ind("oi") or get_oi_signal(symbol, float(df["close"].iloc[-1]))
    except Exception:
        oi = {}

    try:
        liq = _ind("liq") or get_liquidation_signal(
            symbol,
            float(df["close"].iloc[-1]),
            oi_change_pct_4h=float(oi.get("oi_change_pct_4h", 0)),
            funding_rate=float(fr.get("funding_rate_current", 0)),
            ls_ratio=float(oi.get("ls_ratio", 1.0)),
        )
    except Exception:
        liq = {}

    sent = sentiment_cache or {}

    # ── price (8) ──────────────────────────────────────────────────────────
    closes = df["close"].values.astype(float)
    current = closes[-1]

    def _ret(n):
        if len(closes) > n:
            prev = closes[-(n + 1)]
            return (current - prev) / (prev + 1e-9)
        return 0.0

    result["price_return_1c"] = float(np.clip(_ret(1), -0.2, 0.2))
    result["price_return_5c"] = float(np.clip(_ret(5), -0.3, 0.3))
    result["price_return_15c"] = float(np.clip(_ret(15), -0.5, 0.5))
    result["price_atr_normalized"] = float(atr.get("atr_normalized", 0.0))
    result["price_vs_session_vwap_pct"] = float(
        np.clip(vwap.get("session_vwap_dist_pct", 0.0) / 10, -1, 1)
    )
    result["price_vs_weekly_vwap_pct"] = float(
        np.clip(vwap.get("weekly_vwap_dist_pct", 0.0) / 10, -1, 1)
    )

    high = float(df["high"].tail(20).max())
    low = float(df["low"].tail(20).min())
    result["price_high_low_range_pct"] = float((high - low) / (current + 1e-9))
    result["price_bb_position"] = float(_bb_position(df))

    # ── volume (6) ─────────────────────────────────────────────────────────
    result["vol_buy_ratio"] = float(np.clip(of.get("buy_volume_ratio", 0.5), 0, 1))
    result["vol_spike_5c"] = float(np.clip(of.get("volume_spike_5c", 1.0), 0, 10))
    result["vol_spike_20c"] = float(np.clip(of.get("volume_spike_20c", 1.0), 0, 5))
    result["vol_dollar_normalized"] = float(
        np.clip(of.get("dollar_volume_normalized", 1.0), 0, 10)
    )
    result["vol_trend_slope"] = float(np.clip(of.get("volume_trend_slope", 0.0), -5, 5))
    result["vol_at_price_pct"] = float(
        np.clip(of.get("volume_at_price_level_pct", 0.0), 0, 1)
    )

    # ── cvd (5) ────────────────────────────────────────────────────────────
    result["cvd_value_normalized"] = float(
        np.clip(cvd.get("cvd_value_normalized", 0.0), -5, 5)
    )
    result["cvd_slope_5c"] = float(np.clip(cvd.get("cvd_slope_5c", 0.0), -5, 5))
    result["cvd_slope_20c"] = float(np.clip(cvd.get("cvd_slope_20c", 0.0), -5, 5))
    div_type = cvd.get("cvd_divergence_type", "none")
    result["cvd_divergence"] = float(_DIV_MAP.get(div_type, 0.0))
    result["cvd_trend_aligned"] = 1.0 if cvd.get("cvd_trend_aligned", False) else 0.0

    # ── momentum (7) ───────────────────────────────────────────────────────
    result["mom_macd_hist_fast"] = float(
        np.clip(macd.get("macd_hist_3_15_3", 0.0) / (current + 1e-9) * 100, -5, 5)
    )
    result["mom_macd_hist_slow"] = float(
        np.clip(macd.get("macd_hist_6_20_5", 0.0) / (current + 1e-9) * 100, -5, 5)
    )
    result["mom_rsi_14"] = float(np.clip(rsi.get("rsi_14", 50.0) / 100, 0, 1))
    result["mom_rsi_21"] = float(np.clip(rsi.get("rsi_21", 50.0) / 100, 0, 1))
    result["mom_williams_r"] = float(
        np.clip((wr.get("williams_r_14", -50.0) + 100) / 100, 0, 1)
    )
    result["mom_macd_long_aligned"] = (
        1.0 if macd.get("macd_long_aligned", False) else 0.0
    )
    rsi_div = rsi.get("rsi_divergence_type", "none")
    result["mom_rsi_divergence"] = float(_DIV_MAP.get(rsi_div, 0.0))

    # ── vwap (4) ───────────────────────────────────────────────────────────
    result["vwap_session_dist_pct"] = float(
        np.clip(vwap.get("session_vwap_dist_pct", 0.0) / 5, -1, 1)
    )
    result["vwap_weekly_dist_pct"] = float(
        np.clip(vwap.get("weekly_vwap_dist_pct", 0.0) / 5, -1, 1)
    )
    result["vwap_band_position"] = float(
        np.clip(vwap.get("vwap_band_position", 0) / 3, -1, 1)
    )
    result["vwap_reclaim"] = 1.0 if vwap.get("vwap_reclaim_signal", False) else 0.0

    # ── orderbook (5) ──────────────────────────────────────────────────────
    result["ob_imbalance_l1"] = float(np.clip(ob.get("ob_imbalance_l1", 0.5), 0, 1))
    result["ob_imbalance_l5"] = float(np.clip(ob.get("ob_imbalance_l5", 0.5), 0, 1))
    result["ob_imbalance_l20"] = float(np.clip(ob.get("ob_imbalance_l20", 0.5), 0, 1))
    wall_above = ob.get("wall_above_dist_pct", 100.0)
    wall_below = ob.get("wall_below_dist_pct", 100.0)
    result["ob_wall_above_pct"] = float(
        np.clip(wall_above / 10, 0, 1)
    )  # normalize 0-10%
    result["ob_wall_below_pct"] = float(np.clip(wall_below / 10, 0, 1))

    # ── derivatives (6) ────────────────────────────────────────────────────
    result["deriv_funding_rate"] = float(
        np.clip(fr.get("funding_rate_current", 0.0) / 0.002, -1, 1)
    )
    result["deriv_carry_annual"] = float(
        np.clip(fr.get("carry_annual", 0.0) / 100, -1, 1)
    )
    oi_sig = oi.get("oi_signal", "neutral")
    result["deriv_oi_signal"] = float(_OI_SIGNAL_MAP.get(oi_sig, 0.0))
    result["deriv_ls_ratio"] = float(
        np.clip((oi.get("ls_ratio", 1.0) - 1.0) / 2, -1, 1)
    )
    skew_dir = sent.get("skew_direction", "neutral")
    result["deriv_skew_direction"] = float(_SKEW_MAP.get(skew_dir, 0.0))
    result["deriv_iv_rank"] = float(np.clip(sent.get("iv_pct_rank", 50.0) / 100, 0, 1))

    # ── liquidation (3) ────────────────────────────────────────────────────
    result["liq_long_dist_pct"] = float(
        np.clip(liq.get("nearest_long_liq_dist_pct", 10.0) / 10, 0, 1)
    )
    result["liq_short_dist_pct"] = float(
        np.clip(liq.get("nearest_short_liq_dist_pct", 10.0) / 10, 0, 1)
    )
    result["liq_cascade_risk"] = float(
        np.clip(liq.get("cascade_risk_score", 0) / 100, 0, 1)
    )

    # ── regime/sentiment (5) ───────────────────────────────────────────────
    result["regime_vol_mult"] = float(
        np.clip(atr.get("vol_regime_mult", 1.0), 0.5, 1.5)
    )
    result["regime_fg_current"] = float(np.clip(sent.get("fg_current", 50) / 100, 0, 1))
    result["regime_fg_momentum_7d"] = float(
        np.clip(sent.get("fg_momentum_7d", 0) / 50, -1, 1)
    )
    cc = rsi.get("rsi_centerline_cross", "none")
    result["regime_rsi_centerline"] = (
        1.0 if cc == "crossed_up" else (-1.0 if cc == "crossed_down" else 0.0)
    )
    result["regime_adx_normalized"] = float(
        np.clip(atr.get("atr_14", 0.0) / (current + 1e-9) * 100 / 50, 0, 1)
    )

    # ── time (4) ───────────────────────────────────────────────────────────
    now_utc = time.gmtime()
    hour = now_utc.tm_hour
    dow = now_utc.tm_wday  # 0=Mon
    result["time_hour_sin"] = float(math.sin(2 * math.pi * hour / 24))
    result["time_hour_cos"] = float(math.cos(2 * math.pi * hour / 24))
    result["time_day_of_week"] = float(dow / 6)
    # Session: 0=ASIA(0-8h), 0.5=LONDON(8-16h), 1.0=NY(16-24h)
    result["time_session"] = 0.0 if hour < 8 else (0.5 if hour < 16 else 1.0)

    # ── onchain (4) ────────────────────────────────────────────────────────
    whale_dir = sent.get("whale_signal", "neutral")
    result["onchain_whale_signal"] = float(_WHALE_DIR_MAP.get(whale_dir, 0.0))
    result["onchain_whale_strength"] = float(
        np.clip(sent.get("whale_strength", 0.0), 0, 1)
    )
    result["onchain_exchange_flow"] = float(
        np.clip(sent.get("exchange_flow", 0.0), -1, 1)
    )
    result["onchain_score"] = float(np.clip(sent.get("onchain_score", 0.5), 0, 1))

    # ── Validate and NaN-fill ─────────────────────────────────────────────
    for name in FEATURE_NAMES:
        val = result.get(name, 0.0)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            result[name] = 0.0
        else:
            result[name] = float(val)

    result["feature_ts"] = time.time()
    result["symbol"] = symbol

    # Shadow State Features (Manifest Sections 2.1, 2.2)
    # Injected after canonical FEATURE_NAMES — never interfere with the 57-feature array.
    # Defaults are fail-open during ~60s warmup after startup.
    try:
        from data.edge_monitor import get_shadow_state as _get_shadow

        _sym_clean = (
            str(result.get("symbol") or symbol or "")
            .upper()
            .replace("-USDC", "")
            .replace("-USDT", "")
            .replace("-USD", "")
            .replace("/", "")
            .replace("-", "")
        )
        _shadow = _get_shadow(_sym_clean)
        result["kalman_dev_pct"] = float(_shadow.get("kalman_dev_pct", 0.0))
        result["adf_stationary"] = bool(_shadow.get("adf_stationary", True))
        result["ou_halflife_bars"] = float(_shadow.get("ou_halflife_bars", 999.0))
    except Exception:
        result["kalman_dev_pct"] = 0.0
        result["adf_stationary"] = True
        result["ou_halflife_bars"] = 999.0

    return result


def to_array(features: Dict[str, float]) -> np.ndarray:
    """
    Convert feature dict → numpy array in canonical FEATURE_NAMES order.
    Used for model inference.
    """
    return np.array(
        [features.get(name, 0.0) for name in FEATURE_NAMES], dtype=np.float32
    )


def to_dataframe_row(features: Dict[str, float]) -> pd.DataFrame:
    """
    Convert feature dict → single-row DataFrame for sklearn/XGBoost.
    """
    row = {name: [features.get(name, 0.0)] for name in FEATURE_NAMES}
    return pd.DataFrame(row)


def describe_features(features: Dict[str, float]) -> str:
    """
    Human-readable feature summary for debugging / notifications.
    Highlights the strongest signals.
    """
    lines = []
    if features.get("cvd_divergence", 0) > 0:
        lines.append("CVD bullish divergence")
    if features.get("vwap_reclaim", 0) > 0:
        lines.append("VWAP reclaim signal")
    if features.get("mom_macd_long_aligned", 0) > 0:
        lines.append("MACD 3-variant aligned long")
    if features.get("ob_imbalance_l5", 0.5) > 0.6:
        lines.append(f"OB bullish L5 ({features['ob_imbalance_l5']:.2f})")
    rsi = features.get("mom_rsi_14", 0.5) * 100
    if rsi < 35:
        lines.append(f"RSI oversold ({rsi:.0f})")
    elif rsi > 70:
        lines.append(f"RSI overbought ({rsi:.0f})")
    cascade = features.get("liq_cascade_risk", 0) * 100
    if cascade > 60:
        lines.append(f"Cascade risk elevated ({cascade:.0f}/100)")
    return " | ".join(lines) if lines else "No strong signals"
