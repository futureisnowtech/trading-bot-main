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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    PERP_ENABLED, LANE3_ENABLED,
    LANE3_SCAN_INTERVAL_SECONDS,
    ANTHROPIC_API_KEY,
    WATCHDOG_INTERVAL_SECONDS,
    MAX_STRATEGY_LOSS_STREAK,
)
from data.market_data import (
    is_market_open, is_in_no_trade_window,
    get_daily_bars,
)
from data.coinbase_feed import get_microstructure_feed
from risk.risk_manager import get_risk_manager
from logging_db.trade_logger import (
    log_event, get_today_stats, get_all_time_stats, get_todays_pnl,
)
from alerts.telegram_alert import alert_system, alert_daily_summary

# ── Import shared state from helpers ──────────────────────────────────────────
from scheduler._helpers import (
    _debate_available, _build_market_data, _get_microstructure,
    _crypto_strategy,
    _CONTEXT_AVAILABLE, run_session_analysis,
)

# ── Import sub-module scan functions ─────────────────────────────────────────
from scheduler.exit_monitor import (
    monitor_exits_with_ai, _execute_crypto_exit,
)
from scheduler.crypto_scanner import run_crypto_scan
from scheduler.perp_scanner import run_perp_scan, _monitor_perp_exit, run_perp_time_watchdog
from scheduler.mes_scanner import (
    run_mes_scan, run_mes_premarket, run_mes_opening_range,
)
try:
    from scheduler.derivatives_momentum_scanner import run_derivatives_momentum_scan
    _DERIV_SCANNER_AVAILABLE = True
except Exception as _dse:
    run_derivatives_momentum_scan = None
    _DERIV_SCANNER_AVAILABLE = False
    print(f"[scheduler] Derivatives momentum scanner unavailable: {_dse}")

if LANE3_ENABLED:
    try:
        from scheduler.lane3_scanner import run_prediction_market_scan
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"[lane3] Import failed: {_e}")
        run_prediction_market_scan = None
else:
    run_prediction_market_scan = None


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
    run_mes_premarket()
    try:
        df_spy = get_daily_bars('SPY', '3mo')
        if df_spy is not None:
            from data.indicators import get_htf_bias, add_all_indicators
            df_ind = add_all_indicators(df_spy.copy())
            bias = get_htf_bias(df_ind)
            msg = f"Pre-market: SPY HTF={bias['bias']} ADX={bias['strength']:.2f}"
            print(f"  {msg}")
            alert_system('INFO', msg)
    except Exception as e:
        print(f"[premarket] {e}")


def run_opening_range() -> None:
    run_mes_opening_range()


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


# ─── PARALLEL LANE SCAN ──────────────────────────────────────────────────────

def run_parallel_scan() -> None:
    """Run crypto, perp, and Lane 3 scans in parallel threads.

    Each lane is fully independent: they share no write state except via
    risk_manager (SQLite WAL — safe for concurrent reads and serialised writes).
    A 60-second Claude API call for BTC no longer blocks perp or Lane 3.
    """
    tz = pytz.timezone(MARKET_TIMEZONE)
    print(f"[parallel] Starting lane scan at {datetime.now(tz).strftime('%H:%M:%S')} ET")
    log_event('INFO', 'heartbeat', f"scan cycle {datetime.now(tz).strftime('%H:%M:%S')}")

    lane_tasks: dict = {}
    # Lane 4 (derivatives momentum) runs on every cycle when perp is enabled
    _run_deriv = _DERIV_SCANNER_AVAILABLE and PERP_ENABLED and run_derivatives_momentum_scan is not None
    _max_workers = 3 + (1 if _run_deriv else 0) + (1 if LANE3_ENABLED and run_prediction_market_scan else 0)
    with ThreadPoolExecutor(max_workers=max(4, _max_workers), thread_name_prefix='lane') as executor:
        lane_tasks['crypto'] = executor.submit(run_crypto_scan)
        if PERP_ENABLED:
            lane_tasks['perp'] = executor.submit(run_perp_scan)
        if _run_deriv:
            lane_tasks['deriv'] = executor.submit(run_derivatives_momentum_scan)
        if LANE3_ENABLED and run_prediction_market_scan is not None:
            lane_tasks['lane3'] = executor.submit(run_prediction_market_scan)

        for name, future in lane_tasks.items():
            try:
                future.result(timeout=300)  # 5-min hard cap per lane
            except Exception as e:
                _tb_str = traceback.format_exc()
                log_event('ERROR', 'scheduler',
                          f"[parallel] Lane '{name}' raised unhandled error: {e}\n{_tb_str[:1000]}")
                traceback.print_exc()

    # Run health check after every scan cycle (rate-limited internally to 1/min)
    try:
        from monitoring.health_check import run_health_check
        run_health_check()
    except Exception as _hce:
        pass  # health check must never crash the scan loop


# ─── SCHEDULER SETUP & LOOP ──────────────────────────────────────────────────

def setup_schedules() -> None:
    days = [schedule.every().monday, schedule.every().tuesday, schedule.every().wednesday,
            schedule.every().thursday, schedule.every().friday]
    for d in days:
        d.at('08:30').do(run_premarket)
        d.at('09:35').do(run_opening_range)
        d.at('16:15').do(run_daily_close)

    # Crypto + Perp + Lane 3 all run together in parallel threads.
    # A slow Claude call for one symbol can no longer block the other lanes.
    schedule.every(CRYPTO_SCAN_INTERVAL_SECONDS).seconds.do(run_parallel_scan)
    schedule.every(WATCHDOG_INTERVAL_SECONDS).seconds.do(run_watchdog)

    # Independent perp time-exit watchdog — runs every 5 min on its own schedule.
    # Closes any perp position that has been open >= 4h even if the scanner loop
    # crashes, hangs, or throws an unhandled exception.
    if PERP_ENABLED:
        schedule.every(300).seconds.do(run_perp_time_watchdog)

    if FUTURES_ENABLED:
        schedule.every(FUTURES_SCAN_INTERVAL_SECONDS).seconds.do(run_mes_scan)

    # Session-open analysis triggers (24/7 — crypto never closes)
    schedule.every().day.at('20:00').do(lambda: run_session_open_analysis('ASIA'))
    schedule.every().day.at('03:00').do(lambda: run_session_open_analysis('LONDON'))
    schedule.every().day.at('08:30').do(lambda: run_session_open_analysis('NY_OPEN'))

    lane3_status = f"Lane3: {LANE3_SCAN_INTERVAL_SECONDS}s" if LANE3_ENABLED else "Lane3: OFF"
    deriv_status = "DerivMomentum: ON" if _DERIV_SCANNER_AVAILABLE and PERP_ENABLED else "DerivMomentum: OFF"
    print(f"[scheduler] Parallel scan: {CRYPTO_SCAN_INTERVAL_SECONDS}s "
          f"(Crypto + {'Perp' if PERP_ENABLED else 'no-Perp'} + {deriv_status} + {lane3_status}) | "
          f"Watchdog: {WATCHDOG_INTERVAL_SECONDS}s | "
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
