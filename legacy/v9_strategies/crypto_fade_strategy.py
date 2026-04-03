"""
strategies/crypto_fade_strategy.py

Fade-the-rally strategy for ranging/volatile crypto markets.

What it does:
  Fades (sells into) overbought price extensions in non-trending markets.
  When price has stretched above the daily Kalman estimate AND AVWAP AND is
  touching the upper Bollinger Band in a ranging regime, the statistical
  expectation is reversion back toward the mean. We SHORT the overshoot.

When it activates:
  - Regime is 'ranging' or 'volatile' (NOT trending — never fade a real trend)
  - Price is near the UPPER Bollinger Band (within 1.2% below)
  - Price is above Kalman estimate by >= +0.80% (overbought vs filtered trend)
  - Price is above AVWAP by >= +0.50% (extended above volume-weighted anchor)
  - ADX < 22 (market NOT trending — above this, a breakout is more likely)
  - MACD histogram not in strong bullish momentum (not a blow-off top we'd miss)

Target: Middle Bollinger Band (mean-reversion target)
Stop: 2% above entry (above upper BB noise)

Fee math:
  Gross target: BB mid - price ≈ 3-5% in volatile conditions.
  Net win: 4% - 0.8% fees = 3.2%.  Net loss: 2% + 1.0% = 3%.
  R:R = 3.2/3 = 1.07x. Break-even WR: 3/(3+3.2) = 48%.
  With wider BBs (5%+): R:R improves significantly.

On Coinbase spot: paper-trades only (Coinbase doesn't support spot shorting).
For live short execution: signals route to Binance perp via perp_scanner integration.
"""
import pandas as pd
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies.base_strategy import Signal
from data.indicators import add_all_indicators
from config import CRYPTO_POSITION_SIZE_USD


# ── Thresholds ─────────────────────────────────────────────────────────────────
_STOP_LOSS_PCT   = 0.020   # 2.0% stop ABOVE entry for SHORT
_BB_PROXIMITY    = 0.012   # Must be within 1.2% BELOW upper BB
_ADX_MAX         = 22.0    # Max ADX — above this = trending, too risky to fade
_MIN_RR          = 2.0     # Minimum 2x R:R
_MIN_REWARD_PCT  = 0.04    # Target must be ≥4% below entry (fee viability)
_CONF_LOW        = 0.45
_CONF_HIGH       = 0.72
_FALLBACK_TP_PCT = 0.055   # Fallback TP (5.5% below entry) if BB mid unavailable


