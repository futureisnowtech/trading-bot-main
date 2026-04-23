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
    SPOT_SCALP_SCORE_WEIGHT_COMPOSITE,
    SPOT_SCALP_SCORE_WEIGHT_DERIVATIVE,
    SPOT_STATE_CACHE_SECONDS,
)
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
    return str(symbol or "").upper().replace("-USD", "").replace("USD", "").replace("USDT", "")


def _timeframe_state(df: pd.DataFrame) -> dict[str, Any]:
    df = add_all_indicators(df.copy())
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

    kst_component = _series_zscore(kst - kst_signal, float((kst - kst_signal).iloc[-1]))
    macd_component = _series_zscore(macd_hist, float(macd_hist.iloc[-1]))
    vwap_component = _clip(float(avwap_dev.iloc[-1]) * 2.0)
    ichimoku_component = (
        1.0
        if bool(_bool_series(df, "cloud_bullish").iloc[-1])
        else -1.0
        if bool(_bool_series(df, "cloud_bearish").iloc[-1])
        else 0.0
    )
    supertrend_component = (
        1.0
        if bool(_bool_series(df, "supertrend_bullish").iloc[-1])
        else -1.0
        if "supertrend_bullish" in df.columns
        else 0.0
    )
    returns_component = _series_zscore(returns, float(returns.iloc[-1]))

    z_series = pd.Series(
        (
            0.22 * _series_zscore(kst - kst_signal, x)
            + 0.20 * _series_zscore(macd_hist, y)
            + 0.18 * _clip(float(v) * 2.0)
            + 0.16 * (
                1.0
                if bool(cb)
                else -1.0
                if bool(cbr)
                else 0.0
            )
            + 0.14 * (
                1.0
                if bool(stb)
                else -1.0
                if "supertrend_bullish" in df.columns
                else 0.0
            )
            + 0.10 * _series_zscore(returns, r)
        )
        for x, y, v, cb, cbr, stb, r in zip(
            (kst - kst_signal).fillna(0.0),
            macd_hist.fillna(0.0),
            avwap_dev.fillna(0.0),
            _bool_series(df, "cloud_bullish"),
            _bool_series(df, "cloud_bearish"),
            _bool_series(df, "supertrend_bullish"),
            returns.fillna(0.0),
        )
    )
    v_series = z_series.diff().fillna(0.0).ewm(span=3, adjust=False).mean()
    a_series = v_series.diff().fillna(0.0).ewm(span=3, adjust=False).mean()

    z = float(z_series.iloc[-1])
    v = float(v_series.iloc[-1])
    a = float(a_series.iloc[-1])
    frame_score = float(np.clip(50.0 + 22.0 * z + 14.0 * v + 10.0 * a, 0.0, 100.0))

    structural_confirms = {
        "kst": 1 if float(kst.iloc[-1]) > float(kst_signal.iloc[-1]) else 0,
        "ichimoku": 1 if bool(_bool_series(df, "cloud_bullish").iloc[-1]) else 0,
        "supertrend": 1 if bool(_bool_series(df, "supertrend_bullish").iloc[-1]) else 0,
    }

    return {
        "z": z,
        "v": v,
        "a": a,
        "frame_score": frame_score,
        "rv_ratio": float(rv_ratio.iloc[-1] or 1.0),
        "ou_halflife_minutes": float(ou_halflife.iloc[-1] or 15.0),
        "autocorr_ret": float(autocorr.iloc[-1] or 0.0),
        "atr_pct": _safe_pct(float(atr.iloc[-1] or 0.0), float(close.iloc[-1] or 0.0)),
        "price": float(close.iloc[-1] or 0.0),
        "price_above_vwap": bool(float(avwap_dev.iloc[-1] or 0.0) >= 0.0),
        "structural_confirms": structural_confirms,
        "structural_confirm_count": int(sum(structural_confirms.values())),
        "summary": f"z={z:.2f}|v={v:.2f}|a={a:.2f}|score={frame_score:.1f}",
    }


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
    setup_family = classify_setup_family(states, regime)
    return {
        "symbol": symbol,
        "frames": states,
        "regime": regime,
        "derivative_score": round(float(derivative_score), 2),
        "structural_confirm_count": int(confirm_count),
        "setup_family": setup_family,
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
    if use_cache and cached and (now - float(cached.get("ts") or 0.0) <= SPOT_STATE_CACHE_SECONDS):
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
        return {"symbol": clean, "ok": True, "state_source": state.get("state_source", "fresh")}
    except Exception as exc:
        return {"symbol": clean, "ok": False, "reason": str(exc)}


def warm_spot_universe(symbols: list[str]) -> list[dict[str, Any]]:
    return [warm_spot_state(sym) for sym in symbols]


def classify_setup_family(states: dict[str, dict], regime: str) -> str:
    s5 = states["5m"]
    s30 = states["30m"]
    s4 = states["4h"]
    if (
        s5["v"] > 0
        and s5["a"] > 0
        and s30["v"] > 0
        and s5["price_above_vwap"]
        and s5["structural_confirm_count"] >= 2
    ):
        return "impulse_continuation"
    if (
        regime != "CHOP"
        and s30["frame_score"] >= 55
        and s5["z"] > 0
        and s5["price_above_vwap"]
    ):
        return "pullback_reclaim"
    return "compression_breakout"


def final_spot_score(existing_composite: float, derivative_score: float) -> float:
    return round(
        float(existing_composite) * SPOT_SCALP_SCORE_WEIGHT_COMPOSITE
        + float(derivative_score) * SPOT_SCALP_SCORE_WEIGHT_DERIVATIVE,
        1,
    )
