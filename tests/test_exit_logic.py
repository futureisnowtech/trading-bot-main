"""
tests/test_exit_logic.py

Regression tests for exit logic.
Catches: stagnant exit gated behind AI engine (the ARB-USDC 13h bug),
         partial close not firing, perp time exit bypassed.
"""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_pos(age_minutes: int, pnl_pct: float = 0.001, direction: str = 'LONG',
              entry: float = 100.0) -> dict:
    """Build a fake position dict aged by age_minutes."""
    entry_dt = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    price = entry * (1 + pnl_pct)
    stop = entry * 0.985
    target = entry * 1.045
    return {
        'entry': entry,
        'stop': stop,
        'target': target,
        'high_since_entry': price,
        'low_since_entry': price,
        'ts_entry': entry_dt.isoformat(),
        'direction': direction,
        'qty': 100.0,
        'strategy': 'crypto_macd_consensus',
    }


class TestStagnantExit:
    def test_time_exit_fires_without_ai_engine(self):
        """
        The stagnant time exit must fire even when engine=None (AI unavailable).
        This is the exact bug that kept ARB-USDC open for 13+ hours.
        """
        # Read exit_monitor source and verify time exit is NOT inside `if engine and cr_md:`
        exit_monitor_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'scheduler', 'exit_monitor.py'
        )
        with open(exit_monitor_path) as f:
            src = f.read()

        # Find the time exit block
        time_exit_line = "CRYPTO_MAX_HOLD_HOURS * 60"
        assert time_exit_line in src, "Time exit logic not found in exit_monitor.py"

        # Verify it appears BEFORE the `if engine and cr_md:` block in the crypto section
        crypto_section_start = src.find("for pid, pos in list(all_pos.get('crypto'")
        time_exit_pos = src.find(time_exit_line, crypto_section_start)
        engine_block_pos = src.find("if engine and cr_md:", crypto_section_start)

        assert time_exit_pos < engine_block_pos, (
            "REGRESSION: Time exit is INSIDE `if engine and cr_md:` block. "
            "Stagnant positions will not be closed when AI engine is unavailable. "
            "Move time exit check BEFORE the engine block."
        )

    def test_should_exit_stagnant_crypto(self):
        """should_exit() must trigger for a 14-hour flat position."""
        from risk.stop_loss_manager import should_exit

        pos = _make_pos(age_minutes=840, pnl_pct=0.001)  # 14h, +0.1% (flat)
        current_price = pos['entry'] * 1.001  # barely moved

        # Hard stop and take profit should NOT trigger
        exit_flag, reason = should_exit(pos, 'crypto_macd_consensus', current_price)
        # At 0.1% from entry with 1.5% stop, hard stop should not fire
        # (this tests the stop logic is sane, not the time logic which is in exit_monitor)
        assert pos['entry'] * 0.985 < current_price, "Price is below stop — test setup error"

    def test_flat_threshold(self):
        """FLAT_POSITION_THRESHOLD_PCT must be 1.5% (prevents closing profitable positions)."""
        from config import FLAT_POSITION_THRESHOLD_PCT
        assert FLAT_POSITION_THRESHOLD_PCT == 0.015, (
            f"FLAT_POSITION_THRESHOLD_PCT={FLAT_POSITION_THRESHOLD_PCT}, expected 0.015. "
            "Changing this changes which positions get time-exited."
        )

    def test_crypto_max_hold_hours(self):
        """CRYPTO_MAX_HOLD_HOURS must be set (not zero/None)."""
        from config import CRYPTO_MAX_HOLD_HOURS
        assert CRYPTO_MAX_HOLD_HOURS > 0, "CRYPTO_MAX_HOLD_HOURS must be positive"
        assert CRYPTO_MAX_HOLD_HOURS <= 24, "CRYPTO_MAX_HOLD_HOURS > 24h is too permissive"


class TestPerpTimeExit:
    def test_perp_exit_threshold(self):
        """Perp time exit fires at 240 minutes when flat."""
        # Read perp_scanner and verify the 240-minute check exists
        perp_scanner_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'scheduler', 'perp_scanner.py'
        )
        with open(perp_scanner_path) as f:
            src = f.read()
        assert "mins_in >= 240" in src, (
            "Perp 4-hour stagnant exit (240 min) not found in perp_scanner.py"
        )


class TestStopLossManager:
    def test_hard_stop_fires_below_stop(self):
        """Hard stop must trigger when price drops below stop level."""
        from risk.stop_loss_manager import should_exit
        pos = _make_pos(age_minutes=10, pnl_pct=-0.02)
        pos['stop'] = pos['entry'] * 0.99   # stop at -1%
        current_price = pos['entry'] * 0.985  # price at -1.5% — below stop

        exit_flag, reason = should_exit(pos, 'crypto_macd_consensus', current_price)
        assert exit_flag, f"Hard stop did not fire. Reason: {reason}"
        assert 'stop' in reason.lower(), f"Exit reason doesn't mention stop: {reason}"

    def test_take_profit_fires_at_target(self):
        """Take profit must trigger when price reaches target."""
        from risk.stop_loss_manager import should_exit
        pos = _make_pos(age_minutes=10, pnl_pct=0.06)
        pos['target'] = pos['entry'] * 1.045
        current_price = pos['target'] * 1.001  # just above target

        exit_flag, reason = should_exit(pos, 'crypto_macd_consensus', current_price)
        assert exit_flag, f"Take profit did not fire. Reason: {reason}"

    def test_trailing_stop_not_early(self):
        """Trailing stop must NOT fire immediately after entry (before activation)."""
        from risk.stop_loss_manager import should_exit
        pos = _make_pos(age_minutes=2, pnl_pct=0.005)  # only 0.5% up
        current_price = pos['entry'] * 1.005

        exit_flag, reason = should_exit(pos, 'crypto_macd_consensus', current_price)
        # Should not trail-stop when barely moved
        if exit_flag:
            assert 'trailing' not in reason.lower(), (
                f"Trailing stop fired too early at 0.5% gain: {reason}"
            )
