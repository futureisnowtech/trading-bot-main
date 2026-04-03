"""
strategies/crypto_range_scalper.py

Range scalper for ultra-flat/choppy crypto markets.

What it does:
  The "iron condor equivalent" for crypto. When price is trapped in a confirmed
  tight Bollinger Band range (ADX < 15, CHOP > 55, OU half-life confirmed),
  buy near the LOWER boundary and target the UPPER boundary — using the full
  range width as reward instead of just the midpoint.

  This is designed for flat markets where the MACD/cascade/divergence engine
  finds nothing, but price is reliably bouncing between well-defined levels.

When it activates (LONG side only — perp scanner handles the SHORT at upper BB):
  - ADX < 15 (ultra-flat — stricter than MR's ADX < 22)
  - BB width 3–8% (enough room to trade after fees, not so wide it's volatile)
  - CHOP > 55 (confirms market is choppy/non-trending)
  - Price within 1.5% of lower BB (at range support)
  - OU half-life 5–180 min (mean-reverting microstructure confirmed)
  - Regime is NOT trending_down (avoid buying support in a real downtrend)

Target: Upper Bollinger Band (full range — not just midpoint)
Stop: 1.5% below lower BB

Fee math (BB width = 4%):
  Reward (lower→upper): ~4%. Stop distance: 1.5%.
  Net win: 4% - 0.8% fees = 3.2%.  Net loss: 1.5% + 1.0% = 2.5%.
  R:R = 3.2/2.5 = 1.28x. Break-even WR = 2.5/(2.5+3.2) = 44%.

Note: For the SHORT leg at range resistance (sell upper BB),
route to Binance perp_scanner — spot Coinbase can't short.
"""
import pandas as pd
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies.base_strategy import Signal
from data.indicators import add_all_indicators
from config import CRYPTO_POSITION_SIZE_USD


# ── Thresholds ─────────────────────────────────────────────────────────────────
_ADX_MAX        = 15.0    # Strict: must be ultra-flat (MR uses 22)
_BB_WIDTH_MIN   = 0.030   # Min BB width (3%) — below this, range too tight for fees
_BB_WIDTH_MAX   = 0.080   # Max BB width (8%) — above this, use MR or AI path instead
_BB_PROXIMITY   = 0.015   # Must be within 1.5% of lower BB
_CHOP_MIN       = 55.0    # CHOP > 55 = non-trending confirmed (61.8 = strongly choppy)
_STOP_BELOW_BB  = 0.015   # Stop 1.5% below lower BB
_MIN_RR         = 1.5     # Minimum 1.5x R:R
_MIN_REWARD_PCT = 0.025   # Minimum 2.5% reward (roughly half the min BB width)
_OU_HL_MIN      = 5.0     # OU half-life min minutes (must revert fast enough to trade)
_OU_HL_MAX      = 180.0   # OU half-life max minutes (very slow reversion = skip)
_CONF_BASE      = 0.50    # Base confidence
_CONF_MAX       = 0.72    # Max confidence


