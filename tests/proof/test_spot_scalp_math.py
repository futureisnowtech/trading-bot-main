from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_ssm01_final_spot_score_blends_composite_and_derivative():
    from runtime.spot_momentum import final_spot_score

    assert final_spot_score(60.0, 80.0, regime="TREND") == 64.0


def test_ssm01b_final_spot_score_neutral_leans_on_composite():
    from runtime.spot_momentum import final_spot_score

    assert final_spot_score(62.7, 46.7, regime="NEUTRAL") == 61.1


def test_ssm02_regime_classifier_trend():
    from runtime.spot_regime import classify_spot_regime

    state_30m = {"frame_score": 56.0, "v": 0.12, "z": 0.25, "rv_ratio": 1.0}
    state_4h = {"frame_score": 60.0, "v": 0.05}
    assert classify_spot_regime(state_30m, state_4h) == "TREND"


def test_ssm03_setup_family_impulse_continuation():
    from runtime.spot_momentum import classify_setup_family

    states = {
        "5m": {
            "v": 0.20,
            "a": 0.08,
            "price_above_vwap": True,
            "structural_confirm_count": 2,
            "z": 0.30,
        },
        "30m": {"v": 0.10, "frame_score": 62.0},
        "4h": {"frame_score": 58.0},
    }
    assert classify_setup_family(states, "TREND") == "impulse_continuation"


def test_ssm04_score_floor_softens_for_clean_impulse():
    from runtime.spot_regime import score_floor_for_regime

    assert (
        score_floor_for_regime(
            "NEUTRAL", structural_confirm_count=2, setup_family="impulse_continuation"
        )
        == 48.0
    )
    assert (
        score_floor_for_regime(
            "CHOP", structural_confirm_count=2, setup_family="compression_breakout"
        )
        == 48.0
    )


def test_ssm05_timeframe_state_reports_impulse_and_path_metrics():
    import pandas as pd
    import numpy as np
    from runtime.spot_momentum import timeframe_state_from_history

    idx = pd.date_range("2026-01-01", periods=160, freq="5min", tz="UTC")
    base = np.linspace(100, 112, len(idx))
    df = pd.DataFrame(
        {
            "open": base - 0.2,
            "high": base + 0.4,
            "low": base - 0.4,
            "close": base,
            "volume": np.linspace(1_000, 5_000, len(idx)),
        },
        index=idx,
    )
    state = timeframe_state_from_history(df)
    assert "momentum_impulse" in state
    assert "accel_impulse" in state
    assert "path_efficiency" in state
    assert "j" in state
    assert 0.0 <= state["frame_score"] <= 100.0
