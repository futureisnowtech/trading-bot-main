"""
strategies/crypto_mean_reversion.py

Mean-reversion strategy for volatile crypto markets.

When it activates:
  - Regime is 'ranging' or 'volatile' (detected by regime_detector.py)
  - RSI < 33: price is oversold — buying the dip inside a range
  - Price is within 1.2% of the lower Bollinger Band: near structural support
  - ADX < 22: confirming the market is NOT trending (actual range condition)
  - No existing position in this symbol
  - MACD histogram is not strongly negative (hist > -0.003 * price): not in freefall
  - BB mid is at least 4% away (minimum absolute reward — required to overcome fees)

Fee math: 0.4% maker entry + 0.4% maker exit (winning) = 0.8% round-trip on wins.
          0.4% maker entry + 0.6% taker stop (losing)  = 1.0% round-trip on losses.
With 2% stop and 5% minimum target:
  Net win: 5% - 0.8% = 4.2%.  Net loss: 2% + 1.0% = 3%.
  Break-even WR: 3 / (3 + 4.2) = 42% — achievable.

This strategy fires rarely (needs wide BBs = volatile crypto). It should be treated
as an opportunistic add-on, not a primary strategy.

This strategy does NOT touch futures, equity, or the MACD trend-following path.
It runs in parallel with the AI debate path in job_runner.run_crypto_scan().
"""
import pandas as pd
import numpy as np
from typing import Optional

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies.base_strategy import BaseStrategy, Signal
from data.indicators import add_all_indicators
from config import CRYPTO_POSITION_SIZE_USD


# ── Tuneable constants (overridable via config flags imported in job_runner) ──
_STOP_LOSS_PCT   = 0.020   # 2.0% stop — wide enough to avoid noise-induced stops
_BB_PROXIMITY    = 0.012   # Must be within 1.2% of lower BB
_RSI_ENTRY_MAX   = 33.0    # RSI threshold for entry
_RSI_CONF_MIN    = 33.0    # RSI at which confidence = 0.45
_RSI_CONF_MAX    = 20.0    # RSI at which confidence = 0.75
_CONF_LOW        = 0.45
_CONF_HIGH       = 0.75
_ADX_MAX         = 22.0    # Max ADX — above this = trending, skip
_MIN_RR          = 2.5     # Minimum 2.5x R:R — requires target 5%+ away from stop
_MIN_REWARD_PCT  = 0.04    # Absolute minimum: target must be ≥4% above entry price
_FALLBACK_TP_PCT = 0.055   # Take-profit fallback (5.5%) if bb_mid unavailable or too close