def get_fade_signal(
    symbol: str,
    market_data: dict,
    candles_df: pd.DataFrame,
) -> Signal:
    """
    Evaluate a fade-the-rally (SHORT) entry for the given symbol.

    Returns Signal(action='SELL') when all conditions pass — caller must check
    this and handle it as a SHORT entry (paper log or perp route).
    Returns Signal(action='HOLD') when conditions are not met.
    """
    if candles_df is None or len(candles_df) < 30:
        return _hold(symbol, 0.0, "Insufficient candle data for fade strategy")

    df = add_all_indicators(candles_df)
    last = df.iloc[-1]
    price = float(last.get('close', 0) or 0)

    if price <= 0:
        return _hold(symbol, price, "Invalid price")

    # ── Extract indicators ────────────────────────────────────────────────────
    adx        = float(last.get('adx', 25) or 25)
    bb_upper   = float(last.get('bb_upper', 0) or 0)
    bb_mid     = float(last.get('bb_mid', 0) or 0)
    macd_hist  = float(last.get('macd1_hist', 0) or 0)
    kalman_dev = float(market_data.get('kalman_dev', 0.0) or 0.0)
    avwap_dev  = float(market_data.get('avwap_dev', 0.0) or 0.0)

    # ── Condition 1: Regime ───────────────────────────────────────────────────
    regime = market_data.get('regime', 'ranging')
    if regime not in ('ranging', 'volatile'):
        return _hold(symbol, price,
                     f"Regime '{regime}' not suitable for fade (only ranging/volatile)")
    # Never fade a downtrend (price near upper BB in a downtrend = just a bounce)
    if regime == 'trending_down':
        return _hold(symbol, price, "Regime trending_down — fade abort (could be reversal)")

    # ── Condition 2: Overbought via advanced math signals ────────────────────
    kalman_ob = kalman_dev >= 0.80    # price ≥0.8% above Kalman filtered trend
    avwap_ob  = avwap_dev  >= 0.50   # price ≥0.5% above volume-weighted anchor
    if not (kalman_ob or avwap_ob):
        return _hold(symbol, price,
                     f"Not overbought: Kalman={kalman_dev:+.2f}% AVWAP={avwap_dev:+.2f}% "
                     f"— need Kalman≥+0.8% or AVWAP≥+0.5%")

    # ── Condition 3: Price near upper Bollinger Band ──────────────────────────
    if bb_upper <= 0:
        return _hold(symbol, price, "Bollinger Band upper not available")
    dist_below_upper = (bb_upper - price) / price   # positive = price is below upper BB
    if dist_below_upper > _BB_PROXIMITY:
        return _hold(symbol, price,
                     f"Price ${price:.4f} not near upper BB ${bb_upper:.4f} "
                     f"(dist={dist_below_upper:.2%} > {_BB_PROXIMITY:.1%})")

    # ── Condition 4: ADX confirms low trend strength ──────────────────────────
    if adx >= _ADX_MAX:
        return _hold(symbol, price,
                     f"ADX {adx:.1f} >= {_ADX_MAX:.1f} — market is trending, too risky to fade")

    # ── Condition 5: MACD not in strong bullish momentum ─────────────────────
    strong_bull_threshold = 0.003 * price
    if macd_hist >= strong_bull_threshold:
        return _hold(symbol, price,
                     f"MACD hist {macd_hist:.6f} strongly bullish — momentum too strong to fade")

    # ── Build SHORT trade parameters ──────────────────────────────────────────
    stop_loss  = price * (1.0 + _STOP_LOSS_PCT)   # stop ABOVE entry (SHORT)
    stop_dist  = stop_loss - price

    # Take-profit = BB mid (mean-reversion target below current price)
    if 0 < bb_mid < price:
        take_profit = bb_mid
    else:
        take_profit = price * (1.0 - _FALLBACK_TP_PCT)

    # ── Minimum R:R ───────────────────────────────────────────────────────────
    reward_dist = price - take_profit   # positive for SHORT
    if stop_dist <= 0 or (reward_dist / stop_dist) < _MIN_RR:
        rr = reward_dist / stop_dist if stop_dist > 0 else 0.0
        return _hold(symbol, price,
                     f"R:R {rr:.2f}x below minimum {_MIN_RR}x — HOLD")

    # ── Minimum absolute reward (fee viability) ───────────────────────────────
    if reward_dist / price < _MIN_REWARD_PCT:
        return _hold(symbol, price,
                     f"Reward {reward_dist/price:.2%} < {_MIN_REWARD_PCT:.0%} min — "
                     f"BB too tight to cover fees")

    # ── Confidence: both Kalman AND AVWAP overbought = stronger signal ────────
    if kalman_ob and avwap_ob:
        confidence = _CONF_HIGH
    elif kalman_ob or avwap_ob:
        confidence = (_CONF_LOW + _CONF_HIGH) / 2
    else:
        confidence = _CONF_LOW
    confidence = round(confidence, 4)

    reason = (
        f"Fade rally: regime={regime} "
        f"Kalman={kalman_dev:+.2f}% AVWAP={avwap_dev:+.2f}% "
        f"near upper BB (${bb_upper:.4f}, {dist_below_upper:.2%} below) ADX={adx:.1f} | "
        f"SHORT → bb_mid ${take_profit:.4f} stop ${stop_loss:.4f} R:R={reward_dist/stop_dist:.2f}x"
    )

    return Signal(
        action='SELL',   # 'SELL' = short entry (caller routes to paper log or perp)
        symbol=symbol,
        strategy='crypto_fade',
        confidence=confidence,
        reason=reason,
        price=price,
        suggested_size_usd=CRYPTO_POSITION_SIZE_USD,
        stop_loss=stop_loss,
        take_profit=take_profit,
        metadata={
            'regime':            regime,
            'kalman_dev':        kalman_dev,
            'avwap_dev':         avwap_dev,
            'adx':               adx,
            'bb_upper':          bb_upper,
            'bb_mid':            bb_mid,
            'dist_below_upper':  round(dist_below_upper, 6),
            'stop_pct':          _STOP_LOSS_PCT,
            'reward_risk':       round(reward_dist / stop_dist, 3),
        },
    )


def _hold(symbol: str, price: float, reason: str) -> Signal:
    return Signal(
        action='HOLD',
        symbol=symbol,
        strategy='crypto_fade',
        confidence=0.0,
        reason=reason,
        price=price,
    )
