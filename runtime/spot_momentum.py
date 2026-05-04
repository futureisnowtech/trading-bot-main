"""
runtime/spot_momentum.py — multi-timeframe spot scalp state from repo-native indicators.
"""

from __future__ import annotations

import os
import sys
import importlib.util
import time
from typing import Any

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import (
    SPOT_ACCEL_IMPULSE_WINDOW,
    SPOT_FRAME_SCORE_ANCHOR,
    SPOT_MOMENTUM_IMPULSE_WINDOW,
    SPOT_NEUTRAL_SCORE_WEIGHT_COMPOSITE,
    SPOT_NEUTRAL_SCORE_WEIGHT_DERIVATIVE,
    SPOT_SCALP_SCORE_WEIGHT_COMPOSITE,
    SPOT_SCALP_SCORE_WEIGHT_DERIVATIVE,
    SPOT_STATE_CACHE_SECONDS,
)
from runtime.spot_strategy import final_score_for_symbol

try:
    from data.historical_data import get_candles
    from data.indicators import add_all_indicators
except Exception:
    _hist_spec = importlib.util.spec_from_file_location(
        "repo_historical_data", os.path.join(_ROOT, "data", "historical_data.py")
    )
    _hist_mod = importlib.util.module_from_spec(_hist_spec)
    assert _hist_spec and _hist_spec.loader
    _hist_spec.loader.exec_module(_hist_mod)
    get_candles = _hist_mod.get_candles

    _ind_spec = importlib.util.spec_from_file_location(
        "repo_indicators", os.path.join(_ROOT, "data", "indicators.py")
    )
    _ind_mod = importlib.util.module_from_spec(_ind_spec)
    assert _ind_spec and _ind_spec.loader
    _ind_spec.loader.exec_module(_ind_mod)
    add_all_indicators = _ind_mod.add_all_indicators
from runtime.spot_regime import classify_spot_regime


class SpotStateUnavailable(ValueError):
    """Raised when the spot state cannot be built from current market data."""


_STATE_CACHE: dict[str, dict[str, Any]] = {}


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def _ema_last(series: pd.Series, span: int = 3) -> float:
    if series.empty:
        return 0.0
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def _rolling_integral(series: pd.Series, window: int = 12) -> float:
    tail = pd.to_numeric(series, errors="coerce").fillna(0.0).tail(max(2, window))
    if tail.empty:
        return 0.0
    arr = tail.to_numpy(dtype=float)
    integral = float(np.trapezoid(arr, dx=1.0))
    normalizer = max(len(arr) - 1, 1)
    return integral / normalizer


def _positive_integral(series: pd.Series, window: int = 12) -> float:
    return _rolling_integral(series.clip(lower=0.0), window=window)


def _path_efficiency(series: pd.Series, window: int = 12) -> float:
    tail = pd.to_numeric(series, errors="coerce").fillna(0.0).tail(max(2, window))
    if len(tail) < 2:
        return 0.0
    arr = tail.to_numpy(dtype=float)
    path = float(np.abs(np.diff(arr)).sum())
    if path <= 1e-9:
        return 0.0
    net = float(abs(arr[-1] - arr[0]))
    return _clip(net / path, 0.0, 1.0)


def _safe_pct(num: float, den: float) -> float:
    return float(num / den) if den not in (0, 0.0) else 0.0


def _series_zscore(series: pd.Series, value: float, clip_abs: float = 3.0) -> float:
    if len(series) < 10:
        return _clip(value)
    mean = float(series.mean())
    std = float(series.std(ddof=0) or 0.0)
    if std <= 1e-9:
        return _clip(value)
    return _clip(((value - mean) / std) / clip_abs)


def _series_zscore_vector(series: pd.Series, clip_abs: float = 3.0) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if len(numeric) < 10:
        return numeric.clip(-1.0, 1.0)
    mean = float(numeric.mean())
    std = float(numeric.std(ddof=0) or 0.0)
    if std <= 1e-9:
        return numeric.clip(-1.0, 1.0)
    return (((numeric - mean) / std) / clip_abs).clip(-1.0, 1.0)


