"""
scheduler/perp_scanner.py — Binance USD-M perp futures scan and exit monitoring.

Runs every CRYPTO_SCAN_INTERVAL_SECONDS when PERP_ENABLED=true.
Long/short signals from crypto_perp_strategy, server-side SL/TP via Binance.
4-hour flat exit to avoid funding cost drain on stagnant positions.
"""
import os
import sys

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    PERP_ENABLED, PERP_PAIRS, PAPER_TRADING,
    PERP_POSITION_SIZE_USD, PERP_MAX_LEVERAGE,
    PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT,
    MARKET_TIMEZONE, FUNDING_OVERHEATED_PCT,
)
from risk.risk_manager import get_risk_manager
from logging_db.trade_logger import log_event, log_signal


def run_perp_scan() -> None:
    """Scan Binance perp pairs for long/short entry signals."""
    if not PERP_ENABLED:
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return

    from execution.binance_broker import get_binance_broker
    from strategies.crypto_perp_strategy import get_perp_signal

    bb = get_binance_broker()
    if not bb.is_connected():
        bb.connect()

    if not hasattr(run_perp_scan, '_prev_oi'):
        run_perp_scan._prev_oi = {}

    for symbol in PERP_PAIRS:
        try:
            pos = rm.get_position('crypto_perp', symbol)
            if pos:
                _monitor_perp_exit(bb, rm, symbol, pos)
                continue

            # Fetch candles from Binance (correct exchange for perp symbols like AVAXUSDT)
            df = bb.get_klines(symbol, interval='1m', limit=100)
            if df is None:
                # Binance futures geo-blocked in US — silently skip, no log spam
                continue
            if len(df) < 25:
                log_event('INFO', 'scan_feed',
                          f"[perp] {symbol} skip — only {len(df)} candles returned")
                continue

            funding = bb.get_funding_rate(symbol)
            # Gate: skip if funding is overheated (too many longs paying — crowded entry)
            if funding > FUNDING_OVERHEATED_PCT:
                log_event('INFO', 'scan_feed',
                          f"[perp] {symbol} skip — funding overheated "
                          f"({funding*100:.4f}%/8h > {FUNDING_OVERHEATED_PCT*100:.4f}%)")
                continue

            # Gate: skip long entries during liquidation cascade (longs being washed out)
            try:
                from data.liquidation_feed import get_liquidation_signal
                liq = get_liquidation_signal(symbol)
                if liq.get('liq_avoid_long') and liq.get('liq_signal') == 'cascade':
                    log_event('INFO', 'scan_feed',
                              f"[perp] {symbol} skip — liquidation cascade detected "
                              f"(long ratio={liq.get('liq_long_ratio', 0):.2f})")
                    continue
            except Exception:
                pass  # fail-open: liquidation data unavailable, proceed normally

            oi = bb.get_open_interest(symbol)
            prev_oi = run_perp_scan._prev_oi.get(symbol, oi)
            run_perp_scan._prev_oi[symbol] = oi

            sig = get_perp_signal(symbol, df, funding_rate=funding,
                                  open_interest=oi, open_interest_prev=prev_oi)

            log_signal('crypto_perp', symbol, sig.action, sig.confidence,
                       sig.reason, price=sig.price)

            if sig.action not in ('BUY', 'SELL'):
                log_event('INFO', 'scan_feed',
                          f"[perp] {symbol} HOLD | {sig.reason[:80]}")
                continue

            direction = sig.metadata.get('direction', 'LONG') if sig.metadata else 'LONG'
            log_event('INFO', 'scan_feed',
                      f"[perp] {symbol} → {direction} conf={sig.confidence:.0%} "
                      f"funding={funding*100:.4f}%/8h | {sig.reason[:80]}")

            risk_check = rm.pre_check_entry('crypto_perp', symbol, sig.action,
                                            sig.price, sig.confidence)
            if not risk_check:
                log_event('INFO', 'scan_feed', f"[perp] {symbol} ⛔ {risk_check.reason}")
                continue

            if direction == 'LONG':
                result = bb.open_long(symbol, PERP_POSITION_SIZE_USD,
                                      PERP_MAX_LEVERAGE, PERP_STOP_PCT,
                                      PERP_TAKE_PROFIT_PCT, 'crypto_perp')
            else:
                result = bb.open_short(symbol, PERP_POSITION_SIZE_USD,
                                       PERP_MAX_LEVERAGE, PERP_STOP_PCT,
                                       PERP_TAKE_PROFIT_PCT, 'crypto_perp')

            if result:
                # Use live mark price for accurate position accounting
                mark_price = bb.get_mark_price(symbol) or sig.price
                rm.register_position(
                    'crypto_perp', symbol,
                    PERP_POSITION_SIZE_USD / mark_price,
                    mark_price, sig.stop_loss, sig.take_profit,
                    direction=direction, entry_reason=sig.reason
                )

        except Exception as e:
            print(f"[perp_scan] {symbol}: {e}")
            log_event('ERROR', 'perp_scan', f"{symbol}: {e}")

    rm.ping()