def get_range_scalper_signal(
    symbol: str,
    market_data: dict,
    candles_df: pd.DataFrame,
) -> Signal:
    """
    Evaluate a range-scalp LONG entry for the given symbol.

    Returns Signal(action='BUY') when ultra-flat conditions are confirmed and
    price is near lower BB support. Returns Signal(action='HOLD') otherwise.
    """
    if candles_df is None or len(candles_df) < 30:
        return _hold(symbol, 0.0, "Insufficient candle data for range scalper")

    df = add_all_indicators(candles_df)
    last = df.iloc[-1]
    price = float(last.get('close', 0) or 0)

    if price <= 0:
        return _hold(symbol, price, "Invalid price")

    # ── Extract indicators ────────────────────────────────────────────────────
    adx      = float(last.get('adx', 25) or 25)
    bb_lower = float(last.get('bb_lower', 0) or 0)
    bb_upper = float(last.get('bb_upper', 0) or 0)
    bb_mid   = float(last.get('bb_mid', 0) or 0)
    bb_width = float(last.get('bb_width', 0) or 0)   # (upper-lower)/mid
    chop_val = float(market_data.get('chop', 50) or 50)
    ou_hl    = market_data.get('ou_halflife_minutes')
    regime   = market_data.get('regime', 'ranging')

    # ── Condition 1: Ultra-flat ADX ───────────────────────────────────────────
    if adx >= _ADX_MAX:
        return _hold(symbol, price,
                     f"ADX {adx:.1f} >= {_ADX_MAX:.1f} — not flat enough for range scalp")

    # ── Condition 2: BB width in tradeable range ──────────────────────────────
    if bb_lower <= 0 or bb_upper <= 0 or bb_mid <= 0:
        return _hold(symbol, price, "Bollinger Bands not available")
    if bb_width < _BB_WIDTH_MIN:
        return _hold(symbol, price,
                     f"BB width {bb_width:.2%} < {_BB_WIDTH_MIN:.1%} — "
                     f"range too tight, round-trip fees won't clear")
    if bb_width > _BB_WIDTH_MAX:
        return _hold(symbol, price,
                     f"BB width {bb_width:.2%} > {_BB_WIDTH_MAX:.1%} — "
                     f"too volatile for range scalp (use MR or AI path)")

    # ── Condition 3: CHOP confirms non-trending ───────────────────────────────
    if chop_val < _CHOP_MIN:
        return _hold(symbol, price,
                     f"CHOP {chop_val:.1f} < {_CHOP_MIN:.1f} — "
                     f"market may be trending, range scalp abort")

    # ── Condition 4: Price near lower Bollinger Band ──────────────────────────
    dist_from_lower = (price - bb_lower) / price    # positive = above lower BB
    if dist_from_lower < 0:
        return _hold(symbol, price, f"Price below lower BB — wait for bounce confirmation")
    if dist_from_lower > _BB_PROXIMITY:
        return _hold(symbol, price,
                     f"Price {dist_from_lower:.2%} above lower BB — "
                     f"need to be within {_BB_PROXIMITY:.1%} of range support")

    # ── Condition 5: OU half-life confirms mean-reverting microstructure ───────
    if ou_hl is not None:
        try:
            hl = float(ou_hl)
            if not (_OU_HL_MIN <= hl <= _OU_HL_MAX):
                return _hold(symbol, price,
                             f"OU half-life {hl:.0f}min outside [{_OU_HL_MIN:.0f}, {_OU_HL_MAX:.0f}] "
                             f"— microstructure not mean-reverting in tradeable window")
        except (TypeError, ValueError):
            pass  # no OU data — don't gate on it

    # ── Condition 6: Regime soft gate — no buying support in confirmed downtrend
    if regime == 'trending_down':
        return _hold(symbol, price,
                     f"Regime trending_down — range scalp abort (buying support in downtrend = falling knife)")

    # ── Build trade parameters ────────────────────────────────────────────────
    stop_loss   = bb_lower * (1.0 - _STOP_BELOW_BB)   # 1.5% below the lower band
    stop_dist   = price - stop_loss
    take_profit = bb_upper                              # full range to upper band

    # ── R:R check ─────────────────────────────────────────────────────────────
    reward_dist = take_profit - price
    if stop_dist <= 0 or (reward_dist / stop_dist) < _MIN_RR:
        rr = reward_dist / stop_dist if stop_dist > 0 else 0.0
        return _hold(symbol, price,
                     f"R:R {rr:.2f}x below minimum {_MIN_RR}x — range asymmetric, skip")

    # ── Minimum absolute reward (fee viability) ───────────────────────────────
    if reward_dist / price < _MIN_REWARD_PCT:
        return _hold(symbol, price,
                     f"Reward {reward_dist/price:.2%} < {_MIN_REWARD_PCT:.1%} — "
                     f"BB range too narrow after fees")

    # ── Confidence: higher CHOP and confirmed OU = better signal ─────────────
    chop_bonus = min((chop_val - _CHOP_MIN) / 20.0, 0.15)   # +0–0.15 for CHOP 55–75
    confidence = min(_CONF_BASE + chop_bonus, _CONF_MAX)

    if ou_hl is not None:
        try:
            hl = float(ou_hl)
            if 10 <= hl <= 60:   # optimal mean-reversion window
                confidence = min(confidence + 0.08, _CONF_MAX)
        except (TypeError, ValueError):
            pass

    confidence = round(confidence, 4)

    ou_str = f" OU_hl={float(ou_hl):.0f}min" if ou_hl is not None else ""
    reason = (
        f"Range scalp: ADX={adx:.1f} CHOP={chop_val:.1f} "
        f"BB_width={bb_width:.2%} near lower BB ({dist_from_lower:.2%} away) "
        f"regime={regime}{ou_str} | "
        f"target=upper_BB ${bb_upper:.4f} stop=${stop_loss:.4f} R:R={reward_dist/stop_dist:.2f}x"
    )

    return Signal(
        action='BUY',
        symbol=symbol,
        strategy='crypto_range_scalper',
        confidence=confidence,
        reason=reason,
        price=price,
        suggested_size_usd=CRYPTO_POSITION_SIZE_USD * 0.75,   # smaller — tight market
        stop_loss=stop_loss,
        take_profit=take_profit,
        metadata={
            'regime':          regime,
            'adx':             adx,
            'chop':            chop_val,
            'bb_width':        round(bb_width, 4),
            'bb_lower':        bb_lower,
            'bb_upper':        bb_upper,
            'dist_from_lower': round(dist_from_lower, 6),
            'ou_halflife':     ou_hl,
            'stop_pct':        _STOP_BELOW_BB,
            'reward_risk':     round(reward_dist / stop_dist, 3),
        },
    )


def _hold(symbol: str, price: float, reason: str) -> Signal:
    return Signal(
        action='HOLD',
        symbol=symbol,
        strategy='crypto_range_scalper',
        confidence=0.0,
        reason=reason,
        price=price,
    )
