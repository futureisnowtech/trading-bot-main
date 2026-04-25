from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_sss01_strategy_symbols_can_enable_all_8_spot_names(monkeypatch):
    import config
    from runtime.spot_strategy import strategy_spot_symbols

    monkeypatch.setattr(
        config,
        "SPOT_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "SPOT_STRATEGY_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"],
        raising=False,
    )
    symbols = set(strategy_spot_symbols())
    assert symbols == {"BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"}


def test_sss02_final_score_is_symbol_specific():
    from runtime.spot_momentum import final_spot_score

    btc = final_spot_score(62.0, 54.0, regime="NEUTRAL", symbol="BTC")
    sol = final_spot_score(62.0, 54.0, regime="NEUTRAL", symbol="SOL")
    assert btc != sol
    assert btc > sol


def test_sss03_doge_requires_strong_opportunistic_setup_evidence():
    from runtime.spot_strategy import spot_quality_block_reason

    spot_state = {
        "regime": "TREND",
        "setup_family": "pullback_reclaim",
        "structural_confirm_count": 3,
        "frames": {
            "5m": {
                "v": 0.2,
                "a": 0.1,
                "frame_score": 64.0,
                "momentum_impulse": 0.2,
                "structure_component": 0.2,
                "path_efficiency": 0.5,
                "participation_component": 0.2,
            },
            "30m": {
                "v": 0.1,
                "frame_score": 60.0,
                "volatility_quality": 0.2,
            },
        },
    }
    reason, floor = spot_quality_block_reason(
        "DOGE",
        spot_state,
        final_spot_score=70.0,
    )
    assert reason == "edge_setup_family_mismatch"
    assert floor >= 58.0


def test_sss04_eth_uses_quick_exit_profile_targets():
    from runtime.spot_strategy import trail_arm_r_for_symbol, target_r_for_symbol

    assert target_r_for_symbol("ETH", "TREND") == 1.5
    assert trail_arm_r_for_symbol("ETH", "TREND") == 0.9


def test_sss05_link_edge_policy_exposes_replay_summary():
    from runtime.spot_strategy import edge_policy_for_symbol

    policy = edge_policy_for_symbol("LINK")
    assert policy["profile"] == "balanced"
    assert "setup = compression_breakout" in policy["conditions_summary"]
    assert policy["metrics"]["pf"] == 2.8022