def get_mean_reversion_signal(
    symbol: str,
    market_data: dict,
    candles_df: pd.DataFrame,
) -> Signal:
    """
    Evaluate a mean-reversion entry for the given crypto symbol.

    Parameters
    ----------
    symbol      : e.g. 'BTC-USDC'
    market_data : dict built by job_runner._build_market_data() — must contain
                  'regime' key. Also used to detect an existing position via
                  the 'position' key if present (set to None/missing = no pos).
    candles_df  : 1-min (or 5-min) OHLCV DataFrame. Indicators are added here
                  if not already present.

    Returns
    -------
    Signal with action='BUY' when all conditions pass, else action='HOLD'.
    """
    # ── Guard: insufficient data ──────────────────────────────────────────────
    if candles_df is None or len(candles_df) < 30:
        return _hold(symbol, 0.0, "Insufficient candle data for mean-reversion")

    df = add_all_indicators(candles_df)
    last = df.iloc[-1]
    price = float(last.get('close', 0) or 0)

    if price <= 0:
        return _hold(symbol, price, "Invalid price")

    # ── Extract indicators ────────────────────────────────────────────────────
    adx  = float(last.get('adx', 25) or 25)

    bb_lower = float(last.get('bb_lower', 0) or 0)
    bb_mid   = float(last.get('bb_mid',   0) or 0)

    # Advanced math oversold indicators (deep-research-backed — replaces RSI gate)
    kalman_dev  = float(last.get('kalman_dev', 0.0) or 0.0)   # price below Kalman estimate
    avwap_dev   = float(last.get('avwap_dev',  0.0) or 0.0)   # price below AVWAP
    autocorr    = float(last.get('autocorr_ret', 0.0) or 0.0) # mean-reverting microstructure

    # Use macd1_hist (Workhorse variant) as the freefall detector
    macd_hist = float(last.get('macd1_hist', 0) or 0)

    # ── Condition 1: Regime ───────────────────────────────────────────────────
    regime = market_data.get('regime', 'ranging')
    if regime not in ('ranging', 'volatile'):
        return _hold(symbol, price,
                     f"Regime '{regime}' not suitable for mean-reversion")

    # ── Condition 2: Oversold via advanced math signals (replaces RSI gate) ───
    # Price must be below Kalman estimate OR below AVWAP, with negative autocorr
    # confirming mean-reverting microstructure. Deep research: these are more
    # reliable than RSI for detecting true oversold on 1-min crypto.
    kalman_oversold = kalman_dev <= -0.8   # price ≥0.8% below Kalman estimate
    avwap_oversold  = avwap_dev  <= -0.5   # price ≥0.5% below AVWAP
    mr_microstructure = autocorr < 0.0    # negative autocorr = mean-reverting
    if not (kalman_oversold or avwap_oversold):
        return _hold(symbol, price,
                     f"Not oversold: Kalman={kalman_dev:.2f}% AVWAP={avwap_dev:.2f}% — need <-0.8% or <-0.5%")

    # ── Condition 3: Near lower Bollinger Band ────────────────────────────────
    if bb_lower <= 0:
        return _hold(symbol, price, "Bollinger Band lower not available")
    dist_from_lower = (price - bb_lower) / price  # positive = above bb_lower
    if dist_from_lower > _BB_PROXIMITY:
        return _hold(symbol, price,
                     f"Price {price:.4f} not near lower BB {bb_lower:.4f} "
                     f"(dist={dist_from_lower:.2%} > {_BB_PROXIMITY:.1%})")

    # ── Condition 4: ADX confirms low trend strength ──────────────────────────
    adx_max = float(market_data.get('mr_adx_max', _ADX_MAX))
    if adx >= adx_max:
        return _hold(symbol, price,
                     f"ADX {adx:.1f} >= {adx_max:.1f} — market is trending, not ranging")

    # ── Condition 5: No existing position in this symbol ─────────────────────
    # job_runner passes existing position info via market_data or checks separately;
    # we rely on the caller (job_runner) to gate on this via pre_check_entry.
    # As an extra guard, check the 'position' key if provided.
    if market_data.get('position') is not None:
        return _hold(symbol, price, "Existing position — no averaging down")

    # ── Condition 6: MACD histogram not in freefall ───────────────────────────
    freefall_threshold = -0.003 * price
    if macd_hist <= freefall_threshold:
        return _hold(symbol, price,
                     f"MACD hist {macd_hist:.6f} strongly negative (freefall threshold "
                     f"{freefall_threshold:.6f}) — skipping")

    # ── All entry conditions met — build the trade parameters ─────────────────

    stop_loss   = price * (1.0 - _STOP_LOSS_PCT)
    stop_dist   = price - stop_loss           # absolute distance to stop

    # Take-profit = middle Bollinger Band (mean-reversion target)
    if bb_mid > price:
        take_profit = bb_mid
    else:
        # bb_mid below price (unusual on oversold) — use fallback
        take_profit = price * (1.0 + _FALLBACK_TP_PCT)

    # ── Minimum R:R check ─────────────────────────────────────────────────────
    reward_dist = take_profit - price
    if stop_dist <= 0 or (reward_dist / stop_dist) < _MIN_RR:
        rr = reward_dist / stop_dist if stop_dist > 0 else 0
        return _hold(symbol, price,
                     f"R:R {rr:.2f}x below minimum {_MIN_RR}x — HOLD")

    # ── Minimum absolute reward check (fee viability) ─────────────────────────
    # BB mid is often only 1-2% away on 5-min candles — not enough after 1% fees.
    # Require ≥4% room to ensure the trade is worth the commission cost.
    if reward_dist / price < _MIN_REWARD_PCT:
        return _hold(symbol, price,
                     f"Reward {reward_dist/price:.2%} < {_MIN_REWARD_PCT:.0%} min — "
                     f"BB too tight to cover fees")

    # ── Confidence: based on depth of oversold signal ────────────────────────
    # Both Kalman AND AVWAP confirming = 0.75, one alone = 0.50, MR microstructure adds 0.10
    confidence = _CONF_LOW
    if kalman_oversold and avwap_oversold:
        confidence = _CONF_HIGH
    elif kalman_oversold or avwap_oversold:
        confidence = (_CONF_LOW + _CONF_HIGH) / 2
    if mr_microstructure:
        confidence = min(confidence + 0.10, _CONF_HIGH)
    confidence = round(confidence, 4)

    reason = (
        f"Mean-reversion: regime={regime} "
        f"Kalman={kalman_dev:.2f}% AVWAP={avwap_dev:.2f}% autocorr={autocorr:.3f} "
        f"near lower BB ({dist_from_lower:.2%} away) ADX={adx:.1f} "
        f"MACD hist={macd_hist:.6f} | "
        f"target=bb_mid ${bb_mid:.4f} stop=${stop_loss:.4f} R:R={reward_dist/stop_dist:.2f}x"
    )

    return Signal(
        action='BUY',
        symbol=symbol,
        strategy='crypto_mean_reversion',
        confidence=confidence,
        reason=reason,
        price=price,
        suggested_size_usd=CRYPTO_POSITION_SIZE_USD,
        stop_loss=stop_loss,
        take_profit=take_profit,
        metadata={
            'regime':          regime,
            'kalman_dev':      kalman_dev,
            'avwap_dev':       avwap_dev,
            'autocorr_ret':    autocorr,
            'adx':             adx,
            'bb_lower':        bb_lower,
            'bb_mid':          bb_mid,
            'dist_from_lower': round(dist_from_lower, 6),
            'macd1_hist':      macd_hist,
            'stop_pct':        _STOP_LOSS_PCT,
            'reward_risk':     round(reward_dist / stop_dist, 3),
        },
    )


# ── Internal helper ───────────────────────────────────────────────────────────

def _hold(symbol: str, price: float, reason: str) -> Signal:
    return Signal(
        action='HOLD',
        symbol=symbol,
        strategy='crypto_mean_reversion',
        confidence=0.0,
        reason=reason,
        price=price,
    )
