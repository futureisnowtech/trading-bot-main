"""
strategies/crypto_perp_strategy.py

Bybit perpetual futures strategy — trades both LONG and SHORT.

Entry logic:
  LONG:  Price breaks above 20-bar high + RSI > 55 + ADX > 20
         + funding rate <= 0.03% (not overloaded with longs)
         + OI rising or neutral (real momentum, not liquidation)

  SHORT: Price breaks below 20-bar low + RSI < 45 + ADX > 20
         + funding rate >= 0.01% per 8h (longs paying = bearish tax)
         + OI rising or neutral

  Both need vol_spike >= 1.2x to confirm the breakout is real.

Fee math (20x leverage, $100 notional = $2000 exposure):
  Taker round-trip: 0.11% × $2000 = $2.20
  1.5% stop on notional = $30 loss → need >$2.20 to be viable ✓
  3.0% TP on notional = $60 gain → R:R 2:1 ✓

Why funding rate matters:
  Positive funding (> 0.01%/8h): longs pay shorts every 8h → shorts get paid
  to hold, AND the excess long positioning creates mean-reversion pressure.
  Negative funding: shorts pay longs → longs get tail wind.

  Extreme funding (> 0.05%/8h): signals over-leveraged market → fade the crowd.
  We treat >0.05% as a SHORT confirmation boost, not a block.
"""
import pandas as pd
import numpy as np
from typing import Optional, Tuple
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.base_strategy import Signal
from data.indicators import add_all_indicators
from config import (
    PERP_POSITION_SIZE_USD, PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT,
    PERP_MAX_LEVERAGE,
)

# ── Tuneable constants ─────────────────────────────────────────────────────────
_BREAKOUT_BARS    = 20        # Look-back bars for high/low breakout detection
_ADX_MIN          = 20.0      # Minimum trend strength required
_RSI_LONG_MIN     = 55.0      # RSI must confirm bullish momentum for longs
_RSI_SHORT_MAX    = 45.0      # RSI must confirm bearish momentum for shorts
_VOL_SPIKE_MIN    = 1.2       # Volume confirmation (1.2× 20-bar avg)
_FUNDING_SHORT_MIN= 0.0001    # 0.01% per 8h → longs paying → bearish bias
_FUNDING_LONG_MAX = 0.0003    # Above 0.03% → too expensive to go long (cost paid 3×/day)
_FUNDING_EXTREME  = 0.0005    # 0.05% → extreme, strong short confirmation


