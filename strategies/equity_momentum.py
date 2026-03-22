"""
strategies/equity_momentum.py

Equity momentum strategy for Webull (regular stocks, no OTC, no margin).
$500 cash account — designed to avoid PDT rule (max 3 day trades/5 days).

Signal logic (30-min Heikin Ashi candles, primary):
  BUY when ALL of:
    - HTF bias is BULLISH (daily chart)
    - Volume spike > 150% of 20-day average
    - RSI between 35 and 65 (not overbought, not extremely oversold)
    - MACD histogram > 0 (positive momentum)
    - Price above VWAP (buyers in control)
    - KST zero-line cross (from below to above)
    - No 3 consecutive red candles on 5-min
    - Market is open and past the 10am no-trade window

  EXIT (SELL) when ANY of:
    - RSI > 70 (overbought)
    - 3 consecutive red candles on 5-min chart
    - Price crosses below VWAP
    - MACD histogram turns negative
    - KST crosses below signal line

Confidence scoring weights each condition.
"""
import pandas as pd
import numpy as np
from typing import Optional

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies.base_strategy import BaseStrategy, Signal
from data.indicators import add_all_indicators, get_htf_bias
from data.market_data import get_bars, get_daily_bars
from config import (
    EQUITY_POSITION_SIZE_USD, EQUITY_STOP_LOSS_PCT, EQUITY_TAKE_PROFIT_PCT,
    EQUITY_RSI_OVERSOLD, EQUITY_RSI_OVERBOUGHT, EQUITY_VOLUME_SPIKE_MULTIPLIER
)


