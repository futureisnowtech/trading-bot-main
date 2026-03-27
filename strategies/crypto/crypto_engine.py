"""
strategies/crypto/crypto_engine.py — Four-signal hierarchy for crypto entries.

Signal priority (highest wins, sets size_multiplier):
  1. Liquidation cascade  — funding spike + OI collapse → longs liquidated → BUY 1.5x
  2. Cross-pair divergence — symbol diverges from BTC by > 1.5% → buy the laggard  1.0x
  3. Order book imbalance  — persistent bid pressure (OBI > 0.40)                    0.75x
  4. MACD consensus        — 3-variant MACD aligned fallback                          0.50x

Hard rules (code-enforced, no debate can override):
  - LIMIT ORDERS ONLY for entries. Market orders are reserved for emergency exits.
  - No entries 11:00am – 2:00pm ET (lunch dead zone — thin books, wide spreads).
  - Check funding rate before every entry (via market_data['funding_rate_pct']).
  - Scan on 5-minute bar closes (enforced via CRYPTO_CANDLE_GRANULARITY in config).

Returns EngineSignal with the signal type and a size_multiplier that unified_sizer
applies as a scale factor on top of the base position size.
"""
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import (
    MARKET_TIMEZONE,
    FUNDING_OVERHEATED_PCT,
    ATR_FEE_FLOOR_PCT,
)

# ── Thresholds ────────────────────────────────────────────────────────────────

# Liquidation cascade
CASCADE_FUNDING_PCT = 0.03      # funding ≥ 0.03%/8h signals crowded longs
CASCADE_OI_DROP_PCT = -0.01     # OI dropped ≥ 1% = longs being unwound

# Cross-pair divergence
DIVERGENCE_THRESHOLD_PCT = 1.5  # % price divergence vs BTC triggers signal

# Order book imbalance
OBI_STRONG_THRESHOLD = 0.40     # OBI ≥ 0.40 = 3:1+ bid/ask pressure

# Lunch dead zone
LUNCH_DEAD_ZONE_START = 11      # 11:00am ET
LUNCH_DEAD_ZONE_END   = 14      # 2:00pm ET


@dataclass
class EngineSignal:
    action:          str   = 'HOLD'       # 'BUY' | 'HOLD'
    signal_type:     str   = 'none'       # 'cascade' | 'divergence' | 'obi' | 'macd_fallback' | 'none'
    size_multiplier: float = 0.0          # 1.5 | 1.0 | 0.75 | 0.5 | 0.0
    order_type:      str   = 'limit'      # always 'limit' — market reserved for emergency exits
    confidence:      float = 0.0
    reason:          str   = ''
    fired_signals:   list  = field(default_factory=list)   # all signals that fired (for agent context)


def _get_et_hour() -> int:
    """Return current Eastern Time hour."""
    try:
        tz = pytz.timezone(MARKET_TIMEZONE)
        return datetime.now(tz).hour
    except Exception:
        return 12  # safe fallback — neutral hour


def _is_lunch_dead_zone() -> bool:
    """True during 11am–2pm ET — thin books, wide spreads, avoid."""
    hour = _get_et_hour()
    return LUNCH_DEAD_ZONE_START <= hour < LUNCH_DEAD_ZONE_END


# ── Signal detectors ──────────────────────────────────────────────────────────

def _detect_cascade(market_data: dict) -> bool:
    """
    Liquidation cascade: funding rate crowded AND OI is dropping.
    Meaning: longs are over-leveraged AND positions are being closed (forced).
    The resulting price move is often sharp and one-directional — ride it.
    """
    funding_pct = market_data.get('funding_rate_pct')
    oi_change   = market_data.get('oi_change_pct')

    if funding_pct is None or oi_change is None:
        return False

    try:
        fp = float(funding_pct)
        oi = float(oi_change)
    except (TypeError, ValueError):
        return False

    # Funding ≥ threshold (crowded longs) AND OI falling (positions being closed)
    return fp >= CASCADE_FUNDING_PCT and oi <= CASCADE_OI_DROP_PCT


def _detect_divergence(
    symbol: str,
    market_data: dict,
    btc_change_pct: Optional[float],
) -> bool:
    """
    Cross-pair divergence: this symbol's recent change diverges from BTC by > threshold.

    Logic:
      - BTC is the market beta. Everything roughly tracks BTC.
      - If ETH dropped 3% but BTC only dropped 0.5%, ETH is oversold relative to BTC.
      - Buy the laggard — it will converge.

    Returns True when: symbol is significantly below BTC's move (oversold relative).
    `btc_change_pct` = BTC's 5-min % change. `change_pct` in market_data = this symbol's.
    """
    if btc_change_pct is None:
        return False

    symbol_change = float(market_data.get('change_pct', 0.0))

    # Symbol needs to be lagging BTC by more than threshold
    divergence = btc_change_pct - symbol_change   # positive = symbol is below BTC
    return divergence >= DIVERGENCE_THRESHOLD_PCT


def _detect_obi(market_data: dict) -> bool:
    """
    Order book imbalance: persistent bid pressure.
    OBI = (bid_depth - ask_depth) / (bid_depth + ask_depth)
    OBI > 0.40 ≈ 70% bids vs 30% asks — strong buy-side pressure.
    """
    obi = market_data.get('obi')
    if obi is None:
        return False
    try:
        return float(obi) >= OBI_STRONG_THRESHOLD
    except (TypeError, ValueError):
        return False


