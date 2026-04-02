"""
tests/test_perp_momentum.py

Regression tests for the perp momentum continuation path.
Catches: momentum path never firing, breakout path blocked when it should fire,
         confidence cap respected, direction metadata correct.
"""
import sys
import os
import pytest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_df(
    n: int = 40,
    price: float = 100.0,
    rsi: float = 60.0,
    adx: float = 20.0,
    vol_spike: float = 1.0,
    macd_hist: float = 0.001,
    prev_macd_hist: float = 0.0005,
) -> pd.DataFrame:
    """
    Build a minimal OHLCV + indicator DataFrame that satisfies add_all_indicators
    column expectations without actually computing all indicators.
    We inject pre-cooked indicator values into the last two rows.
    """
    close = [price] * n
    data = {
        'open':   close,
        'high':   [p * 1.002 for p in close],
        'low':    [p * 0.998 for p in close],
        'close':  close,
        'volume': [1_000_000.0] * n,
    }
    df = pd.DataFrame(data)

    # Inject indicator columns directly so we skip the heavy compute
    df['rsi']       = rsi
    df['adx']       = adx
    df['vol_spike'] = vol_spike
    df['macd_hist'] = macd_hist
    df.at[df.index[-2], 'macd_hist'] = prev_macd_hist

    # Columns that add_all_indicators may reference (safe defaults)
    for col in ['macd', 'macd_signal', 'ema_20', 'ema_50', 'bb_upper', 'bb_lower',
                'bb_mid', 'kc_upper', 'kc_lower', 'supertrend', 'squeeze_fired',
                'wae_bullish', 'wae_exploding', 'fisher', 'wt1', 'wt2', 'lrsi',
                'chop', 'kalman', 'avwap', 'rv_ratio', 'oi_delta',
                'cumulative_delta', 'iv_skew', 'whale_flow']:
        if col not in df.columns:
            df[col] = 0.0
    if 'supertrend_bullish' not in df.columns:
        df['supertrend_bullish'] = True

    return df


class TestPerpBreakoutPath:
    def test_long_breakout_fires(self):
        """LONG breakout signal fires when price exceeds N-bar high."""
        from strategies.crypto_perp_strategy import get_perp_signal, _BREAKOUT_BARS

        n = _BREAKOUT_BARS + 10
        # All prior bars at 100; last bar spikes above
        close = [100.0] * (n - 1) + [101.5]
        high  = [c * 1.001 for c in close]
        low   = [c * 0.999 for c in close]
        df = pd.DataFrame({
            'open': close, 'high': high, 'low': low,
            'close': close, 'volume': [1_000_000.0] * n,
        })
        # Inject indicators: RSI high enough, ADX trending
        for col in ['rsi', 'adx', 'vol_spike', 'macd_hist', 'macd', 'macd_signal',
                    'ema_20', 'ema_50', 'bb_upper', 'bb_lower', 'bb_mid',
                    'kc_upper', 'kc_lower', 'squeeze_fired', 'wae_bullish',
                    'wae_exploding', 'fisher', 'wt1', 'wt2', 'lrsi', 'chop',
                    'kalman', 'avwap', 'rv_ratio', 'supertrend', 'cumulative_delta',
                    'iv_skew', 'whale_flow', 'oi_delta']:
            df[col] = 0.0
        df['rsi']       = 58.0
        df['adx']       = 22.0
        df['vol_spike'] = 0.9   # paper mode: no vol gate
        df['supertrend_bullish'] = True

        sig = get_perp_signal('BTCUSDT', df, funding_rate=0.0001)
        assert sig.action == 'BUY', f"Expected BUY breakout, got {sig.action}: {sig.reason}"
        assert sig.metadata.get('direction') == 'LONG'
        assert sig.stop_loss < sig.price < sig.take_profit

    def test_short_breakout_fires(self):
        """SHORT breakdown fires when price falls below N-bar low."""
        from strategies.crypto_perp_strategy import get_perp_signal, _BREAKOUT_BARS

        n = _BREAKOUT_BARS + 10
        close = [100.0] * (n - 1) + [98.4]
        high  = [c * 1.001 for c in close]
        low   = [c * 0.999 for c in close]
        df = pd.DataFrame({
            'open': close, 'high': high, 'low': low,
            'close': close, 'volume': [1_000_000.0] * n,
        })
        for col in ['rsi', 'adx', 'vol_spike', 'macd_hist', 'macd', 'macd_signal',
                    'ema_20', 'ema_50', 'bb_upper', 'bb_lower', 'bb_mid',
                    'kc_upper', 'kc_lower', 'squeeze_fired', 'wae_bullish',
                    'wae_exploding', 'fisher', 'wt1', 'wt2', 'lrsi', 'chop',
                    'kalman', 'avwap', 'rv_ratio', 'supertrend', 'cumulative_delta',
                    'iv_skew', 'whale_flow', 'oi_delta']:
            df[col] = 0.0
        df['rsi']       = 42.0
        df['adx']       = 22.0
        df['vol_spike'] = 0.9
        df['supertrend_bullish'] = False

        sig = get_perp_signal('BTCUSDT', df, funding_rate=0.0002)
        assert sig.action == 'SELL', f"Expected SELL breakdown, got {sig.action}: {sig.reason}"
        assert sig.metadata.get('direction') == 'SHORT'
        assert sig.stop_loss > sig.price > sig.take_profit


