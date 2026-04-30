from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_ssp01_spot_econ_separates_quality_from_economics():
    from risk.spot_economics_gate import check_spot_economics

    result = check_spot_economics(
        symbol="XRP",
        size_usd=100.0,
        final_spot_score=34.0,
        stop_pct=0.015,
        target_r=1.2,
        spread_pct=0.0010,
        bid_depth_usd=10_000.0,
        ask_depth_usd=10_000.0,
        regime="NEUTRAL",
        structural_confirm_count=2,
        setup_family="impulse_continuation",
    )

    assert result["approved"] is False
    assert result["reason"] == "below_regime_floor"
    assert result["gate_class"] == "quality"
    assert result["score_floor"] == pytest.approx(60.0)


def test_ssp02_spot_econ_marks_spread_fail_as_microstructure():
    from risk.spot_economics_gate import check_spot_economics

    result = check_spot_economics(
        symbol="ETH",
        size_usd=200.0,
        final_spot_score=70.0,
        stop_pct=0.012,
        target_r=1.2,
        spread_pct=0.0030,
        bid_depth_usd=20_000.0,
        ask_depth_usd=20_000.0,
        regime="NEUTRAL",
        structural_confirm_count=3,
        setup_family="impulse_continuation",
    )

    assert result["approved"] is False
    assert result["reason"] == "spread_cap_exceeded"
    assert result["gate_class"] == "microstructure"


def test_ssp03_build_spot_state_can_fall_back_to_stale_cache(monkeypatch):
    import runtime.spot_momentum as sm

    idx = pd.date_range("2026-04-23", periods=80, freq="5min")
    candles = pd.DataFrame(
        {
            "open": [100.0 + i * 0.1 for i in range(80)],
            "high": [100.4 + i * 0.1 for i in range(80)],
            "low": [99.6 + i * 0.1 for i in range(80)],
            "close": [100.0 + i * 0.1 for i in range(80)],
            "volume": [1000.0] * 80,
        },
        index=idx,
    )

    def _add_indicators(df):
        df = df.copy()
        df["macd_hist"] = 0.05
        df["kst"] = 1.0
        df["kst_signal"] = 0.8
        df["avwap_dev"] = 0.02
        df["rv_ratio"] = 1.0
        df["ou_halflife_minutes"] = 12.0
        df["autocorr_ret"] = 0.1
        df["atr"] = 1.0
        df["cloud_bullish"] = True
        df["cloud_bearish"] = False
        df["supertrend_bullish"] = True
        return df

    monkeypatch.setattr(sm, "get_candles", lambda symbol, tf, bars: candles.copy())
    monkeypatch.setattr(sm, "add_all_indicators", _add_indicators)

    fresh = sm.build_spot_state("ETH", use_cache=False, allow_stale=False)
    assert fresh["cache_stale"] is False

    monkeypatch.setattr(
        sm, "get_candles", lambda symbol, tf, bars: candles.head(10).copy()
    )
    stale = sm.build_spot_state("ETH", use_cache=False, allow_stale=True)
    assert stale["cache_stale"] is True
    assert stale["state_source"] == "stale_cache"


def test_ssp04_quality_gate_open_before_calibration():
    """
    pullback_reclaim is a hard tiny-live quarantine and impulse_continuation
    should still avoid quarantine semantics even if another quality gate later
    blocks the candidate.
    """
    from unittest.mock import patch

    from runtime.spot_strategy import spot_quality_block_reason

    _frames = {
        "5m": {
            "v": 0.2,
            "a": 0.1,
            "frame_score": 58.0,
            "momentum_impulse": 0.3,
            "structure_component": 0.2,
            "path_efficiency": 0.4,
            "participation_component": 0.1,
        },
        "30m": {"v": 0.1, "frame_score": 57.0, "volatility_quality": 0.0},
    }

    # pullback_reclaim remains hard quarantined in tiny-live mode
    pr_state = {
        "symbol": "BTC",
        "regime": "NEUTRAL",
        "setup_family": "pullback_reclaim",
        "setup_score": 0.92,
        "structural_confirm_count": 2,
        "frames": _frames,
    }
    with patch("config.SPOT_PULLBACK_RECLAIM_NEUTRAL_BLOCKED", True):
        reason, _ = spot_quality_block_reason("BTC", pr_state, final_spot_score=65.0)
    assert reason == "pullback_reclaim_quarantined", (
        f"pullback_reclaim must be quarantined in tiny live; got: {reason!r}"
    )

    # impulse_continuation in NEUTRAL must always have open gate (no edge blocks)
    ic_state = {
        "symbol": "BTC",
        "regime": "NEUTRAL",
        "setup_family": "impulse_continuation",
        "setup_score": 0.92,
        "structural_confirm_count": 2,
        "frames": _frames,
    }
    ic_reason, _ = spot_quality_block_reason("BTC", ic_state, final_spot_score=65.0)
    assert "quarantined" not in ic_reason, (
        f"impulse_continuation NEUTRAL must not be quarantined; got: {ic_reason!r}"
    )
