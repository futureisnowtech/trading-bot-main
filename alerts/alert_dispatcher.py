"""
alerts/alert_dispatcher.py — Multi-channel alert dispatcher (Sprint 2).

Channels (in priority order):
  1. Telegram Bot API (if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID set)
  2. SQLite system_events table (always — dashboard displays this)

Public API is identical to telegram_alert.py — nothing else changes.
Telegram is optional: if token/chat_id not set, falls back to SQLite-only.

Setup:
  1. Create a Telegram bot: https://t.me/BotFather → /newbot
  2. Add to .env: TELEGRAM_BOT_TOKEN=<token> TELEGRAM_CHAT_ID=<your-chat-id>
  3. Start the bot chat (send /start once) so it can message you
  4. Replace all imports of telegram_alert with alert_dispatcher (or alias)

To find your chat_id: message @userinfobot on Telegram.

Adapted from Fully-Autonomous-Polymarket-AI-Trading-Bot/src/observability/alerts.py
(sync requests; our SQLite pattern; identical public API to telegram_alert.py).
"""
import os
import sys
from datetime import datetime
from typing import Optional

import pytz
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MARKET_TIMEZONE, PAPER_TRADING, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_TELEGRAM_API = "https://api.telegram.org"
_TELEGRAM_TIMEOUT = 5.0    # don't let Telegram slow down the scan loop


def _fmt_time() -> str:
    return datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%m/%d %H:%M:%S ET')


def _mode_tag() -> str:
    return 'PAPER' if PAPER_TRADING else 'LIVE'


def _notify(level: str, subject: str, body: str) -> None:
    """Write to SQLite (always) + Telegram (if configured)."""
    # 1. SQLite — always available, drives dashboard
    try:
        from logging_db.trade_logger import log_event
        log_event(level, 'notify', f"{subject} | {body}")
    except Exception as e:
        print(f"[NOTIFY] {subject}: {body}  (DB write failed: {e})")

    # 2. Telegram — best-effort, never raises
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            _send_telegram(f"*{subject}*\n{body}")
        except Exception:
            pass   # Telegram failure must never affect the trading loop


def _send_telegram(text: str) -> bool:
    """POST a message to Telegram Bot API. Returns True on success."""
    try:
        url = f"{_TELEGRAM_API}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=_TELEGRAM_TIMEOUT,
        )
        return resp.status_code == 200
    except Exception:
        return False


# ─── Public alert functions (identical API to telegram_alert.py) ──────────────

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


# ── Lane 3 specific alert ─────────────────────────────────────────────────────

def alert_prediction_resolved(
    platform: str,
    question: str,
    side: str,
    won: bool,
    pnl_usd: float,
    calibration_score: Optional[float] = None,
) -> None:
    """Alert when a prediction market resolves."""
    result = "WON" if won else "LOST"
    level = "INFO" if won else "WARNING"
    subject = f"{_mode_tag()} — PREDICTION {result} ${pnl_usd:+.2f}"
    body = (
        f"Platform: {platform.upper()} | "
        f"Side: {side} | "
        f"Q: {question[:80]} | "
        f"P&L: ${pnl_usd:+.2f}"
    )
    if calibration_score is not None:
        body += f" | Cal: {calibration_score:.3f}"
    body += f" | {_fmt_time()}"
    _notify(level, subject, body)
