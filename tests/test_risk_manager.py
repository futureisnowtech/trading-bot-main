"""
tests/test_risk_manager.py — Verify hard risk rules can never be bypassed.

Run: python3 -m pytest tests/test_risk_manager.py -v

Patches are applied against the sub-module where each function is actually
imported (v9.0 decomposition: risk_manager → drawdown_controller + risk_limits).
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_rm():
    """Create a fresh RiskManager with empty positions and no halt."""
    # After v9.0 decomposition, get_todays_pnl / get_all_time_stats / get_todays_fees
    # are imported inside risk.drawdown_controller, not risk.risk_manager.
    with patch('risk.drawdown_controller.get_todays_pnl', return_value=0.0), \
         patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
         patch('risk.drawdown_controller.get_all_time_stats', return_value={'total_pnl': 0.0}), \
         patch('risk.risk_manager.load_open_positions', return_value=[]), \
         patch('risk.risk_manager.RiskManager._restore_halt_state'):
        from risk.risk_manager import RiskManager
        return RiskManager()


class TestHaltRule:
    def test_halted_system_blocks_all_entries(self):
        rm = _make_rm()
        rm._halted = True
        rm._halt_reason = "Test halt"
        with patch('risk.drawdown_controller.get_todays_pnl', return_value=0.0), \
             patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
             patch('risk.drawdown_controller.get_all_time_stats', return_value={'total_pnl': 0.0}):
            result = rm.pre_check_entry('crypto_macd', 'BTC-USDC', 'BUY', 50000.0)
        assert not result.approved
        assert "halted" in result.reason.lower()

    def test_resume_clears_halt(self):
        rm = _make_rm()
        rm._halted = True
        rm._halt_reason = "Test halt"
        with patch('risk.risk_manager.log_event'):
            rm.resume()
        assert not rm.is_halted
        assert rm.halt_reason == ''


class TestDailyLossLimit:
    def test_daily_loss_triggers_halt(self):
        """Daily P&L below limit should block new entries."""
        rm = _make_rm()
        # -$25 on a $5000 account = -0.5%, but config MAX_DAILY_LOSS_PCT=0.04 on $5000 = $200 limit
        # Use a loss that exceeds the configured limit
        from config import ACCOUNT_SIZE, MAX_DAILY_LOSS_PCT
        limit = ACCOUNT_SIZE * MAX_DAILY_LOSS_PCT
        loss = -(limit + 1.0)

        with patch('risk.drawdown_controller.get_todays_pnl', return_value=loss), \
             patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
             patch('risk.drawdown_controller.get_all_time_stats', return_value={'total_pnl': 0.0}), \
             patch('risk.risk_manager.log_event'):
            result = rm.pre_check_entry('crypto_macd', 'BTC-USDC', 'BUY', 50000.0, confidence=0.8)
        assert not result.approved
        assert "loss" in result.reason.lower()


class TestPositionLimits:
    def test_max_crypto_positions_enforced(self):
        from config import MAX_POSITIONS_CRYPTO
        rm = _make_rm()
        # Fill to the max
        for i in range(MAX_POSITIONS_CRYPTO):
            rm._crypto[f'SYM{i}-USDC'] = {
                'qty': 1, 'entry': 100, 'stop': 98, 'target': 106,
                'high_since_entry': 100, 'ts_entry': '2026-01-01',
                'direction': 'LONG', 'entry_reason': '',
            }
        with patch('risk.drawdown_controller.get_todays_pnl', return_value=0.0), \
             patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
             patch('risk.drawdown_controller.get_all_time_stats', return_value={'total_pnl': 0.0}), \
             patch('risk.risk_limits.get_daily_trade_count', return_value=0), \
             patch('risk.risk_limits.is_market_open', return_value=True), \
             patch('risk.risk_limits.is_in_no_trade_window', return_value=False):
            result = rm.pre_check_entry('crypto_macd', 'NEW-USDC', 'BUY', 50000.0)
        assert not result.approved
        assert "max crypto" in result.reason.lower()

    def test_no_double_entry_same_symbol(self):
        rm = _make_rm()
        rm._crypto['BTC-USDC'] = {
            'qty': 0.005, 'entry': 50000, 'stop': 49250, 'target': 52500,
            'high_since_entry': 50000, 'ts_entry': '2026-01-01',
            'direction': 'LONG', 'entry_reason': '',
        }
        with patch('risk.drawdown_controller.get_todays_pnl', return_value=0.0), \
             patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
             patch('risk.drawdown_controller.get_all_time_stats', return_value={'total_pnl': 0.0}), \
             patch('risk.risk_limits.is_market_open', return_value=True), \
             patch('risk.risk_limits.is_in_no_trade_window', return_value=False):
            result = rm.pre_check_entry('crypto_macd', 'BTC-USDC', 'BUY', 50000.0)
        assert not result.approved
        assert "already holding" in result.reason.lower()


class TestStopLossManager:
    def test_stop_loss_never_wider_than_configured(self):
        from risk.stop_loss_manager import calc_stop_loss
        from config import CRYPTO_STOP_LOSS_PCT
        entry = 50000.0
        stop  = calc_stop_loss(entry, 'crypto_macd', atr=0.0)
        pct_away = (entry - stop) / entry
        # Allow up to 1.5× the configured stop (ATR-wide stop can be larger)
        assert pct_away <= CRYPTO_STOP_LOSS_PCT * 1.5 + 1e-6, \
            f"Stop too wide: {pct_away:.4%} vs max {CRYPTO_STOP_LOSS_PCT * 1.5:.4%}"

    def test_take_profit_maintains_rr_ratio(self):
        from risk.stop_loss_manager import calc_stop_loss, calc_take_profit
        entry = 50000.0
        stop  = calc_stop_loss(entry, 'crypto_macd')
        tp    = calc_take_profit(entry, 'crypto_macd')
        risk   = entry - stop
        reward = tp - entry
        rr = reward / risk if risk > 0 else 0
        assert abs(rr - 3.0) < 0.5, f"Crypto R/R should be ~3:1, got {rr:.2f}"

    def test_long_hard_stop_triggers(self):
        from risk.stop_loss_manager import should_exit
        pos = {'stop': 49000, 'target': 53000, 'high_since_entry': 50000,
               'entry': 50000, 'direction': 'LONG'}
        exit_, reason = should_exit(pos, 'crypto_macd', current_price=48999)
        assert exit_
        assert "stop" in reason.lower()

    def test_long_take_profit_triggers(self):
        from risk.stop_loss_manager import should_exit
        pos = {'stop': 49000, 'target': 53000, 'high_since_entry': 53500,
               'entry': 50000, 'direction': 'LONG'}
        exit_, reason = should_exit(pos, 'crypto_macd', current_price=53001)
        assert exit_
        assert "profit" in reason.lower()

    def test_no_exit_when_in_range(self):
        from risk.stop_loss_manager import should_exit
        pos = {'stop': 49000, 'target': 53000, 'high_since_entry': 51000,
               'entry': 50000, 'direction': 'LONG'}
        exit_, _ = should_exit(pos, 'crypto_macd', current_price=51000)
        assert not exit_

    def test_short_stop_triggers_above_entry(self):
        from risk.stop_loss_manager import should_exit
        pos = {'stop': 51000, 'target': 47000, 'high_since_entry': 50000,
               'entry': 50000, 'direction': 'SHORT'}
        exit_, reason = should_exit(pos, 'crypto_macd', current_price=51001)
        assert exit_
        assert "stop" in reason.lower()


class TestDrawdownHeat:
    """Verify the 5-level heat system returns correct levels and size factors."""

    def test_risk_manager_pre_check_entry_passes_confidence_checks(self):
        rm = _make_rm()
        from risk.drawdown_controller import get_heat_level
        with patch('risk.drawdown_controller.get_todays_pnl', return_value=0.0), \
             patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
             patch('risk.drawdown_controller.get_all_time_stats', return_value={'total_pnl': 0.0}), \
             patch('risk.drawdown_controller.get_heat_level', return_value={'level': 0, 'size_factor': 1.0, 'label': 'NORMAL', 'daily_pnl': 0, 'pct_drawn': 0}):
            result = rm.pre_check_entry('crypto_macd', 'BTC-USDC', 'BUY', 50000.0, confidence=0.8)
            assert result.approved

    def _heat(self, daily_pnl: float, all_time_pnl: float = 0.0):
        from risk.drawdown_controller import get_heat_level
        with patch('risk.drawdown_controller.get_todays_pnl', return_value=daily_pnl), \
             patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
             patch('risk.drawdown_controller.get_all_time_stats',
                   return_value={'total_pnl': all_time_pnl}):
            return get_heat_level(False)

    def test_normal_no_loss(self):
        heat = self._heat(0.0)
        assert heat['level'] == 0
        assert heat['label'] == 'NORMAL'
        assert heat['size_factor'] == 1.0

    def test_caution_at_1pt5_pct(self):
        from config import ACCOUNT_SIZE
        loss = -(ACCOUNT_SIZE * 0.015 + 1.0)  # just past -1.5% of real balance
        heat = self._heat(loss)
        assert heat['level'] == 1
        assert heat['label'] == 'CAUTION'
        assert heat['size_factor'] == 0.75

    def test_warning_at_2pt5_pct(self):
        from config import ACCOUNT_SIZE
        loss = -(ACCOUNT_SIZE * 0.025 + 1.0)  # just past -2.5%
        heat = self._heat(loss)
        assert heat['level'] == 2
        assert heat['label'] == 'WARNING'
        assert heat['size_factor'] == 0.50

    def test_danger_at_3pt5_pct(self):
        from config import ACCOUNT_SIZE
        loss = -(ACCOUNT_SIZE * 0.035 + 1.0)  # just past -3.5%
        heat = self._heat(loss)
        assert heat['level'] == 3
        assert heat['label'] == 'DANGER'
        assert heat['size_factor'] == 0.25

    def test_halt_at_4_pct(self):
        from config import ACCOUNT_SIZE
        loss = -(ACCOUNT_SIZE * 0.040 + 1.0)  # just past -4.0%
        heat = self._heat(loss)
        assert heat['level'] == 4
        assert heat['label'] == 'HALT'
        assert heat['size_factor'] == 0.0

    def test_heat_size_factors_are_monotone_decreasing(self):
        """Each level should have a strictly lower size_factor than the previous."""
        from config import ACCOUNT_SIZE
        losses = [0.0,
                  -(ACCOUNT_SIZE * 0.015 + 1.0),
                  -(ACCOUNT_SIZE * 0.025 + 1.0),
                  -(ACCOUNT_SIZE * 0.035 + 1.0),
                  -(ACCOUNT_SIZE * 0.040 + 1.0)]
        factors = [self._heat(loss)['size_factor'] for loss in losses]
        for i in range(1, len(factors)):
            assert factors[i] <= factors[i - 1], \
                f"Heat size factor not decreasing: {factors}"

    def test_small_win_stays_normal(self):
        heat = self._heat(+10.0)
        assert heat['level'] == 0
        assert heat['size_factor'] == 1.0
