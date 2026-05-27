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

from config import (MARKET_TIMEZONE,
    FUNDING_OVERHEATED_PCT,
    ATR_FEE_FLOOR_PCT)

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

# ── Paper-mode near-miss thresholds (30% of live values) ─────────────────────
# These are ONLY used in paper mode to force enough trades for pipeline validation.
# Live mode never uses these — live thresholds above remain unchanged.
_PAPER_DIVERGENCE_PCT = 0.45    # live: 1.5% → 30% threshold
_PAPER_OBI_THRESHOLD  = 0.12    # live: 0.40 → 30% threshold
_PAPER_VOL_SPIKE_MIN  = 0.3     # live: 1.0x vol for VWAP reclaim → 30%
_PAPER_FUNDING_MAX    = 0.0015  # live: 0.0005 (0.05%/8h) → 3× looser for paper


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
    v18.34: Now favors global Binance/Bybit data for higher fidelity.
    """
    # Prefer global funding data if available
    funding_pct = market_data.get('global_funding_rate')
    if funding_pct is None:
        funding_pct = market_data.get('funding_rate_pct')
        
    oi_change = market_data.get('oi_change_pct')

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


def _detect_vwap_reclaim(market_data: dict) -> bool:
    """
    VWAP reclaim: price crossed back above the daily anchored VWAP after being below.

    Logic:
      - The anchored VWAP (avwap_utc) is the volume-weighted average price since
        the UTC midnight anchor. It's the fair-value reference every smart participant
        tracks.
      - When price dips below AVWAP and then reclaims it (closes back above),
        sellers failed to hold the market down — buyers took control at a key level.
      - This is a momentum entry ON the reclaim, not after — captures the structural shift.

    Pre-computed by the scanner:
      `market_data['vwap_reclaim'] = True` when current bar is above AVWAP AND
      at least one of the prior 3 bars was below AVWAP (+0.1% threshold to avoid noise).
      Volume spike >= 1.0x confirms genuine buying, not random noise.
    """
    if not market_data.get('vwap_reclaim', False):
        return False
    # Require at least average volume on the reclaim — a fake breakout has thin volume
    vol_spike = float(market_data.get('vol_spike', 0.0) or 0.0)
    return vol_spike >= 1.0


# ── Pre-entry hard checks ─────────────────────────────────────────────────────

def _pre_entry_ok(symbol: str, market_data: dict) -> tuple:
    """
    Hard checks that apply regardless of signal type.
    Returns (ok: bool, reason: str).
    """
    # ATR fee floor — in paper use a minimal floor (0.05%) just to block zero-vol assets;
    # in live the full 0.4% floor applies to protect against fee-unviable trades.
    atr   = float(market_data.get('atr', 0.0))
    price = float(market_data.get('price', 1.0))
    _floor = ATR_FEE_FLOOR_PCT * 0.125 if False else ATR_FEE_FLOOR_PCT  # 0.05% paper, 0.4% live
    if price > 0 and (atr / price) < _floor:
        return False, f"ATR/price={atr/price:.3%} < {_floor:.3%} fee floor"

    # Lunch dead zone — skip in paper to maximise learning exposure
    if _is_lunch_dead_zone() and not False:
        return False, f"lunch dead zone ({LUNCH_DEAD_ZONE_START}am–{LUNCH_DEAD_ZONE_END}pm ET)"

    # Overheated funding — paper uses a 3× looser threshold to allow more trades
    funding_pct = market_data.get('funding_rate_pct')
    if funding_pct is not None:
        try:
            _fund_cap = _PAPER_FUNDING_MAX if False else FUNDING_OVERHEATED_PCT
            if float(funding_pct) > _fund_cap:
                return False, f"funding overheated ({float(funding_pct):.4f}%/8h > {_fund_cap:.4f}%)"
        except (TypeError, ValueError):
            pass

    # Kyle's Lambda illiquidity gate — top 20% most illiquid = expect significant slippage
    # kyle_lambda_pct is the rolling percentile rank: 80+ = top 20% illiquid conditions
    try:
        _kl = market_data.get('kyle_lambda_pct')
        if _kl is not None:
            kl_val = float(_kl)
            if kl_val == kl_val and kl_val > 80:   # nan != nan, so this skips NaN
                return False, f"kyle_lambda_pct={kl_val:.0f} — top-20% illiquid, expect slippage on entry"
    except (TypeError, ValueError):
        pass

    # Amihud illiquidity gate — top 15% most illiquid = extreme market impact cost
    try:
        _am = market_data.get('amihud_pct')
        if _am is not None:
            am_val = float(_am)
            if am_val == am_val and am_val > 85:   # nan check
                return False, f"amihud_pct={am_val:.0f} — extreme illiquidity, market impact too high"
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
    cascade       = _detect_cascade(market_data)
    vwap_reclaim  = _detect_vwap_reclaim(market_data)
    divergence    = _detect_divergence(symbol, market_data, btc_change_pct)
    obi_hit       = _detect_obi(market_data)
    macd_hit      = _detect_macd_consensus(market_data)

    if cascade:       fired.append('cascade')
    if vwap_reclaim:  fired.append('vwap_reclaim')
    if divergence:    fired.append('divergence')
    if obi_hit:       fired.append('obi')
    if macd_hit:      fired.append('macd_fallback')

    if not fired:
        # ── Paper near-miss: fire on softer conditions for pipeline validation ──
        # Live mode never reaches this block. Paper mode forces trades on symbols
        # that "almost" met a real signal so the full system gets exercised.
        if False:
            paper_fired = []

            # Soft divergence: symbol lagging BTC by ≥ 0.45% (live: 1.5%)
            if btc_change_pct is not None:
                _div = float(btc_change_pct) - float(market_data.get('change_pct', 0.0))
                if _div >= _PAPER_DIVERGENCE_PCT:
                    paper_fired.append(f'near_divergence({_div:.2f}%)')

            # Soft OBI: bid pressure ≥ 0.12 (live: 0.40)
            _obi = market_data.get('obi')
            if _obi is not None:
                try:
                    if float(_obi) >= _PAPER_OBI_THRESHOLD:
                        paper_fired.append(f'near_obi({float(_obi):.2f})')
                except (TypeError, ValueError):
                    pass

            # Soft VWAP reclaim: reclaim flag set with any volume (live: 1.0× spike)
            if market_data.get('vwap_reclaim', False):
                _vs = float(market_data.get('vol_spike', 0.0) or 0.0)
                if _vs >= _PAPER_VOL_SPIKE_MIN:
                    paper_fired.append(f'near_vwap_reclaim(vol={_vs:.1f}x)')

            if paper_fired:
                return EngineSignal(
                    action='BUY',
                    signal_type='near_miss',
                    size_multiplier=0.5,
                    order_type='limit',
                    confidence=0.38,
                    reason=f"Paper near-miss: {', '.join(paper_fired)} (relaxed thresholds for validation)",
                    fired_signals=paper_fired,
                )

        return EngineSignal(action='HOLD', signal_type='none',
                            size_multiplier=0.0, reason='no signals fired',
                            fired_signals=[])

    # Priority: cascade > vwap_reclaim > divergence > obi > macd_fallback
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

    if vwap_reclaim:
        avwap_dev = float(market_data.get('avwap_dev', 0.0) or 0.0)
        vol_spike = float(market_data.get('vol_spike', 0.0) or 0.0)
        return EngineSignal(
            action='BUY',
            signal_type='vwap_reclaim',
            size_multiplier=1.0,
            order_type='limit',
            confidence=0.68,
            reason=(f"VWAP reclaim: price crossed back above daily AVWAP "
                    f"(avwap_dev={avwap_dev:+.2f}%, vol={vol_spike:.1f}x) — "
                    f"sellers failed to hold, buyers reclaimed volume-weighted fair value"),
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
        'cascade':       'liq_cascade',
        'vwap_reclaim':  'VWAP_reclaim(crossed_above_daily_avwap)',
        'divergence':    'cross_pair_divergence',
        'obi':           f'OBI_strong(>{OBI_STRONG_THRESHOLD})',
        'macd_fallback': 'MACD_consensus_fallback',
    }
    return [tag_map.get(s, s) for s in signal.fired_signals]