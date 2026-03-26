"""
tests/test_indicators.py — Indicator math correctness + look-ahead bias checks.

Run: python3 -m pytest tests/test_indicators.py -v
"""
import sys
import os
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.indicators import add_all_indicators


def _make_candles(n: int = 200, seed: int = 42) -> list:
    """Generate synthetic OHLCV candles for testing."""
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    candles = []
    for i, c in enumerate(closes):
        open_  = c * (1 + rng.normal(0, 0.001))
        high   = max(open_, c) * (1 + abs(rng.normal(0, 0.002)))
        low    = min(open_, c) * (1 - abs(rng.normal(0, 0.002)))
        volume = abs(rng.normal(1000, 200))
        ts     = i * 60  # 1-minute candles
        candles.append([ts, open_, high, low, c, volume])
    return candles


class TestIndicatorOutput:
    def setup_method(self):
        self.candles = _make_candles(200)
        self.result  = add_all_indicators(self.candles)

    def test_returns_dict(self):
        assert isinstance(self.result, dict), "add_all_indicators must return a dict"

    def test_required_fields_present(self):
        required = [
            'close', 'macd_hist', 'adx', 'atr',
            'bb_upper', 'bb_lower', 'bb_mid',
        ]
        for field in required:
            assert field in self.result, f"Missing required field: {field}"

    def test_close_matches_last_candle(self):
        last_close = self.candles[-1][4]
        assert abs(self.result['close'] - last_close) < 0.0001, \
            "result['close'] must match last candle close price"

    def test_adx_in_range(self):
        adx = self.result.get('adx')
        if adx is not None and not np.isnan(adx):
            assert 0 <= adx <= 100, f"ADX out of range: {adx}"

    def test_atr_positive(self):
        atr = self.result.get('atr')
        if atr is not None and not np.isnan(atr):
            assert atr > 0, f"ATR must be positive, got {atr}"

    def test_bb_band_ordering(self):
        upper = self.result.get('bb_upper')
        mid   = self.result.get('bb_mid')
        lower = self.result.get('bb_lower')
        if all(v is not None and not np.isnan(v) for v in [upper, mid, lower]):
            assert upper >= mid >= lower, \
                f"Bollinger Band ordering violated: upper={upper} mid={mid} lower={lower}"


class TestLookAheadBias:
    """Verify indicators do not use future candles.

    Method: compute indicator on N candles, then append one more candle
    and recompute. The value for candle N must not change — if it does,
    the indicator is peeking at future data.
    """

    def test_macd_no_lookahead(self):
        candles = _make_candles(150)
        result_n   = add_all_indicators(candles)
        result_n1  = add_all_indicators(candles + [_make_candles(1, seed=99)[0]])
        # MACD hist at bar N should not change when bar N+1 is added
        if 'macd_hist' in result_n and 'macd_hist' in result_n1:
            # Allow small floating point differences
            diff = abs((result_n['macd_hist'] or 0) - (result_n1['macd_hist'] or 0))
            assert diff < 1e-6, \
                f"MACD look-ahead bias detected: changed by {diff} after adding future candle"

    def test_adx_no_lookahead(self):
        candles  = _make_candles(150)
        result_n = add_all_indicators(candles)
        result_n1 = add_all_indicators(candles + [_make_candles(1, seed=77)[0]])
        if 'adx' in result_n and 'adx' in result_n1:
            diff = abs((result_n['adx'] or 0) - (result_n1['adx'] or 0))
            assert diff < 1e-6, \
                f"ADX look-ahead bias detected: changed by {diff} after adding future candle"


class TestEdgeCases:
    def test_too_few_candles(self):
        """With very few candles most indicators should return None/NaN gracefully, not crash."""
        tiny = _make_candles(10)
        result = add_all_indicators(tiny)
        assert isinstance(result, dict), "Should return dict even with few candles"

    def test_constant_price(self):
        """Flat price series — indicators should not crash (ATR=0, ADX edge case)."""
        n = 100
        candles = [[i * 60, 50.0, 50.0, 50.0, 50.0, 1000.0] for i in range(n)]
        result = add_all_indicators(candles)
        assert isinstance(result, dict)