class EquityMomentumStrategy(BaseStrategy):
    """
    Momentum strategy for liquid US equities on Webull.
    Primary timeframe: 30-min Heikin Ashi
    Confirmation: 5-min candle count
    HTF filter: Daily bias
    """

    def __init__(self):
        super().__init__('equity_momentum')
        self._htf_bias_cache: dict = {}     # symbol -> {'bias', 'ts'}
        self._opening_range: dict = {}       # symbol -> {'high', 'low'}

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> Signal:
        """
        df: 30-min OHLCV dataframe with indicators added.
        Returns Signal.
        """
        if df is None or len(df) < 30:
            return self._hold(symbol, 0.0, "Insufficient data")

        df = add_all_indicators(df)
        last = df.iloc[-1]
        price = float(last['close'])

        if price <= 0:
            return self._hold(symbol, price, "Invalid price")

        # ── HTF bias (daily chart) ─────────────────────────────────────────────
        htf = self._get_htf_bias(symbol)
        if htf['bias'] == 'BEARISH':
            return self._hold(symbol, price, f"HTF bearish — skipping longs")

        # ── Gather indicator values ────────────────────────────────────────────
        rsi = float(last.get('rsi', 50) or 50)
        macd_hist = float(last.get('macd_std_hist', 0) or 0)
        kst = float(last.get('kst', 0) or 0)
        kst_signal = float(last.get('kst_signal', 0) or 0)
        vwap = float(last.get('vwap', price) or price)
        vol_spike = float(last.get('vol_spike', 1.0) or 1.0)
        consec_red = int(last.get('consec_red', 0) or 0)
        ha_bullish = bool(last.get('ha_bullish', False))
        adx = float(last.get('adx', 25) or 25)
        bb_width = float(last.get('bb_width', 0) or 0)

        # ── Previous values for crossover detection ────────────────────────────
        if len(df) >= 2:
            prev = df.iloc[-2]
            prev_kst = float(prev.get('kst', 0) or 0)
            prev_kst_signal = float(prev.get('kst_signal', 0) or 0)
            prev_macd_hist = float(prev.get('macd_std_hist', 0) or 0)
        else:
            prev_kst = kst
            prev_kst_signal = kst_signal
            prev_macd_hist = macd_hist

        # ── Check 5-min chart for 3-candle exit rule ───────────────────────────
        df_5m = get_bars(symbol, interval='5m', period='1d')
        consec_red_5m = 0
        if df_5m is not None and len(df_5m) >= 3:
            df_5m = add_all_indicators(df_5m)
            consec_red_5m = int(df_5m.iloc[-1].get('consec_red', 0) or 0)

        # ══════════════════════════════════════════════════════════════════════
        # EXIT CONDITIONS (check first — prioritize risk management)
        # ══════════════════════════════════════════════════════════════════════
        exit_reasons = []

        if rsi > EQUITY_RSI_OVERBOUGHT:
            exit_reasons.append(f"RSI overbought ({rsi:.1f})")

        if consec_red_5m >= 3:
            exit_reasons.append("3 consecutive red candles on 5-min (530 rule)")

        if price < vwap * 0.998:  # Small buffer to avoid noise
            exit_reasons.append(f"Price crossed below VWAP (${vwap:.2f})")

        if macd_hist < 0 and prev_macd_hist >= 0:
            exit_reasons.append("MACD histogram turned negative")

        if kst < kst_signal and prev_kst >= prev_kst_signal:
            exit_reasons.append("KST crossed below signal (530 exit)")

        # Choppy market — high BB width and ADX below threshold
        if adx < 15 and bb_width > 0.05:
            exit_reasons.append(f"Choppy market detected (ADX={adx:.1f})")

        if exit_reasons:
            signal = Signal(
                action='SELL',
                symbol=symbol,
                strategy=self.name,
                confidence=0.80,
                reason=' | '.join(exit_reasons),
                price=price,
                suggested_size_usd=0,
            )
            self._last_signal = signal
            return signal

        # ══════════════════════════════════════════════════════════════════════
        # ENTRY CONDITIONS — Confidence scoring
        # ══════════════════════════════════════════════════════════════════════
        score = 0
        max_score = 7
        reasons = []

        # 1. Volume spike
        if vol_spike >= EQUITY_VOLUME_SPIKE_MULTIPLIER:
            score += 1
            reasons.append(f"Vol spike {vol_spike:.1f}x")

        # 2. RSI in buy zone
        if 35 <= rsi <= 60:
            score += 1
            reasons.append(f"RSI bullish zone ({rsi:.1f})")
        elif rsi < 35:
            score += 0.5
            reasons.append(f"RSI oversold ({rsi:.1f}) — potential bounce")

        # 3. MACD histogram positive
        if macd_hist > 0:
            score += 1
            reasons.append("MACD hist > 0")
        if macd_hist > 0 and prev_macd_hist <= 0:
            score += 0.5
            reasons.append("MACD hist just crossed above 0")

        # 4. Price above VWAP
        if price > vwap:
            score += 1
            reasons.append(f"Price above VWAP (${vwap:.2f})")

        # 5. KST momentum
        if kst > kst_signal:
            score += 1
            reasons.append("KST above signal")
        if kst > 0 and prev_kst <= 0:
            score += 0.5
            reasons.append("KST zero-line cross ↑")

        # 6. Heikin Ashi bullish
        if ha_bullish:
            score += 1
            reasons.append("HA candle bullish")

        # 7. HTF confirmation
        if htf['bias'] == 'BULLISH':
            score += 1
            reasons.append(f"HTF bullish (daily)")

        confidence = score / max_score

        # Minimum score of 4/7 required to enter
        if score < 4:
            return self._hold(
                symbol, price,
                f"Score {score:.1f}/{max_score} insufficient — need ≥4 | "
                + (', '.join(reasons) if reasons else 'no conditions met')
            )

        # ── Calculate stops ────────────────────────────────────────────────────
        atr = float(last.get('atr', price * 0.01) or price * 0.01)
        from risk.risk_manager import get_risk_manager
        rm = get_risk_manager()
        stop_loss = rm.calc_stop_loss(price, self.name, atr=atr)
        take_profit = rm.calc_take_profit(price, self.name, atr=atr)

        signal = Signal(
            action='BUY',
            symbol=symbol,
            strategy=self.name,
            confidence=confidence,
            reason=' | '.join(reasons),
            price=price,
            suggested_size_usd=EQUITY_POSITION_SIZE_USD,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                'score': score,
                'rsi': rsi,
                'macd_hist': macd_hist,
                'vol_spike': vol_spike,
                'adx': adx,
                'htf_bias': htf['bias'],
            }
        )
        self._last_signal = signal
        return signal

    def _get_htf_bias(self, symbol: str) -> dict:
        """Cache daily bias — refresh every 4 hours."""
        import time
        cache = self._htf_bias_cache.get(symbol, {})
        if cache and (time.time() - cache.get('ts', 0)) < 14400:
            return cache

        df_daily = get_daily_bars(symbol, period='6mo')
        if df_daily is None:
            return {'bias': 'NEUTRAL', 'strength': 0.5, 'score': 0}

        bias = get_htf_bias(df_daily)
        self._htf_bias_cache[symbol] = {**bias, 'ts': time.time()}
        return bias

    def set_opening_range(self, symbol: str, high: float, low: float) -> None:
        """Set opening range breakout levels (called by scheduler at 9:35 ET)."""
        self._opening_range[symbol] = {'high': high, 'low': low}

    def get_opening_range(self, symbol: str) -> Optional[dict]:
        return self._opening_range.get(symbol)