def get_perp_signal(
    symbol: str,
    df: pd.DataFrame,
    funding_rate: float = 0.0,
    open_interest: float = 0.0,
    open_interest_prev: float = 0.0,
) -> Signal:
    """
    Evaluate a perp entry for the given symbol.

    Parameters
    ----------
    symbol           : Bybit format, e.g. 'AVAXUSDT'
    df               : OHLCV DataFrame (1m or 5m candles, at least 30 bars)
    funding_rate     : Current 8h funding rate (decimal, e.g. 0.0001 = 0.01%)
    open_interest    : Current OI in USD
    open_interest_prev: Previous OI snapshot for change detection

    Returns
    -------
    Signal with action='BUY' (go long) | 'SELL' (go short) | 'HOLD'
    metadata['direction'] = 'LONG' | 'SHORT'
    """
    if df is None or len(df) < _BREAKOUT_BARS + 5:
        return _hold(symbol, 0.0, "Insufficient data")

    df = add_all_indicators(df)
    last = df.iloc[-1]
    price = float(last.get('close', 0) or 0)

    if price <= 0:
        return _hold(symbol, price, "Invalid price")

    # ── Extract indicators ────────────────────────────────────────────────────
    rsi = float(last.get('rsi', 50) or 50)
    adx = float(last.get('adx', 15) or 15)
    vol_spike = float(last.get('vol_spike', 1.0) or 1.0)

    # 20-bar high and low for breakout detection
    lookback = df.iloc[-_BREAKOUT_BARS - 1:-1]   # exclude current bar
    bar_high = float(lookback['high'].max()) if 'high' in lookback else price
    bar_low  = float(lookback['low'].min())  if 'low'  in lookback else price

    # OI change direction
    oi_rising = open_interest >= open_interest_prev * 0.98  # within 2% = neutral/rising

    # ── ADX: trend strength gate (both directions) ────────────────────────────
    if adx < _ADX_MIN:
        return _hold(symbol, price,
                     f"ADX {adx:.1f} < {_ADX_MIN} — no trend, perp pass")

    # ── Volume: breakout must have volume ────────────────────────────────────
    if vol_spike < _VOL_SPIKE_MIN:
        return _hold(symbol, price,
                     f"Vol {vol_spike:.2f}x < {_VOL_SPIKE_MIN}x — weak breakout, skip")

    # ── LONG signal ───────────────────────────────────────────────────────────
    long_breakout = price > bar_high * 1.001   # 0.1% buffer above 20-bar high
    if long_breakout and rsi >= _RSI_LONG_MIN and funding_rate <= _FUNDING_LONG_MAX:
        # Funding gate: if rate is too high, longs are too crowded — skip
        stop   = price * (1 - PERP_STOP_PCT)
        target = price * (1 + PERP_TAKE_PROFIT_PCT)
        rr = (target - price) / (price - stop)

        # Confidence: base 0.55, boost for funding tailwind + OI confirmation
        confidence = 0.55
        if funding_rate < 0:              confidence += 0.05  # longs get paid
        if oi_rising:                      confidence += 0.05
        if rsi > 65:                       confidence += 0.05
        if funding_rate <= -_FUNDING_SHORT_MIN: confidence += 0.05  # strong tailwind
        confidence = round(min(confidence, 0.85), 3)

        reason = (
            f"LONG breakout above {bar_high:.4f} | price={price:.4f} "
            f"RSI={rsi:.1f} ADX={adx:.1f} vol={vol_spike:.1f}x "
            f"funding={funding_rate*100:.4f}%/8h R:R={rr:.2f}x"
        )
        return Signal(
            action='BUY',
            symbol=symbol,
            strategy='crypto_perp',
            confidence=confidence,
            reason=reason,
            price=price,
            suggested_size_usd=PERP_POSITION_SIZE_USD,
            stop_loss=stop,
            take_profit=target,
            metadata={
                'direction': 'LONG',
                'leverage': PERP_MAX_LEVERAGE,
                'funding_rate': funding_rate,
                'oi_rising': oi_rising,
                'adx': adx, 'rsi': rsi,
            },
        )

    # ── SHORT signal ──────────────────────────────────────────────────────────
    short_breakout = price < bar_low * 0.999   # 0.1% buffer below 20-bar low
    if short_breakout and rsi <= _RSI_SHORT_MAX and funding_rate >= _FUNDING_SHORT_MIN:
        stop   = price * (1 + PERP_STOP_PCT)
        target = price * (1 - PERP_TAKE_PROFIT_PCT)
        rr = (price - target) / (stop - price)

        confidence = 0.55
        if funding_rate >= _FUNDING_EXTREME:  confidence += 0.08  # extreme longs
        elif funding_rate >= 0.0003:          confidence += 0.05
        if oi_rising:                          confidence += 0.05
        if rsi < 35:                           confidence += 0.05
        confidence = round(min(confidence, 0.85), 3)

        reason = (
            f"SHORT breakdown below {bar_low:.4f} | price={price:.4f} "
            f"RSI={rsi:.1f} ADX={adx:.1f} vol={vol_spike:.1f}x "
            f"funding={funding_rate*100:.4f}%/8h longs paying={funding_rate>0} R:R={rr:.2f}x"
        )
        return Signal(
            action='SELL',
            symbol=symbol,
            strategy='crypto_perp',
            confidence=confidence,
            reason=reason,
            price=price,
            suggested_size_usd=PERP_POSITION_SIZE_USD,
            stop_loss=stop,
            take_profit=target,
            metadata={
                'direction': 'SHORT',
                'leverage': PERP_MAX_LEVERAGE,
                'funding_rate': funding_rate,
                'oi_rising': oi_rising,
                'adx': adx, 'rsi': rsi,
            },
        )

    # ── No signal ─────────────────────────────────────────────────────────────
    long_why = (
        f"price {price:.4f} vs 20-bar high {bar_high:.4f} "
        f"RSI={rsi:.1f}(need≥{_RSI_LONG_MIN}) "
        f"funding={funding_rate*100:.4f}%/8h"
    )
    short_why = (
        f"price {price:.4f} vs 20-bar low {bar_low:.4f} "
        f"RSI={rsi:.1f}(need≤{_RSI_SHORT_MAX})"
    )
    return _hold(symbol, price,
                 f"No breakout | LONG: {long_why} | SHORT: {short_why}")


def _hold(symbol: str, price: float, reason: str) -> Signal:
    return Signal(
        action='HOLD',
        symbol=symbol,
        strategy='crypto_perp',
        confidence=0.0,
        reason=reason,
        price=price,
    )