class TestPerpMomentumPath:
    def test_long_momentum_fires_in_paper(self):
        """
        Momentum LONG fires when MACD hist > 0, accelerating, RSI in [50,72],
        ADX >= 12, funding ok — even without a new price high.
        """
        from strategies.crypto_perp_strategy import get_perp_signal, _BREAKOUT_BARS
        from config import PAPER_TRADING

        if not PAPER_TRADING:
            pytest.skip("Momentum path only active in paper mode")

        n = _BREAKOUT_BARS + 10
        # Flat price — no breakout
        price = 100.0
        close = [price] * n
        high  = [p * 1.0005 for p in close]  # tiny range: no N-bar breakout
        low   = [p * 0.9995 for p in close]
        df = pd.DataFrame({
            'open': close, 'high': high, 'low': low,
            'close': close, 'volume': [1_000_000.0] * n,
        })
        for col in ['rsi', 'adx', 'vol_spike', 'macd_hist', 'macd', 'macd_signal',
                    'ema_20', 'ema_50', 'bb_upper', 'bb_lower', 'bb_mid',
                    'kc_upper', 'kc_lower', 'squeeze_fired', 'wae_bullish',
                    'wae_exploding', 'fisher', 'wt1', 'wt2', 'lrsi', 'chop',
                    'kalman', 'avwap', 'rv_ratio', 'supertrend', 'cumulative_delta',
                    'iv_skew', 'whale_flow', 'oi_delta']:
            df[col] = 0.0
        # Momentum conditions: MACD hist positive and accelerating
        df['macd_hist']       = 0.002
        df.at[df.index[-2], 'macd_hist'] = 0.001   # prev < current → accelerating
        df['rsi']             = 58.0   # in [50, 72]
        df['adx']             = 15.0   # >= 12
        df['vol_spike']       = 0.85   # above paper threshold (0.8)
        df['supertrend_bullish'] = True

        sig = get_perp_signal('ETHUSDT', df, funding_rate=0.0001)
        assert sig.action == 'BUY', (
            f"Momentum LONG did not fire. action={sig.action}: {sig.reason}"
        )
        assert sig.metadata.get('entry_type') == 'momentum', (
            f"entry_type should be 'momentum', got {sig.metadata.get('entry_type')}"
        )
        assert sig.confidence <= 0.75, (
            f"Momentum confidence cap violated: {sig.confidence} > 0.75"
        )

    def test_short_momentum_fires_in_paper(self):
        """SHORT momentum: MACD hist negative and diving, RSI in [28,50], funding >= min."""
        from strategies.crypto_perp_strategy import get_perp_signal, _BREAKOUT_BARS
        from config import PAPER_TRADING

        if not PAPER_TRADING:
            pytest.skip("Momentum path only active in paper mode")

        n = _BREAKOUT_BARS + 10
        price = 100.0
        close = [price] * n
        high  = [p * 1.0005 for p in close]
        low   = [p * 0.9995 for p in close]
        df = pd.DataFrame({
            'open': close, 'high': high, 'low': low,
            'close': close, 'volume': [1_000_000.0] * n,
        })
        for col in ['rsi', 'adx', 'vol_spike', 'macd_hist', 'macd', 'macd_signal',
                    'ema_20', 'ema_50', 'bb_upper', 'bb_lower', 'bb_mid',
                    'kc_upper', 'kc_lower', 'squeeze_fired', 'wae_bullish',
                    'wae_exploding', 'fisher', 'wt1', 'wt2', 'lrsi', 'chop',
                    'kalman', 'avwap', 'rv_ratio', 'supertrend', 'cumulative_delta',
                    'iv_skew', 'whale_flow', 'oi_delta']:
            df[col] = 0.0
        df['macd_hist']       = -0.002
        df.at[df.index[-2], 'macd_hist'] = -0.001   # prev > current → diving
        df['rsi']             = 42.0   # in [28, 50]
        df['adx']             = 15.0
        df['vol_spike']       = 0.85   # above paper threshold (0.8)
        df['supertrend_bullish'] = False

        sig = get_perp_signal('SOLUSDT', df, funding_rate=0.00015)
        assert sig.action == 'SELL', (
            f"Momentum SHORT did not fire. action={sig.action}: {sig.reason}"
        )
        assert sig.metadata.get('entry_type') == 'momentum'
        assert sig.confidence <= 0.75

    def test_momentum_long_blocked_when_rsi_too_high(self):
        """Momentum LONG must NOT fire if RSI > 72 (overbought — not the right entry band)."""
        from strategies.crypto_perp_strategy import get_perp_signal, _BREAKOUT_BARS
        from config import PAPER_TRADING

        if not PAPER_TRADING:
            pytest.skip("Momentum path only active in paper mode")

        n = _BREAKOUT_BARS + 10
        price = 100.0
        close = [price] * n
        high  = [p * 1.0005 for p in close]
        low   = [p * 0.9995 for p in close]
        df = pd.DataFrame({
            'open': close, 'high': high, 'low': low,
            'close': close, 'volume': [1_000_000.0] * n,
        })
        for col in ['rsi', 'adx', 'vol_spike', 'macd_hist', 'macd', 'macd_signal',
                    'ema_20', 'ema_50', 'bb_upper', 'bb_lower', 'bb_mid',
                    'kc_upper', 'kc_lower', 'squeeze_fired', 'wae_bullish',
                    'wae_exploding', 'fisher', 'wt1', 'wt2', 'lrsi', 'chop',
                    'kalman', 'avwap', 'rv_ratio', 'supertrend', 'cumulative_delta',
                    'iv_skew', 'whale_flow', 'oi_delta']:
            df[col] = 0.0
        df['macd_hist']       = 0.002
        df.at[df.index[-2], 'macd_hist'] = 0.001
        df['rsi']             = 80.0   # OVERBOUGHT — outside momentum band [50,72]
        df['adx']             = 15.0
        df['vol_spike']       = 0.85
        df['supertrend_bullish'] = True

        sig = get_perp_signal('BTCUSDT', df, funding_rate=0.0001)
        # Should HOLD — RSI 80 is outside [50, 72] so no momentum entry, and no breakout
        assert sig.action == 'HOLD', (
            f"Momentum LONG fired with RSI={80} (should be blocked above 72): {sig.reason}"
        )

    def test_momentum_not_active_when_adx_too_low(self):
        """Momentum path must not fire when ADX < 12 (no trend — random noise)."""
        from strategies.crypto_perp_strategy import get_perp_signal, _BREAKOUT_BARS
        from config import PAPER_TRADING

        if not PAPER_TRADING:
            pytest.skip("Momentum path only active in paper mode")

        n = _BREAKOUT_BARS + 10
        price = 100.0
        close = [price] * n
        high  = [p * 1.0005 for p in close]
        low   = [p * 0.9995 for p in close]
        df = pd.DataFrame({
            'open': close, 'high': high, 'low': low,
            'close': close, 'volume': [1_000_000.0] * n,
        })
        for col in ['rsi', 'adx', 'vol_spike', 'macd_hist', 'macd', 'macd_signal',
                    'ema_20', 'ema_50', 'bb_upper', 'bb_lower', 'bb_mid',
                    'kc_upper', 'kc_lower', 'squeeze_fired', 'wae_bullish',
                    'wae_exploding', 'fisher', 'wt1', 'wt2', 'lrsi', 'chop',
                    'kalman', 'avwap', 'rv_ratio', 'supertrend', 'cumulative_delta',
                    'iv_skew', 'whale_flow', 'oi_delta']:
            df[col] = 0.0
        df['macd_hist']       = 0.002
        df.at[df.index[-2], 'macd_hist'] = 0.001
        df['rsi']             = 58.0
        df['adx']             = 8.0   # ADX < 12 — no trend, ADX gate fires first
        df['vol_spike']       = 0.85
        df['supertrend_bullish'] = True

        sig = get_perp_signal('BTCUSDT', df, funding_rate=0.0001)
        assert sig.action == 'HOLD', (
            f"Signal fired with ADX={8} — ADX gate should have blocked it: {sig.reason}"
        )

    def test_confidence_cap_at_75pct(self):
        """Momentum path confidence must never exceed 0.75."""
        from strategies.crypto_perp_strategy import get_perp_signal, _BREAKOUT_BARS
        from config import PAPER_TRADING

        if not PAPER_TRADING:
            pytest.skip("Momentum path only active in paper mode")

        n = _BREAKOUT_BARS + 10
        price = 100.0
        close = [price] * n
        high  = [p * 1.0005 for p in close]
        low   = [p * 0.9995 for p in close]
        df = pd.DataFrame({
            'open': close, 'high': high, 'low': low,
            'close': close, 'volume': [1_000_000.0] * n,
        })
        for col in ['rsi', 'adx', 'vol_spike', 'macd_hist', 'macd', 'macd_signal',
                    'ema_20', 'ema_50', 'bb_upper', 'bb_lower', 'bb_mid',
                    'kc_upper', 'kc_lower', 'squeeze_fired', 'wae_bullish',
                    'wae_exploding', 'fisher', 'wt1', 'wt2', 'lrsi', 'chop',
                    'kalman', 'avwap', 'rv_ratio', 'supertrend', 'cumulative_delta',
                    'iv_skew', 'whale_flow', 'oi_delta']:
            df[col] = 0.0
        # Max all boosters: OI rising, RSI > 60, negative funding
        df['macd_hist']       = 0.005
        df.at[df.index[-2], 'macd_hist'] = 0.001
        df['rsi']             = 65.0
        df['adx']             = 25.0
        df['vol_spike']       = 0.85
        df['supertrend_bullish'] = True

        sig = get_perp_signal('ETHUSDT', df,
                              funding_rate=-0.0002,   # negative = tailwind
                              open_interest=1.1e9,
                              open_interest_prev=1.0e9)
        if sig.action == 'BUY' and sig.metadata.get('entry_type') == 'momentum':
            assert sig.confidence <= 0.75, (
                f"Confidence cap violated on momentum path: {sig.confidence}"
            )


