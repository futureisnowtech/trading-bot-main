"""
strategies/futures_scalper.py

ES/MES futures intraday scalping strategy.
Uses the AI debate engine for trade decisions.
NO PDT RULE — futures accounts can trade unlimited times per day.

Pre-market (8:30 ET):
  - Pull 4H, 8H, Daily data → directional bias
  - Mark key HTF support/resistance levels

Market open strategy:
  - Hard block: No trades 9:30–10:00 ET (opening chaos)
  - Mark high/low of the opening 5-min candle (opening range)
  - Wait for strong breakout above/below opening range
  - Wait for pullback to VWAP or 50% of candle
  - Enter on 1–2 min chart confirmation
  - Max 4 trades/day, stop when daily goal hit

With $500 account:
  - Use MES ONLY (1 contract max)
  - Daily goal: $30 (6 points on MES)
  - Max daily loss: $25 (5 points on MES)
"""
import pandas as pd
import numpy as np
from typing import Optional
from datetime import datetime
import pytz

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.base_strategy import BaseStrategy, Signal
from data.indicators import add_all_indicators, get_htf_bias
from data.market_data import get_bars, is_market_open, is_in_no_trade_window
from config import MARKET_TIMEZONE


class FuturesScalperStrategy(BaseStrategy):
    """
    Intraday scalper for MES (Micro E-mini S&P 500).
    AI debate engine required for all entries.
    """

    # Daily limits
    DAILY_GOAL_PTS = 6.0    # Stop trading after +6 points profit ($30 on MES)
    DAILY_MAX_LOSS_PTS = 5.0  # Hard stop after -5 points ($25 on MES)
    MAX_TRADES_DAY = 4

    # Stop/target in points
    STOP_LOSS_PTS = 4.0     # $20 risk per trade
    TAKE_PROFIT_PTS = 8.0   # $40 target (2:1 R:R)

    # MES proxy ticker for yfinance data (use ES=F as free data source)
    DATA_TICKER = 'ES=F'

    def __init__(self):
        super().__init__('futures_scalper')
        self._opening_range: dict = {}   # {'high': float, 'low': float, 'set': bool}
        self._daily_pnl_pts = 0.0
        self._trades_today = 0
        self._htf_bias: dict = {}
        self._goal_hit = False

    def generate_signal(self, symbol: str = 'MES', df: Optional[pd.DataFrame] = None) -> Signal:
        """
        Analyze MES/ES setup and return signal.
        Called by scheduler every 60s during market hours.
        """
        if self._goal_hit:
            return self._hold(symbol, 0.0, f"Daily goal hit (+{self.DAILY_GOAL_PTS} pts) — done for the day")

        if self._daily_pnl_pts < -self.DAILY_MAX_LOSS_PTS:
            return self._hold(symbol, 0.0, f"Daily loss limit hit ({self._daily_pnl_pts:.1f} pts) — done for the day")

        if self._trades_today >= self.MAX_TRADES_DAY:
            return self._hold(symbol, 0.0, f"Max {self.MAX_TRADES_DAY} trades reached today")

        if not is_market_open():
            return self._hold(symbol, 0.0, "Market closed")

        if is_in_no_trade_window():
            return self._hold(symbol, 0.0, "No-trade window 9:30–10:00 ET — watching opening range")

        # Get 5-min data for signal
        if df is None:
            df = get_bars(self.DATA_TICKER, interval='5m', period='2d')

        if df is None or len(df) < 20:
            return self._hold(symbol, 0.0, "Insufficient data")

        df = add_all_indicators(df)
        last = df.iloc[-1]
        price = float(last['close'])

        # ── Opening range breakout check ───────────────────────────────────────
        if not self._opening_range.get('set'):
            return self._hold(symbol, price, "Opening range not set yet")

        or_high = self._opening_range.get('high', price)
        or_low = self._opening_range.get('low', price)
        or_range = or_high - or_low

        # Check for breakout
        if price > or_high * 1.001:
            direction = 'LONG'
            breakout_strength = (price - or_high) / or_range if or_range > 0 else 0
        elif price < or_low * 0.999:
            direction = 'SHORT'
            breakout_strength = (or_low - price) / or_range if or_range > 0 else 0
        else:
            return self._hold(symbol, price, f"Price ${price:.2f} inside opening range [{or_low:.2f}-{or_high:.2f}]")

        # HTF bias must align with breakout direction
        bias = self._htf_bias.get('bias', 'NEUTRAL')
        if direction == 'LONG' and bias == 'BEARISH':
            return self._hold(symbol, price, "Long breakout vs bearish HTF — no trade")
        if direction == 'SHORT' and bias == 'BULLISH':
            return self._hold(symbol, price, "Short breakout vs bullish HTF — no trade")

        # Additional filters: volume confirmation, not choppy
        adx = float(last.get('adx', 25) or 25)
        vol_spike = float(last.get('vol_spike', 1.0) or 1.0)
        vwap = float(last.get('vwap', price) or price)

        if adx < 18:
            return self._hold(symbol, price, f"Choppy market (ADX={adx:.1f}) — no futures trade")

        if direction == 'LONG' and price < vwap:
            return self._hold(symbol, price, "Long breakout but price below VWAP — waiting for pullback confirmation")

        # Signal looks valid — build it
        stop = price - self.STOP_LOSS_PTS if direction == 'LONG' else price + self.STOP_LOSS_PTS
        target = price + self.TAKE_PROFIT_PTS if direction == 'LONG' else price - self.TAKE_PROFIT_PTS

        confidence = min(
            0.50 + (breakout_strength * 0.2) + (min(vol_spike, 3) * 0.05) + (adx / 200),
            0.90
        )

        reason = (
            f"{direction} breakout of OR at ${price:.2f} | "
            f"OR=[{or_low:.2f}-{or_high:.2f}] | ADX={adx:.1f} | "
            f"Vol={vol_spike:.1f}x | HTF={bias}"
        )

        signal = Signal(
            action='BUY',
            symbol=symbol,
            strategy=self.name,
            confidence=confidence,
            reason=reason,
            price=price,
            suggested_size_usd=0,  # Futures sizing is in contracts, not USD
            stop_loss=stop,
            take_profit=target,
            metadata={
                'direction': direction,
                'or_high': or_high,
                'or_low': or_low,
                'adx': adx,
                'htf_bias': bias,
            }
        )
        self._last_signal = signal
        return signal

    def set_opening_range(self, high: float, low: float) -> None:
        """Called by scheduler at 9:35 ET with the first 5-min candle's H/L."""
        self._opening_range = {'high': high, 'low': low, 'set': True}
        print(f"[futures_scalper] Opening range set: [{low:.2f} — {high:.2f}]")

    def update_htf_bias(self) -> None:
        """Call at 8:30 ET pre-market to refresh HTF directional bias."""
        df_daily = get_bars(self.DATA_TICKER, interval='1d', period='6mo')
        if df_daily is not None:
            self._htf_bias = get_htf_bias(df_daily)
            print(f"[futures_scalper] HTF bias: {self._htf_bias['bias']} (strength={self._htf_bias['strength']:.2f})")

    def record_trade_result(self, pnl_pts: float) -> None:
        """Update daily P&L tracker after each trade closes."""
        self._daily_pnl_pts += pnl_pts
        self._trades_today += 1
        if self._daily_pnl_pts >= self.DAILY_GOAL_PTS:
            self._goal_hit = True
            print(f"[futures_scalper] 🎯 Daily goal reached ({self._daily_pnl_pts:.1f} pts) — standing down")

    def reset_daily(self) -> None:
        """Called at market open each day."""
        self._daily_pnl_pts = 0.0
        self._trades_today = 0
        self._goal_hit = False
        self._opening_range = {}