def _detect_macd_consensus(market_data: dict) -> bool:
    """
    MACD consensus: all three MACD variants (workhorse/classic/sniper) aligned bullish.
    Reads `macd_consensus` flag already computed by the scanner from CryptoMACDStrategy.
    """
    return bool(market_data.get('macd_consensus', False))


# ── Pre-entry hard checks ─────────────────────────────────────────────────────

def _pre_entry_ok(symbol: str, market_data: dict) -> tuple:
    """
    Hard checks that apply regardless of signal type.
    Returns (ok: bool, reason: str).
    """
    # ATR fee floor — can't clear fees, don't even try
    atr   = float(market_data.get('atr', 0.0))
    price = float(market_data.get('price', 1.0))
    if price > 0 and (atr / price) < ATR_FEE_FLOOR_PCT:
        return False, f"ATR/price={atr/price:.3%} < {ATR_FEE_FLOOR_PCT:.3%} fee floor"

    # Lunch dead zone
    if _is_lunch_dead_zone():
        return False, f"lunch dead zone ({LUNCH_DEAD_ZONE_START}am–{LUNCH_DEAD_ZONE_END}pm ET)"

    # Overheated funding (for ALL signals, not just cascade)
    funding_pct = market_data.get('funding_rate_pct')
    if funding_pct is not None:
        try:
            if float(funding_pct) > FUNDING_OVERHEATED_PCT:
                return False, f"funding overheated ({float(funding_pct):.4f}%/8h > {FUNDING_OVERHEATED_PCT}%)"
        except (TypeError, ValueError):
            pass

    return True, ''


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate(
    symbol: str,
    market_data: dict,
    btc_change_pct: Optional[float] = None,
) -> EngineSignal:
    """
    Evaluate the four-signal hierarchy for a single symbol.

    Args:
        symbol:         Instrument symbol (e.g., 'BTC-USDC').
        market_data:    Full market_data dict from _build_market_data().
        btc_change_pct: BTC's 5-min % change (for divergence signal). Pass None to skip.

    Returns:
        EngineSignal with the highest-priority signal that fired.
        If nothing fires (or hard block), returns EngineSignal(action='HOLD').
    """
    # Hard pre-entry checks first
    ok, block_reason = _pre_entry_ok(symbol, market_data)
    if not ok:
        return EngineSignal(action='HOLD', signal_type='blocked',
                            size_multiplier=0.0, reason=block_reason)

    fired = []

    # Detect each signal
    cascade   = _detect_cascade(market_data)
    divergence = _detect_divergence(symbol, market_data, btc_change_pct)
    obi_hit   = _detect_obi(market_data)
    macd_hit  = _detect_macd_consensus(market_data)

    if cascade:    fired.append('cascade')
    if divergence: fired.append('divergence')
    if obi_hit:    fired.append('obi')
    if macd_hit:   fired.append('macd_fallback')

    if not fired:
        return EngineSignal(action='HOLD', signal_type='none',
                            size_multiplier=0.0, reason='no signals fired',
                            fired_signals=[])

    # Priority: cascade > divergence > obi > macd_fallback
    if cascade:
        return EngineSignal(
            action='BUY',
            signal_type='cascade',
            size_multiplier=1.5,
            order_type='limit',
            confidence=0.80,
            reason=(f"Liquidation cascade: funding={market_data.get('funding_rate_pct', '?'):.4f}%/8h "
                    f"OI_chg={market_data.get('oi_change_pct', '?'):.2f}% — "
                    f"forced long liquidation, ride the forced unwind"),
            fired_signals=fired,
        )

    if divergence:
        diff = float(btc_change_pct or 0) - float(market_data.get('change_pct', 0))
        return EngineSignal(
            action='BUY',
            signal_type='divergence',
            size_multiplier=1.0,
            order_type='limit',
            confidence=0.65,
            reason=(f"Cross-pair divergence: {symbol} lagging BTC by {diff:.2f}% — "
                    f"buy the laggard, expect mean reversion"),
            fired_signals=fired,
        )

    if obi_hit:
        obi_val = market_data.get('obi', '?')
        return EngineSignal(
            action='BUY',
            signal_type='obi',
            size_multiplier=0.75,
            order_type='limit',
            confidence=0.55,
            reason=(f"Order book imbalance: OBI={obi_val:.2f} (>{OBI_STRONG_THRESHOLD}) — "
                    f"persistent buy-side pressure"),
            fired_signals=fired,
        )

    # MACD consensus fallback — lowest priority, smallest size
    return EngineSignal(
        action='BUY',
        signal_type='macd_fallback',
        size_multiplier=0.50,
        order_type='limit',
        confidence=0.45,
        reason="MACD consensus (3 variants aligned) — momentum confirmation fallback",
        fired_signals=fired,
    )


def get_signal_tags(signal: EngineSignal) -> list:
    """
    Return a list of signal tag strings for agent context / trade attribution.
    Matches the format used by the existing signal_triggers field.
    """
    tag_map = {
        'cascade':      'liq_cascade',
        'divergence':   'cross_pair_divergence',
        'obi':          f'OBI_strong(>{OBI_STRONG_THRESHOLD})',
        'macd_fallback': 'MACD_consensus_fallback',
    }
    return [tag_map.get(s, s) for s in signal.fired_signals]
