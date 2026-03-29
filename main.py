"""
main.py — Entry point for the Algo Trading System.
Usage:
  python main.py              # Full system (reads PAPER_TRADING from .env)
  python main.py --mode paper # Force paper trading
  python main.py --mode live  # Force live (requires typed confirmation)
  python main.py --crypto-only
  python main.py --equity-only
"""
import sys, os, argparse, time, traceback
from datetime import datetime
import pytz

# Pre-import stdlib modules and the core DB module at module level.
# Python 3.14 on macOS deadlocks (EDEADLK) when importing these lazily
# inside a function in a launchd daemon context. Pre-loading them here
# (in the "safe" startup context) puts them in sys.modules so the lazy
# imports inside main() become zero-cost cache hits.
import csv, uuid, sqlite3
from typing import Optional
import logging_db.trade_logger  # noqa: F401 — pre-warm, prevents EDEADLK

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║  👑  THE KING'S ALGO TRADING SYSTEM  v9.0  👑                   ║
║                                                                  ║
║  "Nothing is given. Everything is earned." — LeBron James       ║
║                                                                  ║
║  Crypto:  Coinbase/Binance | 4-signal engine | 3-agent debate   ║
║  Futures: Tradovate MES | Opening range pullback                ║
║  Perp:    Binance USD-M | Funding-aware entries                 ║
║  Exits:   Extended thinking AI review on every candle           ║
╚══════════════════════════════════════════════════════════════════╝
"""


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['paper', 'live'], default=None)
    p.add_argument('--equity-only', action='store_true')
    p.add_argument('--crypto-only', action='store_true')
    p.add_argument('--no-alerts', action='store_true')
    return p.parse_args()


def main():
    print(BANNER)
    args = parse_args()
    if args.mode:
        os.environ['PAPER_TRADING'] = 'true' if args.mode == 'paper' else 'false'

    from config import (PAPER_TRADING, ACCOUNT_SIZE, MARKET_TIMEZONE, ANTHROPIC_API_KEY,
                        MAX_RISK_PER_TRADE_PCT, MAX_DAILY_LOSS_PCT, MAX_DEPLOYED_PCT)
    tz = pytz.timezone(MARKET_TIMEZONE)
    mode = '📄 PAPER' if PAPER_TRADING else '💰 LIVE'

    # Sanity-check hardcoded risk values — catch accidental misconfiguration
    assert 0 < MAX_RISK_PER_TRADE_PCT <= 0.10, f"MAX_RISK_PER_TRADE_PCT={MAX_RISK_PER_TRADE_PCT} out of safe range (0–10%)"
    _daily_loss_cap = 1.00 if PAPER_TRADING else 0.15  # paper: no learning-halt cap
    assert 0 < MAX_DAILY_LOSS_PCT <= _daily_loss_cap, f"MAX_DAILY_LOSS_PCT={MAX_DAILY_LOSS_PCT} out of safe range"
    assert 0 < MAX_DEPLOYED_PCT      <= 1.00, f"MAX_DEPLOYED_PCT={MAX_DEPLOYED_PCT} out of safe range (0–100%)"

    print(f"  Mode:       {mode} TRADING")
    print(f"  Account:    ${ACCOUNT_SIZE}")
    print(f"  AI Debate:  {'✅ Enabled' if ANTHROPIC_API_KEY else '⚠️ No API key — using MACD fallback'}")
    print(f"  Time:       {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S ET')}\n")

    if not PAPER_TRADING:
        print("=" * 60)
        print("  ⚠️  LIVE TRADING — Real money will be deployed")
        print(f"  Account: ${ACCOUNT_SIZE}")
        print("=" * 60)
        resp = input("\n  Type 'I UNDERSTAND' to confirm: ").strip()
        if resp != 'I UNDERSTAND':
            print("Cancelled.")
            sys.exit(0)

    print("📦 Initializing database...")
    from logging_db.trade_logger import init_db, log_event
    init_db()
    print("   ✅ logs/trades.db ready\n")

    from memory.trade_memory import get_memory_stats
    mem = get_memory_stats()
    print(f"🧠 Trade memory: {mem.get('total', 0)} experiences | Win rate: {mem.get('win_rate', 0):.1%}\n")

    if not args.equity_only:
        print("🔌 Connecting Coinbase...")
        from execution.coinbase_broker import get_coinbase_broker
        cb = get_coinbase_broker()
        ok = cb.connect()
        print(f"   {'✅ Connected' if ok else '⚠️ Offline — signals log-only'}")
        if ok:
            print("📡 Starting Coinbase WebSocket...")
            from data.coinbase_feed import get_global_feed
            get_global_feed().start()
            time.sleep(2)
            print("   ✅ Real-time feed running\n")

    if not args.no_alerts:
        try:
            from alerts.telegram_alert import alert_system
            from config import FUTURES_ENABLED
            alert_system('STARTUP',
                f"👑 System online\nMode: {mode}\nAccount: ${ACCOUNT_SIZE}\n"
                f"AI: {'On' if ANTHROPIC_API_KEY else 'Fallback'} | "
                f"Futures: {'On' if FUTURES_ENABLED else 'Off'}")
        except Exception:
            pass

    if args.equity_only or args.crypto_only:
        import scheduler.job_runner as jr
        if args.equity_only:
            jr.run_crypto_scan = lambda: None
        if args.crypto_only:
            jr.run_equity_scan = lambda: None

    log_event('INFO', 'main', f"System started — {'paper' if PAPER_TRADING else 'live'}")

    print("=" * 60)
    print("  🚀 Scheduler starting. System is live.")
    print("  📊 Dashboard → streamlit run dashboard/app.py --server.runOnSave true → :8501")
    print("  📋 CSV logs  → logs/csv/")
    print("  🗄️  Database  → logs/trades.db")
    print("  ⌨️  Stop      → Ctrl+C")
    print("=" * 60 + "\n")

    from scheduler.job_runner import run_forever
    run_forever()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👑 Shutdown complete. We came, we worked.")
        sys.exit(0)
    except Exception as e:
        print(f"\n💥 Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
