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
    MARKET_TIMEZONE,
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
    from data.coinbase_feed import get_candles

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

            base = symbol.replace('USDT', '').replace('USDC', '')
            cb_symbol = f"{base}-USDC"
            df = None
            try:
                df = get_candles(cb_symbol, 'ONE_MINUTE', 60)
            except Exception:
                pass
            if df is None or len(df) < 25:
                try:
                    import yfinance as yf
                    hist = yf.Ticker(f'{base}-USD').history(period='2d', interval='1m')
                    if hist is not None and not hist.empty:
                        hist.columns = [c.lower() for c in hist.columns]
                        df = hist.reset_index(drop=True)
                except Exception:
                    pass
            if df is None or len(df) < 25:
                continue

            funding = bb.get_funding_rate(symbol)
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
                rm.register_position(
                    'crypto_perp', symbol,
                    PERP_POSITION_SIZE_USD / sig.price,
                    sig.price, sig.stop_loss, sig.take_profit,
                    direction=direction, entry_reason=sig.reason
                )

        except Exception as e:
            print(f"[perp_scan] {symbol}: {e}")
            log_event('ERROR', 'perp_scan', f"{symbol}: {e}")

    rm.ping()


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
            pnl_pct = abs(current_price - pos['entry']) / pos['entry']
            if mins_in >= 240 and pnl_pct < 0.005:
                should_exit = True
                reason = f"Perp time exit: {mins_in}m, flat ({pnl_pct:.2%}) — funding cost drain"

        if should_exit:
            result = bb.close_position(symbol, strategy='crypto_perp', reason=reason)
            if result is not None:
                rm.close_position('crypto_perp', symbol)
                log_event('INFO', 'perp_exit', f"[perp] CLOSED {symbol} | {reason}")

    except Exception as e:
        log_event('ERROR', 'perp_exit', f"{symbol}: {e}")
