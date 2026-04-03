"""
scheduler/derivatives_momentum_scanner.py — Lane 4: Derivatives momentum signals.

Scans perp pairs for momentum signatures that precede breakouts:
  1. Funding rate acceleration: rate trending toward positive → long squeeze building
  2. OI expansion pre-breakout: open interest rising while price is consolidating
  3. Liquidation magnet proximity: cluster of liquidation levels that price is approaching

Why this is a separate scanner (not part of perp_scanner):
  perp_scanner looks for breakouts that already happened.
  This scanner looks for the CONDITIONS that cause breakouts — trades in earlier.
  Better entry, bigger move captured.

Architecture: runs as Lane 4 in run_parallel_scan() (ThreadPoolExecutor).
Quick Trade Economics Analyst-only debate gates the final entry (fee math + risk check).
"""
import time
import os
import sys
from typing import Optional

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    PERP_PAIRS, PAPER_TRADING, PERP_POSITION_SIZE_USD,
    PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT, PERP_MAX_LEVERAGE,
    PERP_MAX_POSITIONS, ANTHROPIC_API_KEY, MARKET_TIMEZONE,
)
from risk.risk_manager import get_risk_manager
from logging_db.trade_logger import log_event

# ── Thresholds ─────────────────────────────────────────────────────────────────
_FUNDING_ACCEL_THRESH  = 0.00005  # funding rate moving by 0.005%/period = acceleration
_OI_EXPANSION_THRESH   = 0.02     # OI grew ≥ 2% vs prev snapshot = pre-breakout accumulation
_MIN_CONVICTION        = 2        # need ≥ 2 of 3 signals firing to trigger debate
_SCAN_TIMEOUT          = 120      # max seconds for full scan cycle

# ── Funding rate history for acceleration detection (in-memory, per symbol) ──
_funding_history: dict = {}
_oi_history: dict = {}


def _get_binance_data(symbol: str) -> dict:
    """Fetch funding rate + OI + mark price from Binance futures public API."""
    try:
        import requests
        base = "https://fapi.binance.com"
        bsym = symbol.replace('-', '').upper()

        # Mark price + funding rate (single call)
        r1 = requests.get(f"{base}/fapi/v1/premiumIndex?symbol={bsym}", timeout=8)
        mark_price = 0.0
        funding_rate = 0.0
        if r1.status_code == 200:
            d = r1.json()
            mark_price   = float(d.get('markPrice', 0) or 0)
            funding_rate = float(d.get('lastFundingRate', 0) or 0)

        # Open interest
        r2 = requests.get(f"{base}/fapi/v1/openInterest?symbol={bsym}", timeout=8)
        oi = 0.0
        if r2.status_code == 200:
            d = r2.json()
            oi = float(d.get('openInterest', 0) or 0)

        return {
            'symbol':       symbol,
            'mark_price':   mark_price,
            'funding_rate': funding_rate,
            'oi':           oi,
        }
    except Exception:
        return {'symbol': symbol, 'mark_price': 0.0, 'funding_rate': 0.0, 'oi': 0.0}


def _detect_funding_acceleration(symbol: str, funding_rate: float) -> bool:
    """
    Detect funding rate acceleration: rate trending upward (toward positive).
    Positive funding = longs paying shorts = momentum building on long side.
    Accelerating positive funding = squeeze potential building.
    """
    hist = _funding_history.setdefault(symbol, [])
    hist.append((time.time(), funding_rate))
    # Keep last 3 readings
    if len(hist) > 3:
        hist.pop(0)

    if len(hist) < 2:
        return False

    # Is the funding rate rising (becoming more positive)?
    rates = [r for _, r in hist]
    trend = rates[-1] - rates[0]
    return trend > _FUNDING_ACCEL_THRESH and funding_rate > 0


def _detect_oi_expansion(symbol: str, oi: float) -> bool:
    """
    Detect OI expansion: open interest growing while price is consolidating.
    Rising OI = new money coming in = breakout fuel being loaded.
    """
    hist = _oi_history.setdefault(symbol, [])
    hist.append((time.time(), oi))
    if len(hist) > 3:
        hist.pop(0)

    if len(hist) < 2 or oi <= 0:
        return False

    prev_oi = hist[0][1]
    if prev_oi <= 0:
        return False

    expansion_pct = (oi - prev_oi) / prev_oi
    return expansion_pct >= _OI_EXPANSION_THRESH


def _detect_liquidation_magnet(symbol: str, mark_price: float, funding_rate: float) -> bool:
    """
    Detect proximity to liquidation cluster.
    Proxy: high positive funding (> 0.02%/8h) means leveraged longs are stacked above.
    As price pulls back, these longs get liquidated, creating a buying opportunity below.

    This is a simplified proxy — real liquidation maps require exchange data.
    When funding is very elevated + OI is also elevated = lots of leveraged positions = magnet.
    """
    if mark_price <= 0:
        return False
    # Very elevated funding = liquidation risk zone nearby
    return funding_rate > 0.0002  # > 0.02%/8h


