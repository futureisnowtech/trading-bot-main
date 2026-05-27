from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _spot_state():
    return {
        "regime": "TREND",
        "primary_setup": "wae_explosion",  # WAEx implies wae_explosion, clears setup gates
        "direction": "LONG",
        "vol_spike": 3.0,
        "adx_15m": 35.0,
        "kst_value": 1.0,
        "kst_signal": 0.0,
        "supertrend_dir": 1,
        "setup_family": "impulse_continuation",
        "setup_score": 0.98,
        "structural_confirm_count": 4,
        "frames": {
            "5m": {
                "frame_score": 85.0,
                "momentum_impulse": 0.45,
                "structure_component": 0.35,
                "path_efficiency": 0.55,
                "participation_component": 0.20,
                "atr_pct": 0.02,
                "v": 0.25,
            },
            "30m": {
                "frame_score": 75.0,
                "volatility_quality": 0.15,
            },
        },
    }


def test_stv01_monitor_only_tv_context_does_not_boost_score():
    from runtime.spot_momentum import final_spot_score

    base = final_spot_score(60.0, 56.0, regime="TREND", symbol="BTC")
    boosted = final_spot_score(
        60.0,
        56.0,
        regime="TREND",
        symbol="BTC",
        tv_context={
            "symbol": "BTC-USDC",
            "profile_name": "algobot_htf_v2",
            "htf_bias": "LONG",
            "tf_min": "240",
            "age_seconds": 60,
        },
    )
    assert boosted == base


def test_stv02_monitor_only_tv_context_does_not_veto_spot_long():
    from runtime.spot_strategy import spot_quality_block_reason

    reason, _ = spot_quality_block_reason(
        "BTC",
        _spot_state(),
        final_spot_score=70.0,
        tv_context={
            "symbol": "BTC-USDC",
            "profile_name": "algobot_htf_v2",
            "htf_bias": "SHORT",
            "tf_min": "240",
            "age_seconds": 60,
        },
    )
    assert reason == ""