def _get_column(df: pd.DataFrame, *names: str, default: float = 0.0) -> pd.Series:
    for name in names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce").fillna(default)
    return pd.Series([default] * len(df), index=df.index, dtype=float)


def _bool_series(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df.columns:
        return df[name].astype(bool)
    return pd.Series([False] * len(df), index=df.index, dtype=bool)


def _clean_symbol(symbol: str) -> str:
    return (
        str(symbol or "")
        .upper()
        .replace("-USD", "")
        .replace("USD", "")
        .replace("USDT", "")
    )


def _timeframe_state_from_enriched_df(df: pd.DataFrame) -> dict[str, Any]:
    close = _get_column(df, "close")
    returns = np.log(close.replace(0, np.nan)).diff().fillna(0.0)
    macd_hist = _get_column(df, "macd_hist", "macd_histogram", default=0.0)
    kst = _get_column(df, "kst", default=0.0)
    kst_signal = _get_column(df, "kst_signal", default=0.0)
    avwap_dev = _get_column(df, "avwap_dev", default=0.0)
    rv_ratio = _get_column(df, "rv_ratio", default=1.0)
    ou_halflife = _get_column(df, "ou_halflife_minutes", default=15.0)
    autocorr = _get_column(df, "autocorr_ret", default=0.0)
    atr = _get_column(df, "atr", "atr_14", default=0.0)
    adx = _get_column(df, "adx", default=20.0)
    dollar_volume = _get_column(df, "dollar_volume", "volume", default=0.0)
    wae_up = _get_column(df, "wae_trend_up", default=0.0)
    wae_down = _get_column(df, "wae_trend_down", default=0.0)
    wae_explosion = _get_column(df, "wae_explosion", default=0.0)
    squeeze_direction = _get_column(df, "squeeze_direction", default=0.0)
    squeeze_on = _bool_series(df, "squeeze_on")
    squeeze_fired = _bool_series(df, "squeeze_fired")
    cloud_bullish = _bool_series(df, "cloud_bullish")
    cloud_bearish = _bool_series(df, "cloud_bearish")
    supertrend_bullish = _bool_series(df, "supertrend_bullish")

    kst_spread = (kst - kst_signal).fillna(0.0)
    kst_z = _series_zscore_vector(kst_spread)
    macd_z = _series_zscore_vector(macd_hist)
    avwap_component_series = (avwap_dev.fillna(0.0) * 2.0).clip(-1.0, 1.0)
    returns_z = _series_zscore_vector(returns)

    kst_component = float(kst_z.iloc[-1])
    macd_component = _series_zscore(macd_hist, float(macd_hist.iloc[-1]))
    vwap_component = _clip(float(avwap_dev.iloc[-1]) * 2.0)
    dollar_vol_log = np.log1p(dollar_volume.clip(lower=0.0))
    dollar_vol_z = _series_zscore_vector(dollar_vol_log)
    dollar_volume_component = float(dollar_vol_z.iloc[-1])
    wae_delta = (wae_up - wae_down) / wae_explosion.replace(0.0, np.nan)
    wae_delta = wae_delta.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    wae_z = _series_zscore_vector(wae_delta)
    wae_component = float(wae_z.iloc[-1])
    squeeze_state = pd.Series(
        np.where(
            squeeze_fired,
            np.sign(squeeze_direction).astype(float),
            np.where(squeeze_on, 0.25 * np.sign(squeeze_direction), 0.0),
        ),
        index=df.index,
        dtype=float,
    ).fillna(0.0)
    squeeze_component = float(squeeze_state.iloc[-1])
    ichimoku_component = (
        1.0
        if bool(cloud_bullish.iloc[-1])
        else -1.0
        if bool(cloud_bearish.iloc[-1])
        else 0.0
    )
    supertrend_component = (
        1.0
        if bool(supertrend_bullish.iloc[-1])
        else -1.0
        if "supertrend_bullish" in df.columns
        else 0.0
    )
    returns_component = _series_zscore(returns, float(returns.iloc[-1]))

    z_series = pd.Series(
        0.22 * kst_z
        + 0.20 * macd_z
        + 0.18 * avwap_component_series
        + 0.16
        * pd.Series(
            np.where(cloud_bullish, 1.0, np.where(cloud_bearish, -1.0, 0.0)),
            index=df.index,
            dtype=float,
        )
        + 0.14
        * pd.Series(
            np.where(
                supertrend_bullish,
                1.0,
                np.where("supertrend_bullish" in df.columns, -1.0, 0.0),
            ),
            index=df.index,
            dtype=float,
        )
        + 0.10 * returns_z
        + 0.10 * wae_z
        + 0.06 * squeeze_state
        + 0.02 * dollar_vol_z
        + 0.02 * ((adx.fillna(20.0) - 20.0) / 20.0).clip(-1.0, 1.0)
    )
    v_series = z_series.diff().fillna(0.0).ewm(span=3, adjust=False).mean()
    a_series = v_series.diff().fillna(0.0).ewm(span=3, adjust=False).mean()
    j_series = a_series.diff().fillna(0.0).ewm(span=3, adjust=False).mean()

    structure_component = _clip(
        0.45 * ichimoku_component
        + 0.35 * supertrend_component
        + 0.20 * (1.0 if float(kst_spread.iloc[-1]) > 0 else -1.0)
    )
    participation_component = _clip(
        0.55 * wae_component
        + 0.25 * dollar_volume_component
        + 0.20 * _clip((float(adx.iloc[-1]) - 20.0) / 20.0)
    )
    rv = float(rv_ratio.iloc[-1] or 1.0)
    rv_quality = 1.0 - min(abs(rv - 1.15) / 1.25, 1.0)
    autocorr_component = _clip(float(autocorr.iloc[-1] or 0.0) * 2.0)
    ou = float(ou_halflife.iloc[-1] or 15.0)
    ou_quality = 1.0 - min(abs(ou - 18.0) / 30.0, 1.0)
    volatility_quality = _clip(
        (
            0.45 * rv_quality
            + 0.30 * ((autocorr_component + 1.0) / 2.0)
            + 0.25 * ou_quality
        )
        * 2.0
        - 1.0
    )

    momentum_impulse = _clip(
        _positive_integral(z_series, window=SPOT_MOMENTUM_IMPULSE_WINDOW)
    )
    accel_impulse = _clip(
        _positive_integral(a_series, window=SPOT_ACCEL_IMPULSE_WINDOW)
    )
    path_efficiency = _path_efficiency(z_series, window=SPOT_MOMENTUM_IMPULSE_WINDOW)
    path_efficiency_component = _clip(path_efficiency * 2.0 - 1.0)

    z = float(z_series.iloc[-1])
    v = float(v_series.iloc[-1])
    a = float(a_series.iloc[-1])
    j = float(j_series.iloc[-1])
    frame_score = float(
        np.clip(
            SPOT_FRAME_SCORE_ANCHOR
            + 16.0 * z
            + 12.0 * v
            + 8.0 * a
            + 4.0 * j
            + 6.0 * momentum_impulse
            + 4.0 * accel_impulse
            + 6.0 * structure_component
            + 4.0 * participation_component
            + 3.0 * volatility_quality
            + 2.0 * path_efficiency_component,
            0.0,
            100.0,
        )
    )

    structural_confirms = {
        "kst": 1 if float(kst.iloc[-1]) > float(kst_signal.iloc[-1]) else 0,
        "ichimoku": 1 if bool(_bool_series(df, "cloud_bullish").iloc[-1]) else 0,
        "supertrend": 1 if bool(_bool_series(df, "supertrend_bullish").iloc[-1]) else 0,
    }

    return {
        "z": z,
        "v": v,
        "a": a,
        "j": j,
        "frame_score": frame_score,
        "rv_ratio": float(rv_ratio.iloc[-1] or 1.0),
        "ou_halflife_minutes": float(ou_halflife.iloc[-1] or 15.0),
        "autocorr_ret": float(autocorr.iloc[-1] or 0.0),
        "atr_pct": _safe_pct(float(atr.iloc[-1] or 0.0), float(close.iloc[-1] or 0.0)),
        "price": float(close.iloc[-1] or 0.0),
        "price_above_vwap": bool(float(avwap_dev.iloc[-1] or 0.0) >= 0.0),
        "momentum_impulse": float(momentum_impulse),
        "accel_impulse": float(accel_impulse),
        "path_efficiency": float(path_efficiency),
        "structure_component": float(structure_component),
        "participation_component": float(participation_component),
        "volatility_quality": float(volatility_quality),
        "wae_component": float(wae_component),
        "squeeze_component": float(squeeze_component),
        "dollar_volume_component": float(dollar_volume_component),
        "structural_confirms": structural_confirms,
        "structural_confirm_count": int(sum(structural_confirms.values())),
        "summary": (
            f"z={z:.2f}|v={v:.2f}|a={a:.2f}|j={j:.2f}|"
            f"imp={momentum_impulse:.2f}|score={frame_score:.1f}"
        ),
    }


def timeframe_state_from_history(
    df: pd.DataFrame,
    *,
    enriched: bool = False,
) -> dict[str, Any]:
    work = df.copy()
    if not enriched:
        work = add_all_indicators(work)
    return _timeframe_state_from_enriched_df(work)


def _timeframe_state(df: pd.DataFrame) -> dict[str, Any]:
    return timeframe_state_from_history(df, enriched=False)


def _build_spot_state_fresh(symbol: str) -> dict[str, Any]:
    frames = {
        "5m": get_candles(symbol, "5m", 200),
        "30m": get_candles(symbol, "30m", 200),
        "4h": get_candles(symbol, "4h", 200),
        "1d": get_candles(symbol, "1d", 200),
    }
    states: dict[str, dict[str, Any]] = {}
    for tf, df in frames.items():
        if df is None or len(df) < 50:
            raise SpotStateUnavailable(f"insufficient {tf} candles for {symbol}")
        states[tf] = _timeframe_state(df)

    regime = classify_spot_regime(states["30m"], states["4h"])
    derivative_score = (
        0.40 * states["5m"]["frame_score"]
        + 0.30 * states["30m"]["frame_score"]
        + 0.20 * states["4h"]["frame_score"]
        + 0.10 * states["1d"]["frame_score"]
    )
    confirm_count = max(
        states["5m"]["structural_confirm_count"],
        states["30m"]["structural_confirm_count"],
    )
    setup_candidates = classify_setup_candidates(states, regime)
    best_setup = (
        setup_candidates[0]
        if setup_candidates
        else {"family": "compression_breakout", "score": 0.0}
    )
    setup_family = str(best_setup.get("family") or "compression_breakout")
    setup_score = float(best_setup.get("score") or 0.0)
    return {
        "symbol": symbol,
        "frames": states,
        "regime": regime,
        "derivative_score": round(float(derivative_score), 2),
        "structural_confirm_count": int(confirm_count),
        "setup_family": setup_family,
        "setup_score": round(setup_score, 4),
        "setup_candidates": setup_candidates,
        "tf_5m_state": states["5m"]["summary"],
        "tf_30m_state": states["30m"]["summary"],
        "tf_4h_state": states["4h"]["summary"],
        "tf_1d_state": states["1d"]["summary"],
        "structural_confirms": ",".join(
            k for k, v in states["5m"]["structural_confirms"].items() if v
        ),
        "ou_halflife_minutes": float(states["30m"]["ou_halflife_minutes"]),
        "rv_ratio": float(states["30m"]["rv_ratio"]),
        "cache_stale": False,
        "state_source": "fresh",
        "data_warning": "",
    }


def build_spot_state(
    symbol: str,
    *,
    use_cache: bool = True,
    allow_stale: bool = False,
) -> dict[str, Any]:
    clean = _clean_symbol(symbol)
    now = time.time()
    cached = _STATE_CACHE.get(clean)
    if (
        use_cache
        and cached
        and (now - float(cached.get("ts") or 0.0) <= SPOT_STATE_CACHE_SECONDS)
    ):
        return dict(cached["state"])

    try:
        state = _build_spot_state_fresh(clean)
        _STATE_CACHE[clean] = {"ts": now, "state": dict(state)}
        return dict(state)
    except Exception as exc:
        if allow_stale and cached and cached.get("state"):
            stale = dict(cached["state"])
            stale["cache_stale"] = True
            stale["state_source"] = "stale_cache"
            stale["data_warning"] = f"stale_cache:{exc}"
            return stale
        if isinstance(exc, SpotStateUnavailable):
            raise
        raise SpotStateUnavailable(str(exc)) from exc


def warm_spot_state(symbol: str) -> dict[str, Any]:
    clean = _clean_symbol(symbol)
    try:
        state = build_spot_state(clean, use_cache=False, allow_stale=False)
        return {
            "symbol": clean,
            "ok": True,
            "state_source": state.get("state_source", "fresh"),
        }
    except Exception as exc:
        return {"symbol": clean, "ok": False, "reason": str(exc)}


def warm_spot_universe(symbols: list[str]) -> list[dict[str, Any]]:
    return [warm_spot_state(sym) for sym in symbols]


def classify_setup_candidates(
    states: dict[str, dict], regime: str
) -> list[dict[str, Any]]:
    s5 = states["5m"]
    s30 = states["30m"]
    s4 = states["4h"]
    candidates: list[dict[str, Any]] = []

    impulse_score = max(
        0.0,
        min(
            1.0,
            0.24 * max(float(s5["v"]), 0.0)
            + 0.18 * max(float(s5["a"]), 0.0)
            + 0.12 * max(float(s30["v"]), 0.0)
            + 0.14 * max(float(s5.get("momentum_impulse") or 0.0), 0.0)
            + 0.10 * max(float(s5.get("participation_component") or 0.0), 0.0)
            + 0.08 * max(float(s5.get("path_efficiency") or 0.0), 0.0)
            + 0.06 * float(s5.get("structural_confirm_count") or 0) / 3.0
            + (0.08 if bool(s5.get("price_above_vwap")) else 0.0),
        ),
    )
    if (
        float(s5["v"]) > 0
        and float(s5["a"]) > 0
        and float(s30["v"]) > 0
        and bool(s5.get("price_above_vwap"))
        and int(s5.get("structural_confirm_count") or 0) >= 2
    ):
        impulse_score = min(1.0, impulse_score + 0.18)
    candidates.append(
        {
            "family": "impulse_continuation",
            "score": round(impulse_score, 4),
            "reason": "5m/30m continuation with positive acceleration and participation",
        }
    )

    reclaim_score = max(
        0.0,
        min(
            1.0,
            0.18 * max(float(s30.get("frame_score") or 0.0) - 50.0, 0.0) / 20.0
            + 0.16 * max(float(s4.get("frame_score") or 0.0) - 50.0, 0.0) / 20.0
            + 0.14 * max(float(s5.get("structure_component") or 0.0), 0.0)
            + 0.12 * max(float(s5.get("path_efficiency") or 0.0), 0.0)
            + 0.10 * max(float(s30.get("path_efficiency") or 0.0), 0.0)
            + 0.08 * max(float(s5["z"]), 0.0)
            + (0.10 if regime != "CHOP" else 0.0)
            + (0.12 if bool(s5.get("price_above_vwap")) else 0.0),
        ),
    )
    candidates.append(
        {
            "family": "pullback_reclaim",
            "score": round(reclaim_score, 4),
            "reason": "trend context plus reclaim of structure / VWAP",
        }
    )

    compression_score = max(
        0.0,
        min(
            1.0,
            0.18 * max(float(s5.get("squeeze_component") or 0.0), 0.0)
            + 0.18 * max(float(s5["a"]), 0.0)
            + 0.12 * max(float(s5.get("accel_impulse") or 0.0), 0.0)
            + 0.12 * max(float(s5.get("participation_component") or 0.0), 0.0)
            + 0.10 * max(float(s30.get("volatility_quality") or 0.0), 0.0)
            + 0.10 * max(float(s5.get("wae_component") or 0.0), 0.0)
            + 0.08 * (1.0 - max(float(s30.get("path_efficiency") or 0.0), 0.0))
            + 0.08 * float(s5.get("structural_confirm_count") or 0) / 3.0,
        ),
    )
    candidates.append(
        {
            "family": "compression_breakout",
            "score": round(compression_score, 4),
            "reason": "compressed energy releasing into expansion",
        }
    )

    shakeout_score = max(
        0.0,
        min(
            1.0,
            0.16 * max(float(s5["z"]), 0.0)
            + 0.16 * max(float(s5["v"]), 0.0)
            + 0.12 * max(float(s30["v"]), 0.0)
            + 0.10 * max(float(s5.get("structure_component") or 0.0), 0.0)
            + 0.10 * max(float(s5.get("path_efficiency") or 0.0), 0.0)
            + 0.10 * max(float(s30.get("path_efficiency") or 0.0), 0.0)
            + 0.10 * max(float(s5.get("participation_component") or 0.0), 0.0)
            + (0.08 if regime != "CHOP" else 0.0),
        ),
    )
    candidates.append(
        {
            "family": "trend_resume_after_shakeout",
            "score": round(shakeout_score, 4),
            "reason": "trend regains structure after a local reset",
        }
    )

    retest_score = max(
        0.0,
        min(
            1.0,
            0.18 * max(float(s5.get("squeeze_component") or 0.0), 0.0)
            + 0.16 * max(float(s5["v"]), 0.0)
            + 0.12 * max(float(s5.get("accel_impulse") or 0.0), 0.0)
            + 0.12 * max(float(s5.get("path_efficiency") or 0.0), 0.0)
            + 0.12 * max(float(s30.get("volatility_quality") or 0.0), 0.0)
            + 0.10 * max(float(s5.get("participation_component") or 0.0), 0.0)
            + 0.10 * max(float(s5.get("structure_component") or 0.0), 0.0),
        ),
    )
    candidates.append(
        {
            "family": "compression_expansion_retest",
            "score": round(retest_score, 4),
            "reason": "breakout survives first retest with momentum intact",
        }
    )

    return sorted(candidates, key=lambda c: float(c.get("score") or 0.0), reverse=True)


def classify_setup_family(states: dict[str, dict], regime: str) -> str:
    s5 = states["5m"]
    s30 = states["30m"]
    if (
        float(s5.get("v") or 0.0) > 0
        and float(s5.get("a") or 0.0) > 0
        and float(s30.get("v") or 0.0) > 0
        and bool(s5.get("price_above_vwap"))
        and int(s5.get("structural_confirm_count") or 0) >= 2
    ):
        return "impulse_continuation"
    candidates = classify_setup_candidates(states, regime)
    return str(candidates[0]["family"]) if candidates else "compression_breakout"


def final_spot_score(
    existing_composite: float,
    derivative_score: float,
    regime: str = "NEUTRAL",
    symbol: str | None = None,
    direction: str = "LONG",
    tv_context: dict | None = None,
) -> float:
    if symbol:
        return final_score_for_symbol(
            symbol,
            existing_composite=existing_composite,
            derivative_score=derivative_score,
            regime=regime,
            direction=direction,
            tv_context=tv_context,
        )
    if str(regime or "NEUTRAL").upper() == "NEUTRAL":
        composite_w = SPOT_NEUTRAL_SCORE_WEIGHT_COMPOSITE
        derivative_w = SPOT_NEUTRAL_SCORE_WEIGHT_DERIVATIVE
    else:
        composite_w = SPOT_SCALP_SCORE_WEIGHT_COMPOSITE
        derivative_w = SPOT_SCALP_SCORE_WEIGHT_DERIVATIVE
    return round(
        float(existing_composite) * composite_w
        + float(derivative_score) * derivative_w,
        1,
    )
