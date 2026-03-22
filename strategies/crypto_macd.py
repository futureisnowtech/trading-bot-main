"""
strategies/crypto_macd.py

Crypto MACD strategy for Coinbase Advanced Trade API.
Runs 24/7 on 5-minute candles.

Three MACD variants (backtested by Moon Dev with Z-score 70.81):

  Bot 1 — Workhorse MACD(3/15/3) histogram > 0
    - Trades every signal, long bias when hist > 0
    - High frequency: ~288 signals/day on BTC
    - Consistent edge across all sessions

  Bot 2 — Classic MACD(4/16/3) line vs signal crossover
    - MACD line above signal = long, below = short
    - Slightly lower frequency than Bot 1
    - Robust across all quarters

  Bot 3 — Sniper MACD(6/20/5) histogram > threshold
    - Only fires on strong momentum confirmation
    - ~7 signals/day, highest win rate (63.7%)
    - High-conviction overlay

All 3 bots use VWAP as S/R confirmation and RSI as choppy-market filter.
ADX filter: skip when ADX < 20 (no trending market).

Key insight from backtests: Adding RSI as entry filter DESTROYS edge.
RSI is used only as an EXIT signal, not entry filter.
"""
import pandas as pd
import numpy as np
from typing import Optional

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies.base_strategy import BaseStrategy, Signal
from data.indicators import add_all_indicators
from config import (
    CRYPTO_POSITION_SIZE_USD, CRYPTO_STOP_LOSS_PCT, CRYPTO_TAKE_PROFIT_PCT,
    CRYPTO_RSI_OVERBOUGHT, CRYPTO_RSI_OVERSOLD, CRYPTO_MIN_ADX,
    CRYPTO_MACD1_FAST, CRYPTO_MACD1_SLOW, CRYPTO_MACD1_SIGNAL,
    CRYPTO_MACD2_FAST, CRYPTO_MACD2_SLOW, CRYPTO_MACD2_SIGNAL,
    CRYPTO_MACD3_FAST, CRYPTO_MACD3_SLOW, CRYPTO_MACD3_SIGNAL,
    CRYPTO_MACD3_HISTOGRAM_THRESHOLD,
)