class TestPerpStopTarget:
    def test_stop_and_target_set_correctly_long(self):
        """LONG: stop < entry, target > entry, target further than stop."""
        from strategies.crypto_perp_strategy import get_perp_signal, _BREAKOUT_BARS
        from config import PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT

        n = _BREAKOUT_BARS + 10
        price = 100.0
        close = [price] * (n - 1) + [price * 1.02]
        high  = [c * 1.001 for c in close]
        low   = [c * 0.999 for c in close]
        df = pd.DataFrame({
            'open': close, 'high': high, 'low': low,
            'close': close, 'volume': [1_000_000.0] * n,
        })
        for col in ['rsi', 'adx', 'vol_spike', 'macd_hist', 'macd', 'macd_signal',
                    'ema_20', 'ema_50', 'bb_upper', 'bb_lower', 'bb_mid',
                    'kc_upper', 'kc_lower', 'squeeze_fired', 'wae_bullish',
                    'wae_exploding', 'fisher', 'wt1', 'wt2', 'lrsi', 'chop',
                    'kalman', 'avwap', 'rv_ratio', 'supertrend', 'cumulative_delta',
                    'iv_skew', 'whale_flow', 'oi_delta']:
            df[col] = 0.0
        df['rsi']       = 58.0
        df['adx']       = 22.0
        df['vol_spike'] = 0.9
        df['supertrend_bullish'] = True

        sig = get_perp_signal('BTCUSDT', df, funding_rate=0.0001)
        if sig.action == 'BUY':
            assert sig.stop_loss < sig.price, "LONG stop must be below entry"
            assert sig.take_profit > sig.price, "LONG target must be above entry"
            rr = (sig.take_profit - sig.price) / (sig.price - sig.stop_loss)
            assert rr >= 1.8, f"R:R={rr:.2f} — perp needs at least 1.8:1 to clear fees"
