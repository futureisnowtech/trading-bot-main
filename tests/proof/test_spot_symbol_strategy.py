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


def test_sss02_final_score_is_regime_weighted_under_tiny_live_defaults():
    from runtime.spot_momentum import final_spot_score

    btc = final_spot_score(62.0, 54.0, regime="NEUTRAL", symbol="BTC")
    sol = final_spot_score(62.0, 54.0, regime="NEUTRAL", symbol="SOL")
    assert btc == sol
    assert btc == 62.0


def test_sss03_doge_open_gate_before_calibration():
    """
    Before the calibrator has accumulated >= 30 real trades, DOGE must have an
    open gate (no edge conditions) so trades are not blocked by stale backtest data.
    The candidate still has to satisfy the active tiny-live setup contract.
    """
    from runtime.spot_strategy import spot_quality_block_reason

    spot_state = {
        "regime": "TREND",
        "setup_family": "impulse_continuation",
        "vol_spike": 1.1,
        "kst_value": 1.0,
        "kst_signal": 0.5,
        "supertrend_dir": 1,
        "structural_confirm_count": 3,
        "frames": {
            "5m": {
                "v": 0.2,
                "a": 0.1,
                "atr_pct": 0.015,
                "buy_volume_ratio": 0.55,
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
    assert reason == "", f"Expected open gate (empty reason), got: {reason!r}"
    assert floor == 48.0


def test_sss04_eth_uses_precision_exit_profile_targets():
    from runtime.spot_strategy import (
        exit_profile_for_symbol,
        trail_arm_r_for_symbol,
        target_r_for_symbol,
    )

    assert exit_profile_for_symbol("ETH", "TREND") == "precision"
    assert target_r_for_symbol("ETH", "TREND") == 3.0
    assert trail_arm_r_for_symbol("ETH", "TREND") == 1.2


def test_sss05_link_edge_policy_open_gate_before_calibration():
    """
    Before the calibrator has accumulated >= 30 real trades for LINK, the edge
    policy must have no active conditions (open gate) so the symbol can trade.
    The edge_metrics snapshot from the original backtest is preserved for reference.
    """
    from runtime.spot_strategy import edge_policy_for_symbol

    policy = edge_policy_for_symbol("LINK")
    assert policy["profile"] == "balanced"
    # No conditions until calibrator derives them from >= 30 real trades
    assert policy["conditions"] == (), (
        f"Expected empty conditions, got: {policy['conditions']}"
    )
    assert policy["conditions_summary"] == ""
    # Backtest snapshot still present for reference
    assert policy["metrics"]["pf"] == 2.8022


def test_sss06_score_floor_is_strategy_constant_under_tiny_live_defaults():
    from runtime.spot_strategy import score_floor_for_symbol

    base = score_floor_for_symbol("BTC", "TREND")
    synthetic = score_floor_for_symbol("BTC", "TREND", synthetic_candidate=True)
    taker = score_floor_for_symbol(
        "BTC",
        "TREND",
        execution_route="taker_fallback",
    )
    synthetic_taker = score_floor_for_symbol(
        "BTC",
        "TREND",
        execution_route="taker_fallback",
        synthetic_candidate=True,
    )

    assert base == 48.0
    assert synthetic == 48.0
    assert taker == 48.0
    assert synthetic_taker == 48.0