class CryptoMACDStrategy(BaseStrategy):
    """
    Three-variant MACD strategy for crypto on 5-minute candles.
    Bot variant is selectable: 'workhorse', 'classic', or 'sniper'.
    Default uses consensus of all three for highest confidence.
    """

    VARIANT_WORKHORSE = 'workhorse'   # MACD(3/15/3) histogram > 0
    VARIANT_CLASSIC   = 'classic'     # MACD(4/16/3) line vs signal
    VARIANT_SNIPER    = 'sniper'      # MACD(6/20/5) histogram > threshold

    def __init__(self, variant: str = 'consensus'):
        """
        variant: 'workhorse' | 'classic' | 'sniper' | 'consensus'
        'consensus' requires agreement from at least 2 of 3 bots.
        """
        name = f'crypto_macd_{variant}'
        super().__init__(name)
        self.variant = variant

    def generate_signal(self, symbol: str, df: pd.DataFrame) -> Signal:
        """
        df: 5-min OHLCV with indicators added (or will be added here).
        Returns Signal.
        """
        if df is None or len(df) < 30:
            return self._hold(symbol, 0.0, "Insufficient candle data")

        df = add_all_indicators(df)
        last = df.iloc[-1]
        price = float(last['close'])

        if price <= 0:
            return self._hold(symbol, price, "Invalid price")

        prev = df.iloc[-2] if len(df) >= 2 else last

        # ── Extract all indicators ─────────────────────────────────────────────
        rsi = float(last.get('rsi', 50) or 50)
        adx = float(last.get('adx', 25) or 25)
        vwap = float(last.get('vwap', price) or price)

        # MACD Variant 1: Workhorse (3/15/3)
        m1_hist = float(last.get('macd1_hist', 0) or 0)
        m1_hist_prev = float(prev.get('macd1_hist', 0) or 0)

        # MACD Variant 2: Classic (4/16/3)
        m2_line = float(last.get('macd2_line', 0) or 0)
        m2_sig = float(last.get('macd2_sig', 0) or 0)
        m2_line_prev = float(prev.get('macd2_line', 0) or 0)
        m2_sig_prev = float(prev.get('macd2_sig', 0) or 0)

        # MACD Variant 3: Sniper (6/20/5)
        m3_hist = float(last.get('macd3_hist', 0) or 0)
        m3_hist_prev = float(prev.get('macd3_hist', 0) or 0)

        # Dynamic threshold for sniper: 0.5% of price
        sniper_threshold = price * CRYPTO_MACD3_HISTOGRAM_THRESHOLD

        # ── Choppy market filter (key insight: avoid low ADX) ─────────────────
        if adx < CRYPTO_MIN_ADX:
            return self._hold(symbol, price,
                              f"Market choppy — ADX {adx:.1f} < {CRYPTO_MIN_ADX}")

        # ══════════════════════════════════════════════════════════════════════
        # EVALUATE EACH VARIANT
        # ══════════════════════════════════════════════════════════════════════

        v1_signal = self._eval_workhorse(m1_hist, m1_hist_prev, price, vwap, rsi)
        v2_signal = self._eval_classic(m2_line, m2_sig, m2_line_prev, m2_sig_prev, price, vwap, rsi)
        v3_signal = self._eval_sniper(m3_hist, m3_hist_prev, sniper_threshold, price, vwap, rsi)

        # ── Dispatch to requested variant ─────────────────────────────────────
        if self.variant == self.VARIANT_WORKHORSE:
            action, conf, reason = v1_signal
        elif self.variant == self.VARIANT_CLASSIC:
            action, conf, reason = v2_signal
        elif self.variant == self.VARIANT_SNIPER:
            action, conf, reason = v3_signal
        else:
            # Consensus mode: need ≥2 of 3 bots to agree
            action, conf, reason = self._consensus(
                v1_signal, v2_signal, v3_signal
            )

        if action == 'HOLD':
            return self._hold(symbol, price, reason)

        # ── EXIT signals override ENTRY ────────────────────────────────────────
        exit_action, exit_reason = self._check_exits(
            rsi, m1_hist, m2_line, m2_sig, price, vwap
        )
        if exit_action == 'SELL' and action == 'HOLD':
            action = 'SELL'
            reason = exit_reason
            conf = 0.75

        # ── Build final signal ─────────────────────────────────────────────────
        from risk.risk_manager import get_risk_manager
        rm = get_risk_manager()
        atr = float(last.get('atr', price * 0.005) or price * 0.005)
        stop_loss = rm.calc_stop_loss(price, self.name, atr=atr)
        take_profit = rm.calc_take_profit(price, self.name, atr=atr)

        signal = Signal(
            action=action,
            symbol=symbol,
            strategy=self.name,
            confidence=conf,
            reason=reason,
            price=price,
            suggested_size_usd=CRYPTO_POSITION_SIZE_USD,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                'variant': self.variant,
                'rsi': rsi,
                'adx': adx,
                'macd1_hist': m1_hist,
                'macd2_crossover': m2_line > m2_sig,
                'macd3_hist': m3_hist,
                'vwap': vwap,
                'price_vs_vwap': price / vwap if vwap > 0 else 1.0,
            }
        )
        self._last_signal = signal
        return signal

    # ── Variant evaluators ────────────────────────────────────────────────────

    def _eval_workhorse(self, hist, hist_prev, price, vwap, rsi) -> tuple:
        """MACD(3/15/3) histogram > 0 = BUY, < 0 = SELL."""
        if hist > 0:
            conf = 0.65 + (0.10 if price > vwap else 0)
            return 'BUY', conf, f"MACD1 hist {hist:.6f}>0 | {'above' if price>vwap else 'below'} VWAP"
        elif hist < 0:
            conf = 0.65 + (0.10 if price < vwap else 0)
            return 'SELL', conf, f"MACD1 hist {hist:.6f}<0"
        return 'HOLD', 0.0, "MACD1 neutral"

    def _eval_classic(self, line, sig, line_prev, sig_prev, price, vwap, rsi) -> tuple:
        """MACD(4/16/3) line vs signal crossover."""
        if line > sig:
            # Crossover confirmation (just crossed)
            bonus = 0.10 if (line_prev <= sig_prev) else 0
            conf = 0.60 + bonus + (0.10 if price > vwap else 0)
            cross_note = " [fresh cross]" if bonus else ""
            return 'BUY', conf, f"MACD2 line>signal{cross_note}"
        elif line < sig:
            bonus = 0.10 if (line_prev >= sig_prev) else 0
            conf = 0.60 + bonus + (0.10 if price < vwap else 0)
            cross_note = " [fresh cross]" if bonus else ""
            return 'SELL', conf, f"MACD2 line<signal{cross_note}"
        return 'HOLD', 0.0, "MACD2 lines equal"

    def _eval_sniper(self, hist, hist_prev, threshold, price, vwap, rsi) -> tuple:
        """MACD(6/20/5) histogram > threshold = strong momentum only."""
        if hist > threshold:
            conf = 0.70 + (0.10 if price > vwap else 0)
            return 'BUY', conf, f"MACD3 sniper hist {hist:.6f} > threshold (strong momentum)"
        elif hist < -threshold:
            conf = 0.70 + (0.10 if price < vwap else 0)
            return 'SELL', conf, f"MACD3 sniper hist {hist:.6f} < -threshold"
        return 'HOLD', 0.0, f"MACD3 hist {hist:.6f} below sniper threshold"

    def _consensus(self, v1, v2, v3) -> tuple:
        """Require ≥2 of 3 bots to agree on direction."""
        signals = [v1[0], v2[0], v3[0]]
        confs = [v1[1], v2[1], v3[1]]
        reasons = [v1[2], v2[2], v3[2]]

        buy_count = signals.count('BUY')
        sell_count = signals.count('SELL')

        if buy_count >= 2:
            avg_conf = sum(c for s, c in zip(signals, confs) if s == 'BUY') / buy_count
            # Consensus boosts confidence
            final_conf = min(avg_conf * 1.1, 0.95)
            active_reasons = [r for s, r in zip(signals, reasons) if s == 'BUY']
            return 'BUY', final_conf, f"CONSENSUS BUY ({buy_count}/3): " + " | ".join(active_reasons)

        if sell_count >= 2:
            avg_conf = sum(c for s, c in zip(signals, confs) if s == 'SELL') / sell_count
            final_conf = min(avg_conf * 1.1, 0.95)
            active_reasons = [r for s, r in zip(signals, reasons) if s == 'SELL']
            return 'SELL', final_conf, f"CONSENSUS SELL ({sell_count}/3): " + " | ".join(active_reasons)

        return 'HOLD', 0.0, "No consensus — bots disagree"

    # ── Exit evaluation ───────────────────────────────────────────────────────

    def _check_exits(self, rsi, m1_hist, m2_line, m2_sig, price, vwap) -> tuple:
        """Check for exit conditions on an open long position."""
        exits = []

        if rsi > CRYPTO_RSI_OVERBOUGHT:
            exits.append(f"RSI overbought ({rsi:.1f})")

        if m1_hist < 0:
            exits.append("MACD1 hist turned negative")

        if m2_line < m2_sig:
            exits.append("MACD2 line crossed below signal")

        if price < vwap * 0.997:
            exits.append(f"Price broke below VWAP (${vwap:.2f})")

        if exits:
            return 'SELL', ' | '.join(exits)

        return 'HOLD', ''