def run_perp_time_watchdog() -> None:
    """Independent 4h time-exit watchdog — runs on its own schedule, never
    blocked by scanner loop failures.

    This is a safety net separate from _monitor_perp_exit inside run_perp_scan.
    If the main perp scanner crashes or stalls, this still closes stale positions
    on a 5-minute heartbeat.
    """
    if not PERP_ENABLED:
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return

    from execution.binance_broker import get_binance_broker
    bb = get_binance_broker()
    if not bb.is_connected():
        bb.connect()

    all_pos = rm.get_all_positions()
    perp_positions = all_pos.get('perp', {})
    if not perp_positions:
        return

    from datetime import datetime as _dt
    tz = pytz.timezone(MARKET_TIMEZONE)
    now = _dt.now(tz)

    for symbol, pos in list(perp_positions.items()):
        try:
            ts_entry = pos.get('ts_entry', '')
            try:
                entry_dt = _dt.fromisoformat(ts_entry)
                mins_in = int((now - (entry_dt if entry_dt.tzinfo
                               else entry_dt.replace(tzinfo=tz))).total_seconds() / 60)
            except Exception:
                mins_in = 0

            if mins_in >= 240:
                current_price = bb.get_mark_price(symbol)
                if not current_price:
                    log_event('WARNING', 'perp_watchdog',
                              f"[watchdog] {symbol}: {mins_in}m old — can't get price, skipping")
                    continue
                direction = pos.get('direction', 'LONG')
                pnl_pct = ((current_price - pos['entry']) / pos['entry']
                           if direction == 'LONG'
                           else (pos['entry'] - current_price) / pos['entry'])
                # Let winners run — skip 4h close if up > 0.5% and under 8h
                if pnl_pct > 0.005 and mins_in < 480:
                    log_event('INFO', 'perp_watchdog',
                              f"[watchdog] {symbol} {mins_in}m up {pnl_pct:+.2%} — "
                              f"skipping 4h rule, letting winner run (8h max)")
                    continue
                reason = (f"Perp watchdog exit: {mins_in}m, pnl={pnl_pct:+.2%} — "
                          f"{'8h max' if mins_in >= 480 else '4h flat/loss rule'}")
                log_event('WARNING', 'perp_watchdog',
                          f"[watchdog] {symbol} {mins_in}m — force-closing | {reason}")
                from scheduler.exit_monitor import _execute_perp_exit
                _execute_perp_exit(bb, rm, symbol, pos, reason, pos_fallback=pos)

        except Exception as e:
            log_event('ERROR', 'perp_watchdog', f"{symbol}: {e}")


def _monitor_perp_exit(bb, rm, symbol: str, pos: dict) -> None:
    """Check if an open perp position should be closed."""
    try:
        current_price = bb.get_mark_price(symbol)
        if not current_price:
            return

        rm.update_high('crypto_perp', symbol, current_price)
        should_exit, reason = rm.should_exit('crypto_perp', symbol, current_price)

        if not should_exit:
            from datetime import datetime as _dt
            ts_entry = pos.get('ts_entry', '')
            try:
                entry_dt = _dt.fromisoformat(ts_entry)
                tz = pytz.timezone(MARKET_TIMEZONE)
                mins_in = int((_dt.now(tz) - (entry_dt if entry_dt.tzinfo
                               else entry_dt.replace(tzinfo=tz))).total_seconds() / 60)
            except Exception:
                mins_in = 0
            pnl_pct = (current_price - pos['entry']) / pos['entry']
            if mins_in >= 240:
                # Let winners run — only force-close flat or losing positions at 4h.
                # If up > 0.5%, extend to 8h max so the position can reach its target.
                if pnl_pct > 0.005 and mins_in < 480:
                    log_event('INFO', 'scan_feed',
                              f"[perp] {symbol} 4h rule skipped — up {pnl_pct:+.2%}, "
                              f"letting winner run (8h max)")
                else:
                    should_exit = True
                    reason = (f"Perp time exit: {mins_in}m, pnl={pnl_pct:+.2%} — "
                              f"{'8h max' if mins_in >= 480 else '4h flat/loss rule'}")

        if should_exit:
            from scheduler.exit_monitor import _execute_perp_exit
            _execute_perp_exit(bb, rm, symbol, pos, reason, pos_fallback=pos)

    except Exception as e:
        log_event('ERROR', 'perp_exit', f"{symbol}: {e}")
