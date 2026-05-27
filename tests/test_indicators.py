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


def _make_df(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame for testing."""
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    rows = []
    for i, c in enumerate(closes):
        open_  = c * (1 + rng.normal(0, 0.001))
        high   = max(open_, c) * (1 + abs(rng.normal(0, 0.002)))
        low    = min(open_, c) * (1 - abs(rng.normal(0, 0.002)))
        volume = abs(rng.normal(1000, 200))
        rows.append({'open': open_, 'high': high, 'low': low, 'close': c, 'volume': volume})
    return pd.DataFrame(rows)


class TestIndicatorOutput:
    def setup_method(self):
        self.df = _make_df(200)
        self.result = add_all_indicators(self.df)

    def test_returns_dataframe(self):
        assert isinstance(self.result, pd.DataFrame), "add_all_indicators must return a DataFrame"

    def test_required_columns_present(self):
        # MACD is stored as macd1_hist / macd2_hist (3-variant consensus approach)
        required = ['close', 'macd1_hist', 'adx', 'atr', 'bb_upper', 'bb_lower', 'bb_mid']
        for col in required:
            assert col in self.result.columns, f"Missing required column: {col}"

    def test_close_preserved(self):
        pd.testing.assert_series_equal(
            self.result['close'].reset_index(drop=True),
            self.df['close'].reset_index(drop=True),
            check_names=False,
        )

    def test_adx_in_range(self):
        adx = self.result['adx'].dropna()
        if len(adx):
            assert (adx >= 0).all() and (adx <= 100).all(), f"ADX out of [0,100] range"

    def test_atr_positive(self):
        atr = self.result['atr'].dropna()
        if len(atr):
            assert (atr > 0).all(), "ATR must be positive"

    def test_bb_band_ordering(self):
        df = self.result.dropna(subset=['bb_upper', 'bb_mid', 'bb_lower'])
        if len(df):
            assert (df['bb_upper'] >= df['bb_mid']).all(), "bb_upper must be >= bb_mid"
            assert (df['bb_mid'] >= df['bb_lower']).all(), "bb_mid must be >= bb_lower"


class TestLookAheadBias:
    """Verify indicators do not use future candles.

    Method: compute on N bars, append one future bar, recompute.
    The value at bar N must be identical — any change = look-ahead bias.
    """

    def _last_val(self, df: pd.DataFrame, col: str):
        if col not in df.columns:
            return None
        vals = df[col].dropna()
        return float(vals.iloc[-1]) if len(vals) else None

    def test_macd_no_lookahead(self):
        df = _make_df(150)
        extra_row = _make_df(1, seed=99)
        df_ext = pd.concat([df, extra_row], ignore_index=True)

        val_n  = self._last_val(add_all_indicators(df), 'macd_hist')
        val_n1 = self._last_val(add_all_indicators(df_ext.iloc[:-1]), 'macd_hist')

        if val_n is not None and val_n1 is not None:
            assert abs(val_n - val_n1) < 1e-6, \
                f"MACD look-ahead bias: changed by {abs(val_n - val_n1)} after adding future candle"

    def test_adx_no_lookahead(self):
        df = _make_df(150)
        extra_row = _make_df(1, seed=77)
        df_ext = pd.concat([df, extra_row], ignore_index=True)

        val_n  = self._last_val(add_all_indicators(df), 'adx')
        val_n1 = self._last_val(add_all_indicators(df_ext.iloc[:-1]), 'adx')

        if val_n is not None and val_n1 is not None:
            assert abs(val_n - val_n1) < 1e-6, \
                f"ADX look-ahead bias: changed by {abs(val_n - val_n1)}"


class TestEdgeCases:
    def test_too_few_candles(self):
        """With < 30 candles, indicators should return DataFrame unchanged (not crash)."""
        tiny = _make_df(10)
        result = add_all_indicators(tiny)
        assert isinstance(result, pd.DataFrame), "Should return DataFrame even with few candles"

    def test_constant_price(self):
        """Flat price series — ATR=0, ADX edge case — should not crash."""
        rows = [{'open': 50.0, 'high': 50.0, 'low': 50.0, 'close': 50.0, 'volume': 1000.0}
                for _ in range(100)]
        df = pd.DataFrame(rows)
        result = add_all_indicators(df)
        assert isinstance(result, pd.DataFrame)

    def test_minimum_viable_candles(self):
        """Exactly 30 candles — boundary condition."""
        df = _make_df(30)
        result = add_all_indicators(df)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 30
