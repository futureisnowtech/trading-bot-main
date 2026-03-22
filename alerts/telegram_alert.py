"""
alerts/telegram_alert.py
All notifications are written to the SQLite system_events table (source='notify').
The dashboard Notifications panel reads and displays them in real time.
File keeps its original name and public API so nothing else in the codebase changes.
"""
import os
import sys
from datetime import datetime
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MARKET_TIMEZONE, PAPER_TRADING


def _fmt_time() -> str:
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%m/%d %H:%M:%S ET')


def _mode_tag() -> str:
    return 'PAPER' if PAPER_TRADING else 'LIVE'


def _notify(level: str, subject: str, body: str) -> None:
    """Persist a notification to system_events so the dashboard can display it."""
    try:
        from logging_db.trade_logger import log_event
        log_event(level, 'notify', f"{subject} | {body}")
    except Exception as e:
        # Last resort: at least print it so nothing is silently lost
        print(f"[NOTIFY] {subject}: {body}  (DB write failed: {e})")


# ─── Public alert functions ────────────────────────────────────────────────────

def alert_trade_opened(
    strategy: str, symbol: str, action: str,
    qty: float, price: float, stop_loss: float, take_profit: float
) -> None:
    direction = 'BUY' if action == 'BUY' else 'SELL'
    subject = f"{_mode_tag()} — {direction} {symbol}"
    body = (
        f"Strategy: {strategy} | "
        f"Qty: {qty:.6f} @ ${price:,.4f} | "
        f"Stop: ${stop_loss:,.4f} | "
        f"Target: ${take_profit:,.4f} | "
        f"{_fmt_time()}"
    )
    _notify('INFO', subject, body)


def alert_trade_closed(
    strategy: str, symbol: str, action: str, qty: float,
    entry_price: float, exit_price: float, pnl_usd: float, reason: str
) -> None:
    result = 'WIN' if pnl_usd >= 0 else 'LOSS'
    level = 'INFO' if pnl_usd >= 0 else 'WARNING'
    subject = f"{_mode_tag()} — CLOSED {symbol} {result} ${pnl_usd:+,.2f}"
    body = (
        f"Strategy: {strategy} | "
        f"Entry: ${entry_price:,.4f} → Exit: ${exit_price:,.4f} | "
        f"Reason: {reason} | "
        f"{_fmt_time()}"
    )
    _notify(level, subject, body)


def alert_signal(
    strategy: str, symbol: str, signal: str,
    confidence: float, reason: str, price: float
) -> None:
    subject = f"{_mode_tag()} — SIGNAL {signal} {symbol}"
    body = (
        f"Strategy: {strategy} | "
        f"Conf: {confidence:.0%} @ ${price:,.4f} | "
        f"{reason[:120]} | "
        f"{_fmt_time()}"
    )
    _notify('INFO', subject, body)


def alert_risk_halt(reason: str) -> None:
    subject = "RISK MANAGER — TRADING HALTED"
    body = f"Reason: {reason} | {_fmt_time()} | Manual review required."
    _notify('ERROR', subject, body)


def alert_system(level: str, message: str) -> None:
    subject = f"[{level}] System Alert"
    body = f"{message} | {_fmt_time()}"
    db_level = 'ERROR' if level in ('ERROR', 'HALT') else 'WARNING' if level == 'WARN' else 'INFO'
    _notify(db_level, subject, body)


def alert_daily_summary(
    total_trades: int, winning: int, losing: int,
    total_pnl: float, total_fees: float, ending_balance: float
) -> None:
    result = 'PROFIT' if total_pnl >= 0 else 'LOSS'
    win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
    subject = f"{_mode_tag()} — Daily Summary {result} ${total_pnl:+,.2f}"
    body = (
        f"Trades: {total_trades} ({winning}W / {losing}L) | "
        f"Win Rate: {win_rate:.1f}% | "
        f"Fees: ${total_fees:.4f} | "
        f"Balance: ${ending_balance:,.2f}"
    )
    _notify('INFO', subject, body)
