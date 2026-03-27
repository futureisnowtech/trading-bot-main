"""
scheduler/job_runner.py — The engine. Runs forever.

Full pipeline (v5.0 — equity removed):
  Crypto (5min, 24/7):         4-signal engine → ML gate → debate → execute
  Exits (every candle close):  extended_thinking exit review on ALL open positions
  Futures (60s, market hours): opening range breakout → debate confirmation → execute
  Perp (5min, 24/7):           Binance perp entry/exit
  Watchdog (15min):            alert if no scan completed

Sub-modules:
  scheduler/_helpers.py        — shared state, helper functions, strategy instances
  scheduler/exit_monitor.py    — AI-driven exit management
  scheduler/crypto_scanner.py  — crypto 4-signal engine → ML gate → debate → execute
  scheduler/perp_scanner.py    — perp entry/exit via Binance
"""
import time
import traceback
from datetime import datetime
import pytz
import schedule
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    CRYPTO_PAIRS, PAPER_TRADING, ACCOUNT_SIZE,
    CRYPTO_SCAN_INTERVAL_SECONDS,
    FUTURES_SCAN_INTERVAL_SECONDS, MARKET_TIMEZONE,
    CRYPTO_ENABLED, FUTURES_ENABLED,
    PERP_ENABLED,
    ANTHROPIC_API_KEY,
    WATCHDOG_INTERVAL_SECONDS,
    MAX_STRATEGY_LOSS_STREAK,
)
from data.market_data import (
    is_market_open, is_in_no_trade_window, get_bars,
    get_daily_bars,
)
from data.coinbase_feed import get_microstructure_feed
from data.indicators import add_all_indicators
from risk.risk_manager import get_risk_manager
from logging_db.trade_logger import (
    log_event, get_today_stats, get_all_time_stats, get_todays_pnl,
)
from alerts.telegram_alert import alert_system, alert_daily_summary

# ── Import shared state from helpers ──────────────────────────────────────────
from scheduler._helpers import (
    _debate_available, _build_market_data, _get_microstructure,
    _futures_strategy, _crypto_strategy,
    _CONTEXT_AVAILABLE, run_session_analysis,
)

# ── Import sub-module scan functions ─────────────────────────────────────────
from scheduler.exit_monitor import (
    monitor_exits_with_ai, _execute_crypto_exit,
)
from scheduler.crypto_scanner import run_crypto_scan
from scheduler.perp_scanner import run_perp_scan, _monitor_perp_exit


# ─── FUTURES SCAN ────────────────────────────────────────────────────────────

def run_futures_scan() -> None:
    if not FUTURES_ENABLED or not is_market_open() or is_in_no_trade_window():
        return
    rm = get_risk_manager()
    if rm.is_halted:
        return
    try:
        from data.market_data import get_cot_sentiment
        cot = get_cot_sentiment()
        if not cot['is_bullish']:
            log_event('INFO', 'scan_feed',
                      f"[futures] COT: commercials net {cot['commercial_net']:+,} — bearish bias, skipping longs")

        from execution.tradovate_broker import get_tradovate_broker
        tb = get_tradovate_broker()
        sig = _futures_strategy.generate_signal('MES')
        if sig.action == 'BUY' and not cot['is_bullish']:
            log_event('INFO', 'scan_feed', f"[futures] COT bearish advisory — proceeding with BUY (signal confidence required)")
        if sig.action == 'BUY':
            log_event('INFO', 'scan_feed',
                      f"[futures] MES → BUY {sig.confidence:.0%} | {sig.reason[:80]}")
            engine = _debate_available()
            if engine and sig.confidence < 0.75:
                df = get_bars('ES=F', '5m', '2d')
                if df is not None:
                    df_ind = add_all_indicators(df)
                    md = _build_market_data('MES', sig.price, df_ind)
                    md['dollar_volume'] = 1_000_000_000
                    debate = engine['quick']('MES', md, verbose=False)
                    if debate.synthesized_signal != 'BUY':
                        print(f"[futures] Debate override → HOLD")
                        return
            tb.buy_mes(
                num_contracts=_futures_strategy.NUM_CONTRACTS,
                stop_loss_pts=_futures_strategy.STOP_LOSS_PTS,
                take_profit_pts=_futures_strategy.TAKE_PROFIT_PTS,
                strategy='futures_scalper',
            )
    except Exception as e:
        print(f"[futures_scan] {e}")
        log_event('ERROR', 'futures_scan', str(e))


# ─── WATCHDOG ────────────────────────────────────────────────────────────────

def run_watchdog() -> None:
    rm = get_risk_manager()
    if not rm.watchdog_ok(WATCHDOG_INTERVAL_SECONDS):
        msg = f"⚠️ WATCHDOG: No scan completed in {WATCHDOG_INTERVAL_SECONDS//60} minutes. Bot may be stuck."
        print(msg)
        log_event('WARNING', 'watchdog', msg)
        try:
            alert_system('WARNING', msg)
        except Exception:
            pass


# ─── PRE-MARKET ──────────────────────────────────────────────────────────────