def _run_krillin_gate(symbol: str, mark_price: float, signals: list) -> bool:
    """
    Quick Trade Economics Analyst-only check: does the fee math work? Is risk acceptable?
    Returns True if we should enter.

    Trade Economics Analyst's domain: fee math, ATR vs fees, time-of-day, volume gate.
    Here we approximate since we don't have ATR from this scanner.
    """
    if not ANTHROPIC_API_KEY:
        # No AI available — apply rule-based gate
        return len(signals) >= 2 and mark_price > 0

    try:
        import anthropic
        from config import CLAUDE_DEBATE_MODEL
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        signal_text = ', '.join(signals)
        prompt = (
            f"Symbol: {symbol} | Mark price: ${mark_price:.4f}\n"
            f"Derivative signals: {signal_text}\n"
            f"Position size: ${PERP_POSITION_SIZE_USD} notional | "
            f"Stop: {PERP_STOP_PCT*100:.1f}% | Target: {PERP_TAKE_PROFIT_PCT*100:.1f}%\n\n"
            f"Fee math: Binance futures taker = 0.04%. Round-trip = 0.08% on notional. "
            f"Stop loss = {PERP_STOP_PCT*100:.1f}% loss on notional. "
            f"Target = {PERP_TAKE_PROFIT_PCT*100:.1f}% gain on notional.\n\n"
            f"As Trade Economics Analyst (risk economist): Is the fee math viable? Is ATR likely sufficient "
            f"to clear fees? Vote BUY or HOLD with one sentence reason."
        )
        msg = client.messages.create(
            model=CLAUDE_DEBATE_MODEL,
            max_tokens=100,
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = msg.content[0].text.upper()
        return 'BUY' in text
    except Exception as e:
        print(f"[deriv_scanner] Trade Economics Analyst gate error: {e}")
        # Fail-open: rule-based fallback
        return len(signals) >= 2


def _try_enter_position(bb, rm, symbol: str, mark_price: float, signals: list) -> None:
    """Attempt to open a long perp position if risk manager allows."""
    try:
        existing = rm.get_position('crypto_perp', symbol)
        if existing is not None:
            return  # already in this symbol

        perp_positions = rm.get_all_positions().get('perp', {})
        if len(perp_positions) >= PERP_MAX_POSITIONS:
            log_event('INFO', 'deriv_scanner',
                      f"[deriv] {symbol} — perp positions full ({PERP_MAX_POSITIONS}), skip")
            return

        stop   = mark_price * (1 - PERP_STOP_PCT)
        target = mark_price * (1 + PERP_TAKE_PROFIT_PCT)
        reason = f"Deriv momentum: {', '.join(signals)}"

        result = bb.open_long(
            symbol=symbol,
            size_usd=PERP_POSITION_SIZE_USD,
            leverage=PERP_MAX_LEVERAGE,
            stop_pct=PERP_STOP_PCT,
            take_profit_pct=PERP_TAKE_PROFIT_PCT,
            strategy='crypto_perp',
        )
        if result:
            rm.register_position(
                'crypto_perp', symbol,
                PERP_POSITION_SIZE_USD / mark_price,
                mark_price, stop, target,
                direction='LONG',
                entry_reason=reason,
                signal_type='deriv_momentum',
                active_signals=signals,
            )
            log_event('INFO', 'deriv_scanner',
                      f"[deriv] ENTERED {symbol} LONG @ {mark_price:.4f} | {reason}")
            print(f"[deriv_scanner] ✅ ENTERED {symbol} | signals={signals}")
    except Exception as e:
        print(f"[deriv_scanner] entry error {symbol}: {e}")
        log_event('ERROR', 'deriv_scanner', f"{symbol} entry error: {e}")


def run_derivatives_momentum_scan() -> None:
    """
    Main scan function — runs as Lane 4 in parallel with crypto/perp/lane3.

    Scans all PERP_PAIRS for:
    1. Funding rate acceleration (rate trending positive)
    2. OI expansion (new money loading up)
    3. Liquidation magnet proximity (elevated funding = leveraged stacks)

    Entry: requires ≥ 2/3 signals + Trade Economics Analyst gate passes.
    Direction: LONG only (shorting against momentum cascade is risky).
    """
    try:
        from execution.binance_broker import get_binance_broker as _get_bb
        bb = _get_bb()
        rm = get_risk_manager()
    except Exception as e:
        print(f"[deriv_scanner] Broker init failed: {e}")
        return

    pairs = [p.strip() for p in PERP_PAIRS if p.strip()] if PERP_PAIRS else []
    if not pairs:
        return

    scan_start = time.time()
    print(f"[deriv_scanner] Scanning {len(pairs)} pairs for momentum signals...")

    hits = []
    for symbol in pairs:
        if time.time() - scan_start > _SCAN_TIMEOUT:
            break

        try:
            data = _get_binance_data(symbol)
            mp   = data['mark_price']
            fr   = data['funding_rate']
            oi   = data['oi']

            if mp <= 0:
                continue

            fired = []
            if _detect_funding_acceleration(symbol, fr):
                fired.append('funding_accel')
            if _detect_oi_expansion(symbol, oi):
                fired.append('oi_expansion')
            if _detect_liquidation_magnet(symbol, mp, fr):
                fired.append('liq_magnet')

            if len(fired) >= _MIN_CONVICTION:
                hits.append((symbol, mp, fr, fired))

        except Exception as e:
            print(f"[deriv_scanner] {symbol} scan error: {e}")
            continue

    if not hits:
        print(f"[deriv_scanner] No momentum signals fired across {len(pairs)} pairs")
        return

    print(f"[deriv_scanner] {len(hits)} candidates: {[h[0] for h in hits]}")

    # Gate each hit through Trade Economics Analyst before entering
    for symbol, mp, fr, signals in hits:
        try:
            passes = _run_krillin_gate(symbol, mp, signals)
            if passes:
                _try_enter_position(bb, rm, symbol, mp, signals)
            else:
                print(f"[deriv_scanner] {symbol} — Trade Economics Analyst gate FAILED (fee math or risk), skip")
        except Exception as e:
            print(f"[deriv_scanner] {symbol} gate error: {e}")

    print(f"[deriv_scanner] Scan complete in {time.time()-scan_start:.1f}s")
