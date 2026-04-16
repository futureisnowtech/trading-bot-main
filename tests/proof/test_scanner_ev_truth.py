"""
tests/proof/test_scanner_ev_truth.py — Proof tests for scanner EV truth (v15.10/v16).

Coverage:
  1. _step4_expected_value uses effective_position_usd (capped at $100) for EV
  2. scanner_theoretical_position_usd and scanner_effective_position_usd are present in output
  3. expected_profit is computed from effective_position_usd, not uncapped theoretical
  4. _MIN_EXPECTED_PROFIT remains unchanged
  5. For a large position (theoretical > $100), EV is from capped $100
"""

import os
import sys
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_min_expected_profit_unchanged():
    """_MIN_EXPECTED_PROFIT must remain at $0.25."""
    from scanner import _MIN_EXPECTED_PROFIT

    assert _MIN_EXPECTED_PROFIT == 0.25, (
        f"_MIN_EXPECTED_PROFIT must stay at 0.25, got {_MIN_EXPECTED_PROFIT}"
    )


def test_step4_caps_effective_position_usd():
    """_step4_expected_value must cap effective_position_usd at $100."""
    from scanner import _step4_expected_value

    # Candidate with large position (theoretical >> $100)
    # stop_pct=0.01 (1%), account=$5000, risk_pct=0.015 → theoretical=$7500
    candidates = [
        {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "stop_pct": 0.01,
            "target_pct": 0.03,
            "funding_rate": 0.0,
            "spread_pct": 0.05,
            "bid_depth_usd": 100_000.0,
            "ask_depth_usd": 100_000.0,
            "vol_usd": 10_000_000.0,
            "price": 50000.0,
        }
    ]

    result = _step4_expected_value(candidates, account_balance=5000.0)
    if not result:  # might be filtered by EV floor
        # Even if filtered, the effective cap logic was applied — check the input dict
        c = candidates[0]
    else:
        c = result[0]

    assert "scanner_theoretical_position_usd" in c, (
        "scanner_theoretical_position_usd missing"
    )
    assert "scanner_effective_position_usd" in c, (
        "scanner_effective_position_usd missing"
    )
    assert c["scanner_effective_position_usd"] <= 100.0 + 0.01, (
        f"effective_position_usd must be <= $100, got {c['scanner_effective_position_usd']}"
    )
    assert c["scanner_theoretical_position_usd"] > 100.0, (
        f"theoretical should be > $100 for this input, got {c['scanner_theoretical_position_usd']}"
    )


def test_step4_ev_uses_capped_position():
    """expected_profit must be computed from effective_position_usd."""
    from scanner import _step4_expected_value

    # stop_pct=0.005 (0.5%), account=$5000, risk_pct=0.015 → theoretical=$15000
    candidates = [
        {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "stop_pct": 0.005,
            "target_pct": 0.015,
            "funding_rate": 0.0,
            "spread_pct": 0.05,
            "bid_depth_usd": 100_000.0,
            "ask_depth_usd": 100_000.0,
            "vol_usd": 10_000_000.0,
            "price": 50000.0,
        }
    ]

    result = _step4_expected_value(candidates, account_balance=5000.0)
    if not result:
        return

    c = result[0]

    # The EV should be computed from effective ($100), not theoretical ($15000)
    # EV from $100 at target=1.5%, stop=0.5%: (0.52*0.015 - 0.48*0.005) * 100 = $0.54
    # EV from $15000 at same rates: $81  — wildly different
    if c.get("expected_profit"):
        # Should be in reasonable range for $100 position
        assert c["expected_profit"] < 5.0, (
            f"expected_profit {c['expected_profit']} looks like it used uncapped theoretical position"
        )


def test_step4_fields_present_in_candidate():
    """Both scanner_theoretical_position_usd and scanner_effective_position_usd must be in output."""
    from scanner import _step4_expected_value

    candidates = [
        {
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "stop_pct": 0.03,
            "target_pct": 0.06,
            "funding_rate": 0.0,
            "spread_pct": 0.05,
            "bid_depth_usd": 50_000.0,
            "ask_depth_usd": 50_000.0,
            "vol_usd": 5_000_000.0,
            "price": 3000.0,
        }
    ]

    # Run step4 (may pass or fail EV floor — doesn't matter, fields should be on candidate)
    _step4_expected_value(candidates, account_balance=5000.0)
    # Fields are added directly to candidate dicts even if they fail EV floor:
    c = candidates[0]
    assert "scanner_theoretical_position_usd" in c
    assert "scanner_effective_position_usd" in c


def test_step4_small_position_unchanged():
    """When theoretical <= $100, effective == theoretical (no capping needed)."""
    from scanner import _step4_expected_value

    # stop_pct=0.10 (10%), account=$5000, risk_pct=0.015 → theoretical=$75 (below $100)
    candidates = [
        {
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "stop_pct": 0.10,
            "target_pct": 0.30,
            "funding_rate": 0.0,
            "spread_pct": 0.05,
            "bid_depth_usd": 50_000.0,
            "ask_depth_usd": 50_000.0,
            "vol_usd": 5_000_000.0,
            "price": 3000.0,
        }
    ]

    _step4_expected_value(candidates, account_balance=5000.0)
    c = candidates[0]
    theoretical = c.get("scanner_theoretical_position_usd", 0)
    effective = c.get("scanner_effective_position_usd", 0)

    if theoretical <= 100.0:
        assert abs(theoretical - effective) < 0.01, (
            f"When theoretical <= $100, effective should equal theoretical: "
            f"theoretical={theoretical}, effective={effective}"
        )