def run_premarket() -> None:
    print("\n📊 Pre-market analysis...")
    _futures_strategy.update_htf_bias()
    _futures_strategy.reset_daily()
    try:
        from data.indicators import get_htf_bias
        df_spy = get_daily_bars('SPY', '3mo')
        if df_spy is not None:
            bias = get_htf_bias(df_spy)
            msg = f"Pre-market: SPY HTF={bias['bias']} ADX={bias['strength']:.2f}"
            print(f"  {msg}")
            alert_system('INFO', msg)
    except Exception as e:
        print(f"[premarket] {e}")


def run_opening_range() -> None:
    try:
        df = get_bars('ES=F', '5m', '1d')
        if df is not None and len(df) >= 1:
            last = df.iloc[-1]
            _futures_strategy.set_opening_range(float(last['high']), float(last['low']))
    except Exception as e:
        print(f"[opening_range] {e}")


def run_daily_close() -> None:
    ts = get_today_stats(paper=PAPER_TRADING)
    pnl = ts['gross_pnl']
    fees = ts['fees']
    all_stats = get_all_time_stats(paper=PAPER_TRADING)
    real_balance = ACCOUNT_SIZE + all_stats['total_pnl']
    alert_daily_summary(ts['total'], ts['wins'], ts['losses'], pnl, fees, real_balance)
    log_event('INFO', 'daily_close', f"P&L=${pnl:+.2f} | {ts['total']} closed trades")
    rm = get_risk_manager()
    if rm.is_halted and 'Daily loss' in rm.halt_reason:
        rm.resume()


# ─── SESSION ANALYSIS ────────────────────────────────────────────────────────

def run_session_open_analysis(session_name: str) -> None:
    """Fire the AI Session Analyst at each session open."""
    if not _CONTEXT_AVAILABLE:
        return
    try:
        print(f"\n[session_analyst] {session_name} open — running session analysis...")
        ctx = run_session_analysis(session_name=session_name, force=True)
        bias = ctx.get('session_bias', 'NEUTRAL')
        mult = ctx.get('conviction_threshold_multiplier', 1.0)
        notes = ctx.get('session_notes', '')[:100]
        msg = f"[{session_name}] bias={bias} | cv_threshold×{mult:.2f} | {notes}"
        print(f"  {msg}")
        log_event('INFO', 'session_analyst', msg)
    except Exception as e:
        print(f"[session_analyst] {session_name} analysis error: {e}")


# ─── SCHEDULER SETUP & LOOP ──────────────────────────────────────────────────

def setup_schedules() -> None:
    days = [schedule.every().monday, schedule.every().tuesday, schedule.every().wednesday,
            schedule.every().thursday, schedule.every().friday]
    for d in days:
        d.at('08:30').do(run_premarket)
        d.at('09:35').do(run_opening_range)
        d.at('16:15').do(run_daily_close)

    schedule.every(CRYPTO_SCAN_INTERVAL_SECONDS).seconds.do(run_crypto_scan)
    schedule.every(WATCHDOG_INTERVAL_SECONDS).seconds.do(run_watchdog)

    if FUTURES_ENABLED:
        schedule.every(FUTURES_SCAN_INTERVAL_SECONDS).seconds.do(run_futures_scan)

    if PERP_ENABLED:
        schedule.every(CRYPTO_SCAN_INTERVAL_SECONDS).seconds.do(run_perp_scan)

    # Session-open analysis triggers (24/7 — crypto never closes)
    schedule.every().day.at('20:00').do(lambda: run_session_open_analysis('ASIA'))
    schedule.every().day.at('03:00').do(lambda: run_session_open_analysis('LONDON'))
    schedule.every().day.at('08:30').do(lambda: run_session_open_analysis('NY_OPEN'))

    print(f"[scheduler] Crypto: {CRYPTO_SCAN_INTERVAL_SECONDS}s | "
          f"Perp: {'ON' if PERP_ENABLED else 'OFF'} | Watchdog: {WATCHDOG_INTERVAL_SECONDS}s | "
          f"Session Analysis: ASIA 8pm / LONDON 3am / NY 8:30am ET")


def run_forever() -> None:
    # Start microstructure WebSocket feed for all crypto pairs
    if CRYPTO_ENABLED:
        try:
            ms_feed = get_microstructure_feed(CRYPTO_PAIRS)
            log_event('INFO', 'scheduler', f"[microstructure] WebSocket feed started for {len(CRYPTO_PAIRS)} pairs")
        except Exception as e:
            log_event('WARNING', 'scheduler', f"[microstructure] Feed startup failed: {e} — OBI/TFI will be None")

    setup_schedules()
    try:
        from dashboard.terminal import render as render_terminal
        _terminal_ok = True
    except Exception:
        _terminal_ok = False

    _last_render = 0.0
    RENDER_INTERVAL = 5

    print("\n🚀 System online. Press Ctrl+C to stop.\n")
    while True:
        try:
            schedule.run_pending()
            if _terminal_ok and time.time() - _last_render >= RENDER_INTERVAL:
                try:
                    render_terminal()
                except Exception:
                    pass
                _last_render = time.time()
            time.sleep(1)
        except KeyboardInterrupt:
            print("\n[scheduler] Shutting down.")
            break
        except Exception as e:
            print(f"[scheduler] Error: {e}")
            traceback.print_exc()
            log_event('ERROR', 'scheduler', str(e))
            time.sleep(10)


if __name__ == '__main__':
    pass
