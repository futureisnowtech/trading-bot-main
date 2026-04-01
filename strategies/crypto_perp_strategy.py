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
    PERP_MAX_LEVERAGE, PAPER_TRADING,
)

# ── Tuneable constants ─────────────────────────────────────────────────────────
_BREAKOUT_BARS    = 10 if PAPER_TRADING else 20   # paper: 10-bar high/low fires more often
# Paper mode: loosen thresholds to maximise learning trades
_ADX_MIN          = 12.0 if PAPER_TRADING else 20.0   # paper: catch weaker trends
_RSI_LONG_MIN     = 48.0 if PAPER_TRADING else 55.0   # paper: near-neutral RSI ok
_RSI_SHORT_MAX    = 52.0 if PAPER_TRADING else 45.0   # paper: near-neutral RSI ok
_VOL_SPIKE_MIN    = 0.8  if PAPER_TRADING else 1.2    # paper: no vol confirmation needed
_BREAKOUT_BUFFER  = 0.0005 if PAPER_TRADING else 0.001  # paper: 0.05% buffer (was 0.1%)
_FUNDING_SHORT_MIN= 0.0001    # 0.01% per 8h → longs paying → bearish bias
_FUNDING_LONG_MAX = 0.0003    # Above 0.03% → too expensive to go long (cost paid 3×/day)
_FUNDING_EXTREME  = 0.0005    # 0.05% → extreme, strong short confirmation

# Momentum path thresholds (paper only — second entry mode alongside breakout)
_MACD_MOMENTUM_ADX_MIN  = 12.0   # ADX floor for momentum entries
_MACD_MOMENTUM_RSI_LONG = (50, 72)   # RSI band for momentum longs
_MACD_MOMENTUM_RSI_SHORT= (28, 50)   # RSI band for momentum shorts


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
    long_breakout = price > bar_high * (1 + _BREAKOUT_BUFFER)
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
    short_breakout = price < bar_low * (1 - _BREAKOUT_BUFFER)
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

    # ── Momentum continuation path (paper mode — feeds learning system) ──────
    # Fires when MACD histogram is turning in our favor + RSI in trend band + ADX shows trend.
    # Does NOT require a strict price breakout — catches mid-trend momentum entries.
    if PAPER_TRADING and adx >= _MACD_MOMENTUM_ADX_MIN:
        macd_hist = float(last.get('macd_hist', 0) or 0)
        prev_hist = float(df.iloc[-2].get('macd_hist', 0) or 0) if len(df) >= 2 else 0.0

        # LONG momentum: MACD hist positive and accelerating, RSI in bullish band
        if (macd_hist > 0 and macd_hist > prev_hist
                and _MACD_MOMENTUM_RSI_LONG[0] <= rsi <= _MACD_MOMENTUM_RSI_LONG[1]
                and funding_rate <= _FUNDING_LONG_MAX):
            stop   = price * (1 - PERP_STOP_PCT)
            target = price * (1 + PERP_TAKE_PROFIT_PCT)
            rr = (target - price) / (price - stop)
            confidence = 0.52
            if oi_rising:              confidence += 0.04
            if rsi > 60:               confidence += 0.03
            if funding_rate < 0:       confidence += 0.04
            confidence = round(min(confidence, 0.75), 3)
            reason = (
                f"LONG momentum | MACD hist {macd_hist:.6f}↑ RSI={rsi:.1f} "
                f"ADX={adx:.1f} funding={funding_rate*100:.4f}%/8h R:R={rr:.2f}x"
            )
            return Signal(
                action='BUY', symbol=symbol, strategy='crypto_perp',
                confidence=confidence, reason=reason, price=price,
                suggested_size_usd=PERP_POSITION_SIZE_USD,
                stop_loss=stop, take_profit=target,
                metadata={'direction': 'LONG', 'leverage': PERP_MAX_LEVERAGE,
                          'funding_rate': funding_rate, 'oi_rising': oi_rising,
                          'adx': adx, 'rsi': rsi, 'entry_type': 'momentum'},
            )

        # SHORT momentum: MACD hist negative and accelerating down, RSI in bearish band
        if (macd_hist < 0 and macd_hist < prev_hist
                and _MACD_MOMENTUM_RSI_SHORT[0] <= rsi <= _MACD_MOMENTUM_RSI_SHORT[1]
                and funding_rate >= _FUNDING_SHORT_MIN):
            stop   = price * (1 + PERP_STOP_PCT)
            target = price * (1 - PERP_TAKE_PROFIT_PCT)
            rr = (price - target) / (stop - price)
            confidence = 0.52
            if funding_rate >= _FUNDING_EXTREME:  confidence += 0.07
            elif funding_rate >= 0.0003:          confidence += 0.04
            if oi_rising:                          confidence += 0.04
            if rsi < 38:                           confidence += 0.03
            confidence = round(min(confidence, 0.75), 3)
            reason = (
                f"SHORT momentum | MACD hist {macd_hist:.6f}↓ RSI={rsi:.1f} "
                f"ADX={adx:.1f} funding={funding_rate*100:.4f}%/8h longs paying={funding_rate>0} R:R={rr:.2f}x"
            )
            return Signal(
                action='SELL', symbol=symbol, strategy='crypto_perp',
                confidence=confidence, reason=reason, price=price,
                suggested_size_usd=PERP_POSITION_SIZE_USD,
                stop_loss=stop, take_profit=target,
                metadata={'direction': 'SHORT', 'leverage': PERP_MAX_LEVERAGE,
                          'funding_rate': funding_rate, 'oi_rising': oi_rising,
                          'adx': adx, 'rsi': rsi, 'entry_type': 'momentum'},
            )

    # ── No signal ─────────────────────────────────────────────────────────────
    long_why = (
        f"price {price:.4f} vs {_BREAKOUT_BARS}-bar high {bar_high:.4f} "
        f"RSI={rsi:.1f}(need≥{_RSI_LONG_MIN}) "
        f"funding={funding_rate*100:.4f}%/8h"
    )
    short_why = (
        f"price {price:.4f} vs {_BREAKOUT_BARS}-bar low {bar_low:.4f} "
        f"RSI={rsi:.1f}(need≤{_RSI_SHORT_MAX})"
    )
    return _hold(symbol, price,
                 f"No signal | LONG: {long_why} | SHORT: {short_why}")


def _hold(symbol: str, price: float, reason: str) -> Signal:
    return Signal(
        action='HOLD',
        symbol=symbol,
        strategy='crypto_perp',
        confidence=0.0,
        reason=reason,
        price=price,
    )
