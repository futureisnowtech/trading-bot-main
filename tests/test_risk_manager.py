"""
tests/test_risk_manager.py — Verify hard risk rules can never be bypassed.

Run: python3 -m pytest tests/test_risk_manager.py -v

These tests use a fresh RiskManager instance (no DB required for most tests)
and mock the DB-reading functions to control test conditions.
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_rm():
    """Create a fresh RiskManager with empty positions and no halt."""
    with patch('risk.risk_manager.load_open_positions', return_value=[]), \
         patch('risk.risk_manager.get_todays_pnl', return_value=0.0), \
         patch('risk.risk_manager.get_all_time_stats', return_value={'total_pnl': 0.0}):
        # Patch halt restore to avoid DB dependency
        with patch('risk.risk_manager.RiskManager._restore_halt_state'):
            from risk.risk_manager import RiskManager
            return RiskManager()


class TestHaltRule:
    def test_halted_system_blocks_all_entries(self):
        rm = _make_rm()
        rm._halted = True
        rm._halt_reason = "Test halt"
        with patch('risk.drawdown_controller.get_todays_pnl', return_value=0.0), \
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
        """If daily P&L exceeds limit, check_entry should block AND set halt."""
        rm = _make_rm()
        # Simulate -$25 loss on a $500 account (>4% = $20 limit)
        with patch('risk.drawdown_controller.get_todays_pnl', return_value=-25.0), \
             patch('risk.drawdown_controller.get_all_time_stats', return_value={'total_pnl': 0.0}), \
             patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
             patch('risk.risk_manager.log_event'), \
             patch('risk.risk_manager.get_todays_pnl', return_value=-25.0), \
             patch('risk.risk_manager.get_todays_fees', return_value=0.0):
            result = rm.pre_check_entry('crypto_macd', 'BTC-USDC', 'BUY', 50000.0, confidence=0.8)
        assert not result.approved
        assert "loss" in result.reason.lower()


class TestPositionLimits:
    def test_max_crypto_positions_enforced(self):
        rm = _make_rm()
        # Fill up to max (5 positions)
        for i in range(5):
            rm._crypto[f'SYM{i}-USDC'] = {'qty': 1, 'entry': 100, 'stop': 98, 'target': 106,
                                            'high_since_entry': 100, 'ts_entry': '2026-01-01',
                                            'direction': 'LONG', 'entry_reason': ''}
        with patch('risk.drawdown_controller.get_todays_pnl', return_value=0.0), \
             patch('risk.drawdown_controller.get_all_time_stats', return_value={'total_pnl': 0.0}), \
             patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
             patch('risk.risk_limits.get_daily_trade_count', return_value=0), \
             patch('data.market_data.is_market_open', return_value=True), \
             patch('data.market_data.is_in_no_trade_window', return_value=False):
            result = rm.pre_check_entry('crypto_macd', 'NEW-USDC', 'BUY', 50000.0)
        assert not result.approved
        assert "max crypto" in result.reason.lower()

    def test_no_double_entry_same_symbol(self):
        rm = _make_rm()
        rm._crypto['BTC-USDC'] = {'qty': 0.005, 'entry': 50000, 'stop': 49250, 'target': 52500,
                                   'high_since_entry': 50000, 'ts_entry': '2026-01-01',
                                   'direction': 'LONG', 'entry_reason': ''}
        with patch('risk.drawdown_controller.get_todays_pnl', return_value=0.0), \
             patch('risk.drawdown_controller.get_all_time_stats', return_value={'total_pnl': 0.0}), \
             patch('risk.drawdown_controller.get_todays_fees', return_value=0.0), \
             patch('data.market_data.is_market_open', return_value=True), \
             patch('data.market_data.is_in_no_trade_window', return_value=False):
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
        assert pct_away <= CRYPTO_STOP_LOSS_PCT * 1.5 + 1e-6, \
            f"Stop loss too wide: {pct_away:.4%} vs max {CRYPTO_STOP_LOSS_PCT * 1.5:.4%}"

    def test_take_profit_maintains_rr_ratio(self):
        from risk.stop_loss_manager import calc_stop_loss, calc_take_profit
        entry = 50000.0
        stop  = calc_stop_loss(entry, 'crypto_macd')
        tp    = calc_take_profit(entry, 'crypto_macd')
        risk   = entry - stop
        reward = tp - entry
        rr = reward / risk if risk > 0 else 0
        assert abs(rr - 2.0) < 0.01, f"Crypto R/R should be 2:1, got {rr:.2f}"

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
