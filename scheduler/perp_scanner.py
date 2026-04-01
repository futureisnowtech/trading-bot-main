"""
scheduler/perp_scanner.py — Binance USD-M perp futures: full-market momentum scanner.

Every scan cycle:
  1. Pulls ALL ~300 Binance futures tickers in one API call.
  2. Filters by liquidity ($5M+ 24h volume) and ranks by momentum × volume.
  3. Fetches 5-min klines for top 25 movers to confirm momentum not exhausted.
  4. Enters LONG or SHORT based on recent directional momentum + volume spike.
  5. Server-side SL/TP set on Binance — no AI exit review (that was running on
     fake data and killing every trade after 5 minutes before it reached target).

Exit logic: mechanical only.
  - Hard stop/target: checked every scan cycle via rm.should_exit()
  - Time rule: 4h flat/losing → close; 4-8h if up >0.5% (let winner run)
  - Independent 5-min watchdog: run_perp_time_watchdog() as safety net
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
    PERP_MAX_POSITIONS,
)
from risk.risk_manager import get_risk_manager
from logging_db.trade_logger import log_event, log_signal

# Minimum 24h USDT volume to consider a pair liquid enough to trade
_MIN_QUOTE_VOLUME = 5_000_000
# Minimum volume spike on recent candles to confirm momentum has real interest behind it
_MIN_VOL_RATIO = 1.3
# Minimum recent price move (last 3 × 5-min bars) to confirm still in motion
_MIN_RECENT_MOVE_PCT = 0.15


def run_perp_scan() -> None:
    """
    Full-market momentum scanner.
    Scans ALL Binance perpetual futures (not just a fixed list) for the strongest
    movers with volume confirmation. Goes both LONG and SHORT.
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

    # ── Monitor existing positions first ──────────────────────────────────────
    all_pos    = rm.get_all_positions()
    perp_pos   = all_pos.get('perp', {})
    for symbol, pos in list(perp_pos.items()):
        _monitor_perp_exit(bb, rm, symbol, pos)

    # Refresh after any exits
    perp_pos  = rm.get_all_positions().get('perp', {})
    slots_open = PERP_MAX_POSITIONS - len(perp_pos)
    if slots_open <= 0:
        rm.ping()
        return

    # ── FULL MARKET SCAN: one API call → all ~300 USDT perp pairs ────────────
    all_tickers = bb.get_all_tickers()
    if not all_tickers:
        # Fallback: use configured PERP_PAIRS if public API fails
        log_event('WARNING', 'perp_scan', '[perp] get_all_tickers empty — falling back to PERP_PAIRS')
        all_tickers = [
            {'symbol': p.strip(), 'price_change_pct': 0.0, 'last_price': 0.0,
             'quote_volume': _MIN_QUOTE_VOLUME, 'count': 0}
            for p in (PERP_PAIRS or []) if p.strip()
        ]

    # Filter: liquid USDT pairs not already in a position
    candidates = [
        t for t in all_tickers
        if t['symbol'].endswith('USDT')
        and float(t.get('quote_volume', 0) or 0) >= _MIN_QUOTE_VOLUME
        and t['symbol'] not in perp_pos
    ]

    # Score: abs(24h % change) × volume weight — strongest movers with most interest
    for t in candidates:
        abs_move   = abs(float(t.get('price_change_pct', 0) or 0))
        vol_weight = min(float(t.get('count', 0) or 0) / 50_000, 3.0)
        t['_score'] = abs_move * (1.0 + vol_weight)

    # Take top 25 candidates for deeper candle inspection
    candidates = sorted(candidates, key=lambda x: x['_score'], reverse=True)[:25]

    if not candidates:
        rm.ping()
        return

    log_event('INFO', 'perp_scan',
              f"[perp] Full-market scan: {len(all_tickers)} pairs → {len(candidates)} candidates")

    entries_made = 0
    for ticker in candidates:
        if entries_made >= slots_open:
            break

        symbol        = ticker['symbol']
        current_price = float(ticker.get('last_price', 0) or 0)
        change_24h    = float(ticker.get('price_change_pct', 0) or 0)

        if current_price <= 0:
            continue

        try:
            # Fetch 5-min candles to confirm momentum isn't already exhausted
            df = bb.get_klines(symbol, interval='5m', limit=20)
            if df is None or len(df) < 10:
                continue

            closes  = df['close'].astype(float)
            volumes = df['volume'].astype(float)

            # Recent move: last 3 bars (15 min of momentum)
            recent_change = (closes.iloc[-1] - closes.iloc[-4]) / closes.iloc[-4] * 100

            # Volume spike: last bar vs rolling mean of previous bars
            avg_vol   = volumes.iloc[:-1].mean()
            vol_ratio = float(volumes.iloc[-1]) / avg_vol if avg_vol > 0 else 0.0

            # Gate: need real volume behind the move AND still moving
            if vol_ratio < _MIN_VOL_RATIO:
                continue
            if abs(recent_change) < _MIN_RECENT_MOVE_PCT:
                continue

            # Funding rate
            funding = bb.get_funding_rate(symbol)

            # Liquidation cascade gate (longs only)
            try:
                from data.liquidation_feed import get_liquidation_signal
                liq = get_liquidation_signal(symbol)
                if liq.get('liq_avoid_long') and liq.get('liq_signal') == 'cascade' and recent_change > 0:
                    continue
            except Exception:
                pass

            # Direction from RECENT momentum (3-bar), not 24h
            if recent_change > 0 and funding <= FUNDING_OVERHEATED_PCT:
                direction = 'LONG'
                action    = 'BUY'
            elif recent_change < 0:
                direction = 'SHORT'
                action    = 'SELL'
            else:
                continue

            # Risk check
            risk_check = rm.pre_check_entry('crypto_perp', symbol, action,
                                            current_price, 0.65)
            if not risk_check:
                log_event('INFO', 'perp_scan',
                          f"[perp] {symbol} ⛔ risk block: {risk_check.reason}")
                continue

            reason = (f"Momentum scan: {change_24h:+.2f}% 24h | "
                      f"recent={recent_change:+.2f}% 15m | "
                      f"vol={vol_ratio:.1f}x | funding={funding*100:.4f}%/8h")

            log_event('INFO', 'perp_scan',
                      f"[perp] {symbol} → {direction} | {reason[:100]}")
            log_signal('crypto_perp', symbol, action, 0.65, reason, price=current_price)

            if direction == 'LONG':
                result = bb.open_long(symbol, PERP_POSITION_SIZE_USD, PERP_MAX_LEVERAGE,
                                      PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT, 'crypto_perp')
            else:
                result = bb.open_short(symbol, PERP_POSITION_SIZE_USD, PERP_MAX_LEVERAGE,
                                       PERP_STOP_PCT, PERP_TAKE_PROFIT_PCT, 'crypto_perp')

            if result:
                mark_price = bb.get_mark_price(symbol) or current_price
                sl = mark_price * (1 - PERP_STOP_PCT) if direction == 'LONG' else mark_price * (1 + PERP_STOP_PCT)
                tp = mark_price * (1 + PERP_TAKE_PROFIT_PCT) if direction == 'LONG' else mark_price * (1 - PERP_TAKE_PROFIT_PCT)
                registered = rm.register_position(
                    'crypto_perp', symbol,
                    PERP_POSITION_SIZE_USD / mark_price,
                    mark_price, sl, tp,
                    direction=direction,
                    entry_reason=reason,
                    signal_type='momentum',
                    active_signals=[
                        'full_market_scan',
                        f'vol_{vol_ratio:.1f}x',
                        f'chg_{recent_change:+.1f}pct_15m',
                        direction.lower(),
                    ],
                )
                if registered:
                    entries_made += 1
                    print(f"[perp_scan] ✅ {direction} {symbol} @ ${mark_price:.4f} | {reason[:80]}")
                else:
                    print(f"[perp_scan] ⚠️  {symbol} — position already registered (parallel scanner beat us), skip")

        except Exception as e:
            log_event('ERROR', 'perp_scan', f"[perp] {symbol}: {e}")

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
