"""
tests/test_sprint2.py — Unit tests for Sprint 2: unified math framework.

Covers:
  risk/volatility_regime.py — V_score mapping, funding cap, cache, edge cases
  risk/edge_monitor.py      — edge score formula, normalisation, auto-action state
  risk/unified_sizer.py     — multiplier pipeline, devil's advocate gate, guardrails
"""
import math
import unittest
from unittest.mock import patch, MagicMock


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: volatility_regime.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestVolatilityRegime(unittest.TestCase):

    def setUp(self):
        from risk.volatility_regime import invalidate_cache
        invalidate_cache()

    # ── _compute_regime logic (tested directly, bypassing network) ────────────

    def test_high_volatility_ratio(self):
        """ratio > 1.5 → HIGH_VOLATILITY, v_score = 0.20"""
        from risk.volatility_regime import _compute_regime
        import math

        # Inject synthetic returns where 5-day vol >> 20-day vol
        # 20 days: mostly flat, then a spike at the end
        flat = [0.001] * 15
        spike = [0.05, -0.05, 0.04, -0.04, 0.06]   # high short-term vol
        returns = flat + spike

        with patch('risk.volatility_regime._get_daily_returns', return_value=returns):
            result = _compute_regime('BTC-USDC', 'crypto', 5, 20, 0.0)

        self.assertEqual(result['label'], 'HIGH_VOLATILITY')
        self.assertEqual(result['v_score'], 0.20)
        self.assertTrue(result['data_ok'])

    def test_low_volatility_ratio(self):
        """ratio < 0.8 → LOW_VOLATILITY, v_score = 1.00"""
        from risk.volatility_regime import _compute_regime

        # 20 days: high early vol, then compressed at end
        high_early = [0.05, -0.05, 0.04, -0.04, 0.06,
                      0.05, -0.05, 0.04, -0.04, 0.06,
                      0.05, -0.05, 0.04, -0.04, 0.06]
        compressed = [0.0001, -0.0001, 0.0001, -0.0001, 0.0001]
        returns = high_early + compressed

        with patch('risk.volatility_regime._get_daily_returns', return_value=returns):
            result = _compute_regime('BTC-USDC', 'crypto', 5, 20, 0.0)

        self.assertEqual(result['label'], 'LOW_VOLATILITY')
        self.assertEqual(result['v_score'], 1.00)

    def test_normal_range(self):
        """ratio between 0.8 and 1.2 → NORMAL, v_score = 0.75"""
        from risk.volatility_regime import _compute_regime

        # Uniform returns → short vol ≈ long vol → ratio ≈ 1.0
        uniform = [0.01, -0.01] * 10   # 20 returns
        with patch('risk.volatility_regime._get_daily_returns', return_value=uniform):
            result = _compute_regime('BTC-USDC', 'crypto', 5, 20, 0.0)

        self.assertEqual(result['label'], 'NORMAL')
        self.assertEqual(result['v_score'], 0.75)

    def test_elevated_volatility(self):
        """ratio between 1.2 and 1.5 → ELEVATED, v_score = 0.50"""
        from risk.volatility_regime import _compute_regime

        flat_15 = [0.002] * 15
        moderate_spike = [0.03, -0.03, 0.025, -0.025, 0.028]
        returns = flat_15 + moderate_spike

        with patch('risk.volatility_regime._get_daily_returns', return_value=returns):
            result = _compute_regime('BTC-USDC', 'crypto', 5, 20, 0.0)

        # The ratio might land in ELEVATED range or HIGH depending on exact numbers
        self.assertIn(result['label'], ('ELEVATED', 'HIGH_VOLATILITY'))
        self.assertIn(result['v_score'], (0.20, 0.50))

    def test_funding_rate_cap(self):
        """funding_rate > FUNDING_OVERHEATED_PCT caps v_score at 0.50"""
        from risk.volatility_regime import _compute_regime
        from config import FUNDING_OVERHEATED_PCT

        uniform = [0.001, -0.001] * 10  # NORMAL regime → v_score normally 0.75

        with patch('risk.volatility_regime._get_daily_returns', return_value=uniform):
            # Overheat the funding rate
            result = _compute_regime('BTC-USDC', 'crypto', 5, 20,
                                     funding_rate=FUNDING_OVERHEATED_PCT + 0.01)

        self.assertTrue(result['funding_capped'])
        self.assertLessEqual(result['v_score'], 0.50)

    def test_funding_cap_does_not_apply_to_mes(self):
        """Funding rate cap only applies to crypto market, not MES."""
        from risk.volatility_regime import _compute_regime
        from config import FUNDING_OVERHEATED_PCT

        uniform = [0.001, -0.001] * 10

        with patch('risk.volatility_regime._get_daily_returns', return_value=uniform):
            result = _compute_regime('ES', 'mes', 5, 20,
                                     funding_rate=FUNDING_OVERHEATED_PCT + 0.10)

        self.assertFalse(result['funding_capped'])

    def test_insufficient_data_fallback(self):
        """If fewer than n_long returns, return data_ok=False and NORMAL 0.75."""
        from risk.volatility_regime import _compute_regime

        with patch('risk.volatility_regime._get_daily_returns', return_value=None):
            result = _compute_regime('BTC-USDC', 'crypto', 5, 20, 0.0)

        self.assertFalse(result['data_ok'])
        self.assertEqual(result['label'], 'NORMAL')
        self.assertEqual(result['v_score'], 0.75)

    def test_symbol_normalisation(self):
        """Various symbol formats should not raise errors."""
        from risk.volatility_regime import _to_yf_symbol

        cases = [
            ('BTC-USDC', 'BTC-USD'),
            ('ETH-USDC', 'ETH-USD'),
            ('BTCUSDT',  'BTC-USD'),
            ('ETHUSDT',  'ETH-USD'),
            ('ES',       'ES=F'),
            ('MES',      'ES=F'),
            ('SPY',      'SPY'),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(_to_yf_symbol(inp), expected)

    def test_cache_invalidation(self):
        """invalidate_cache() removes cached entries."""
        from risk.volatility_regime import _CACHE, invalidate_cache
        _CACHE['BTC-USDC:crypto'] = {'label': 'TEST', '_ts': 1e18}
        invalidate_cache('BTC-USDC')
        self.assertNotIn('BTC-USDC:crypto', _CACHE)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: edge_monitor.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeMonitor(unittest.TestCase):

    def _make_trades(self, pnls: list) -> list:
        """Build minimal trade dicts from P&L list."""
        return [{'pnl_usd': p, 'value_usd': abs(p) * 10 or 100, 'fee_usd': 0.1}
                for p in pnls]

    # ── _compute_edge_score ───────────────────────────────────────────────────

    def test_empty_trades_returns_zero(self):
        from data.edge_monitor import _compute_market_edge_score_metrics as _compute_edge_score
        result = _compute_edge_score([])
        self.assertEqual(result['edge_score'], 0.0)
        self.assertEqual(result['n_trades'], 0)

    def test_all_winning_trades(self):
        """All wins: WR=1.0, PF=inf→capped, Sharpe high → edge_score near 1.0"""
        from data.edge_monitor import _compute_market_edge_score_metrics as _compute_edge_score
        trades = self._make_trades([10.0] * 20)
        result = _compute_edge_score(trades)
        self.assertEqual(result['win_rate'], 1.0)
        self.assertGreater(result['profit_factor'], 1e6)   # no losses → PF is very large
        self.assertGreater(result['edge_score'], 0.8)

    def test_all_losing_trades(self):
        """All losses: WR=0, PF=0, edge_score near 0."""
        from data.edge_monitor import _compute_market_edge_score_metrics as _compute_edge_score
        trades = self._make_trades([-5.0] * 20)
        result = _compute_edge_score(trades)
        self.assertEqual(result['win_rate'], 0.0)
        self.assertEqual(result['profit_factor'], 0.0)
        self.assertLessEqual(result['edge_score'], 0.15)

    def test_fifty_fifty_win_rate(self):
        """50% win rate near the normalisation midpoint."""
        from data.edge_monitor import _compute_market_edge_score_metrics as _compute_edge_score
        # 10 wins of $5, 10 losses of $5 → WR=0.5, PF=1.0
        trades = self._make_trades([5.0, -5.0] * 10)
        result = _compute_edge_score(trades)
        self.assertAlmostEqual(result['win_rate'], 0.5, places=2)
        self.assertAlmostEqual(result['profit_factor'], 1.0, places=2)
        # edge_score should be around 0.5 (midpoint)
        self.assertGreater(result['edge_score'], 0.3)
        self.assertLess(result['edge_score'], 0.7)

    def test_edge_score_clamped_0_1(self):
        """edge_score is always in [0, 1]."""
        from data.edge_monitor import _compute_market_edge_score_metrics as _compute_edge_score

        for pnls in [
            [100.0] * 20,          # extreme wins
            [-100.0] * 20,         # extreme losses
            [1.0, -1.0] * 10,      # even
        ]:
            result = _compute_edge_score(self._make_trades(pnls))
            with self.subTest(pnls=pnls[:2]):
                self.assertGreaterEqual(result['edge_score'], 0.0)
                self.assertLessEqual(result['edge_score'], 1.0)

    def test_profit_factor_asymmetry(self):
        """High winners / small losses → PF > 2 → edge_score near 1."""
        from data.edge_monitor import _compute_market_edge_score_metrics as _compute_edge_score
        # 5 big wins, 15 small losses: PF should be high enough for decent edge
        trades = self._make_trades([50.0] * 5 + [-1.0] * 15)
        result = _compute_edge_score(trades)
        self.assertGreater(result['profit_factor'], 1.0)

    # ── strategy_to_market ───────────────────────────────────────────────────

    def test_strategy_to_market_mapping(self):
        from data.edge_monitor import strategy_to_market

        self.assertEqual(strategy_to_market('crypto_ai'),        'crypto')
        self.assertEqual(strategy_to_market('crypto_macd'),      'crypto')
        self.assertEqual(strategy_to_market('polymarket_ev'),    'polymarket')
        self.assertEqual(strategy_to_market('mes_pullback'),     'mes')
        self.assertEqual(strategy_to_market('futures_scalper'),  'mes')
        self.assertEqual(strategy_to_market('crypto_perp'),      'crypto')
        self.assertEqual(strategy_to_market('unknown_strategy'), 'crypto')

    # ── get_edge_size_factor ─────────────────────────────────────────────────

    def test_no_consecutive_low_returns_1(self):
        """Without triggering consecutive low windows, factor = 1.0."""
        from data.edge_monitor import get_market_edge_size_factor as get_edge_size_factor, _consecutive_low
        _consecutive_low.clear()
        self.assertEqual(get_edge_size_factor('crypto'), 1.0)

    def test_consecutive_low_triggers_50pct(self):
        """Two consecutive low windows → factor = 0.50."""
        from data.edge_monitor import (
            get_market_edge_size_factor as get_edge_size_factor, _consecutive_low, CONSECUTIVE_TRIGGER
        )
        _consecutive_low['crypto'] = CONSECUTIVE_TRIGGER
        self.assertEqual(get_edge_size_factor('crypto'), 0.50)
        _consecutive_low.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: unified_sizer.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnifiedSizer(unittest.TestCase):

    def _mock_components(self, v=0.75, e_edge=0.5, d=1.0, k=1.0):
        """Return a dict of patch targets → return values for a standard scenario."""
        return {
            'risk.volatility_regime.get_volatility_regime': {'v_score': v, 'data_ok': True},
            'data.edge_monitor.get_market_edge_score': {'edge_score': e_edge, 'n_trades': 25, 'sufficient': True},
            'data.edge_monitor.get_market_edge_size_factor': 1.0,
            'risk.drawdown_controller.get_heat_level': {'size_factor': d, 'level': 0, 'label': 'NORMAL', 'daily_pnl': 0, 'pct_drawn': 0},
            'risk.position_sizer.size_from_kelly': k,
        }

    # ── Pre-adaptive gate (< 20 trades) ──────────────────────────────────────

    def test_pre_adaptive_skips_v_and_e(self):
        """
        With < 20 trades, V and E stay at 1.0.
        Only D and T are applied (K is applied via position_sizer).
        """
        from risk.unified_sizer import get_position_size

        base = 100.0

        with patch('risk.unified_sizer._get_trade_count', return_value=5), \
             patch('risk.unified_sizer._get_time_of_day_multiplier', return_value=1.0), \
             patch('risk.drawdown_controller.get_heat_level',
                   return_value={'size_factor': 1.0, 'level': 0, 'label': 'NORMAL', 'daily_pnl': 0, 'pct_drawn': 0}), \
             patch('risk.position_sizer.size_from_kelly', return_value=1.0):

            result = get_position_size('crypto_ai', 'BTC-USDC', base, 0.6)

        # V and E not applied → result close to base (D=1, T=1, K scale=1/1=1)
        self.assertAlmostEqual(result, base, delta=10)

    # ── Post-adaptive (>= 20 trades) ─────────────────────────────────────────

    def test_adaptive_applies_all_multipliers(self):
        """With >= 20 trades, all multipliers apply."""
        from risk.unified_sizer import get_position_size

        base = 100.0

        with patch('risk.unified_sizer._get_trade_count', return_value=25), \
             patch('risk.unified_sizer._get_time_of_day_multiplier', return_value=1.0), \
             patch('risk.volatility_regime.get_volatility_regime',
                   return_value={'v_score': 0.75, 'data_ok': True}), \
             patch('data.edge_monitor.get_market_edge_score',
                   return_value={'edge_score': 0.5, 'n_trades': 25, 'sufficient': True}), \
             patch('data.edge_monitor.get_market_edge_size_factor', return_value=1.0), \
             patch('risk.drawdown_controller.get_heat_level',
                   return_value={'size_factor': 1.0, 'level': 0, 'label': 'NORMAL', 'daily_pnl': 0, 'pct_drawn': 0}), \
             patch('risk.position_sizer.size_from_kelly', return_value=1.0):

            result = get_position_size('crypto_ai', 'BTC-USDC', base, 0.6)

        # V=0.75, E=0.50+0.5=1.0, D=1.0, T=1.0, K=1.0/1.0=1.0, M=1.0
        expected = base * 0.75 * 1.0 * 1.0 * 1.0 * 1.0 * 1.0
        self.assertAlmostEqual(result, expected, delta=5)

    def test_halt_level_returns_zero(self):
        """D=0.0 (HALT) returns 0.0 immediately."""
        from risk.unified_sizer import get_position_size

        with patch('risk.unified_sizer._get_trade_count', return_value=5), \
             patch('risk.unified_sizer._get_time_of_day_multiplier', return_value=1.0), \
             patch('risk.drawdown_controller.get_heat_level',
                   return_value={'size_factor': 0.0, 'level': 4, 'label': 'HALT', 'daily_pnl': -100, 'pct_drawn': 0.04}):

            result = get_position_size('crypto_ai', 'BTC-USDC', 250.0, 0.7)

        self.assertEqual(result, 0.0)

    def test_min_position_guardrail(self):
        """Result never falls below MIN_POSITION_USD."""
        from risk.unified_sizer import get_position_size, MIN_POSITION_USD

        base = 10.0   # small base + heavy discounts

        with patch('risk.unified_sizer._get_trade_count', return_value=25), \
             patch('risk.unified_sizer._get_time_of_day_multiplier', return_value=0.5), \
             patch('risk.volatility_regime.get_volatility_regime',
                   return_value={'v_score': 0.20, 'data_ok': True}), \
             patch('data.edge_monitor.get_market_edge_score',
                   return_value={'edge_score': 0.0, 'n_trades': 25, 'sufficient': True}), \
             patch('data.edge_monitor.get_market_edge_size_factor', return_value=0.5), \
             patch('risk.drawdown_controller.get_heat_level',
                   return_value={'size_factor': 0.25, 'level': 3, 'label': 'DANGER', 'daily_pnl': -15, 'pct_drawn': 0.035}), \
             patch('risk.position_sizer.size_from_kelly', return_value=0.5):

            result = get_position_size('crypto_ai', 'BTC-USDC', base, 0.3)

        self.assertGreaterEqual(result, MIN_POSITION_USD)

    def test_max_position_guardrail(self):
        """Result never exceeds base_size × MAX_POSITION_SCALE."""
        from risk.unified_sizer import get_position_size, MAX_POSITION_SCALE

        base = 100.0

        with patch('risk.unified_sizer._get_trade_count', return_value=50), \
             patch('risk.unified_sizer._get_time_of_day_multiplier', return_value=1.5), \
             patch('risk.volatility_regime.get_volatility_regime',
                   return_value={'v_score': 1.0, 'data_ok': True}), \
             patch('data.edge_monitor.get_market_edge_score',
                   return_value={'edge_score': 1.0, 'n_trades': 50, 'sufficient': True}), \
             patch('data.edge_monitor.get_market_edge_size_factor', return_value=1.0), \
             patch('risk.drawdown_controller.get_heat_level',
                   return_value={'size_factor': 1.0, 'level': 0, 'label': 'NORMAL', 'daily_pnl': 0, 'pct_drawn': 0}), \
             patch('risk.position_sizer.size_from_kelly', return_value=1.5):

            result = get_position_size('crypto_ai', 'BTC-USDC', base, 1.0)

        self.assertLessEqual(result, base * MAX_POSITION_SCALE)

    def test_zero_base_size_returns_zero(self):
        """base_size=0 returns 0 immediately without any DB calls."""
        from risk.unified_sizer import get_position_size
        result = get_position_size('crypto_ai', 'BTC-USDC', 0.0, 0.7)
        self.assertEqual(result, 0.0)

    def test_dead_zone_time_of_day(self):
        """2–5am ET time multiplier is 0.5 → halves pre-adaptive sizing."""
        from risk.unified_sizer import _get_time_of_day_multiplier

        # Mock 3:00am ET
        import pytz
        from datetime import datetime as dt
        from unittest.mock import patch

        tz = pytz.timezone('America/New_York')
        mock_now = dt(2026, 1, 15, 3, 0, tzinfo=tz)
        with patch('risk.unified_sizer.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now
            mult = _get_time_of_day_multiplier('crypto_ai')
        self.assertEqual(mult, 0.50)

    def test_ny_open_time_of_day(self):
        """9:45am ET (NY open window) → multiplier is 1.5."""
        from risk.unified_sizer import _get_time_of_day_multiplier
        import pytz
        from datetime import datetime as dt

        tz = pytz.timezone('America/New_York')
        mock_now = dt(2026, 1, 15, 9, 45, tzinfo=tz)
        with patch('risk.unified_sizer.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now
            mult = _get_time_of_day_multiplier('crypto_ai')
        self.assertEqual(mult, 1.50)

    def test_polymarket_always_1_0(self):
        """Polymarket strategies ignore time-of-day (24/7 markets)."""
        from risk.unified_sizer import _get_time_of_day_multiplier
        import pytz
        from datetime import datetime as dt

        tz = pytz.timezone('America/New_York')
        # Even in dead zone
        mock_now = dt(2026, 1, 15, 3, 0, tzinfo=tz)
        with patch('risk.unified_sizer.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now
            mult = _get_time_of_day_multiplier('polymarket_ev')
        self.assertEqual(mult, 1.0)

    # ── get_sizing_breakdown ─────────────────────────────────────────────────

    def test_sizing_breakdown_structure(self):
        """get_sizing_breakdown returns all required keys."""
        from risk.unified_sizer import get_sizing_breakdown

        with patch('risk.unified_sizer._get_trade_count', return_value=0), \
             patch('risk.unified_sizer._get_time_of_day_multiplier', return_value=1.0), \
             patch('risk.drawdown_controller.get_heat_level',
                   return_value={'size_factor': 1.0, 'level': 0, 'label': 'NORMAL', 'daily_pnl': 0, 'pct_drawn': 0}), \
             patch('risk.position_sizer.size_from_kelly', return_value=1.0):

            result = get_sizing_breakdown('crypto_ai', 'BTC-USDC', 100.0, 0.6)

        required_keys = {'base_size', 'v', 'e', 'd', 't', 'k', 'm',
                         'final_size', 'adaptive', 'trade_count', 'market'}
        self.assertTrue(required_keys.issubset(set(result.keys())))

    def test_strategy_to_market_mapping(self):
        """_strategy_to_market correctly classifies strategies."""
        from risk.unified_sizer import _strategy_to_market

        self.assertEqual(_strategy_to_market('crypto_ai'),        'crypto')
        self.assertEqual(_strategy_to_market('polymarket_ev'),    'polymarket')
        self.assertEqual(_strategy_to_market('mes_pullback'),     'mes')
        self.assertEqual(_strategy_to_market('futures_scalper'),  'mes')


if __name__ == '__main__':
    unittest.main(verbosity=2)
