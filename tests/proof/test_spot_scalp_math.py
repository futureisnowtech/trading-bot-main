from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_ssm01_final_spot_score_blends_composite_and_derivative():
    from runtime.spot_momentum import final_spot_score

    assert final_spot_score(60.0, 80.0) == 68.0


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
