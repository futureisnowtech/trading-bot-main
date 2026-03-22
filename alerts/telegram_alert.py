"""
alerts/telegram_alert.py
Sends trade notifications via email (Gmail SMTP).
All emails tagged [Trading Bot] in the subject for easy Gmail filtering.

Setup:
  1. Enable 2-Step Verification on your Google account
  2. myaccount.google.com → Security → App Passwords → create one → paste in .env as EMAIL_APP_PASSWORD
  3. In Gmail: Settings → Filters → "Subject contains [Trading Bot]" → Apply label "Trading Bot"
"""
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional
import pytz

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MARKET_TIMEZONE, PAPER_TRADING, EMAIL_TO, EMAIL_FROM, EMAIL_APP_PASSWORD


def _fmt_time() -> str:
    tz = pytz.timezone(MARKET_TIMEZONE)
    return datetime.now(tz).strftime('%m/%d %H:%M:%S ET')


def _mode_tag() -> str:
    return 'PAPER' if PAPER_TRADING else 'LIVE'


def _send_email(subject: str, body: str) -> None:
    """Fire-and-forget email in a background thread."""
    if not EMAIL_APP_PASSWORD or not EMAIL_TO:
        print(f"[ALERT — Email not configured] {subject}: {body}")
        return

    def _run():
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"[Trading Bot] {subject}"
            msg['From'] = f"Trading Bot <{EMAIL_FROM}>"
            msg['To'] = EMAIL_TO

            # Plain text version
            msg.attach(MIMEText(body, 'plain'))

            # HTML version (cleaner in Gmail)
            html_body = body.replace('\n', '<br>').replace('  ', '&nbsp;&nbsp;')
            html = f"""
<div style="font-family:monospace;font-size:14px;background:#1a1a1a;color:#f0f0f0;padding:16px;border-radius:8px;max-width:480px">
<div style="color:#FFD700;font-size:16px;font-weight:bold;margin-bottom:8px">[Trading Bot] {subject}</div>
<hr style="border-color:#333;margin:8px 0">
{html_body}
</div>"""
            msg.attach(MIMEText(html, 'html'))

            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
                server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        except Exception as e:
            print(f"[Email send failed] {e}")

    threading.Thread(target=_run, daemon=True).start()


# ─── Public alert functions ────────────────────────────────────────────────────

def alert_trade_opened(
    strategy: str,
    symbol: str,
    action: str,
    qty: float,
    price: float,
    stop_loss: float,
    take_profit: float
) -> None:
    direction = 'BUY' if action == 'BUY' else 'SELL'
    subject = f"{_mode_tag()} — {direction} {symbol}"
    body = (
        f"TRADE OPENED\n"
        f"--------------------\n"
        f"Strategy:  {strategy}\n"
        f"Symbol:    {symbol}\n"
        f"Action:    {action}\n"
        f"Qty:       {qty:.6f}\n"
        f"Price:     ${price:,.4f}\n"
        f"Stop:      ${stop_loss:,.4f}\n"
        f"Target:    ${take_profit:,.4f}\n"
        f"Time:      {_fmt_time()}"
    )
    _send_email(subject, body)


def alert_trade_closed(
    strategy: str,
    symbol: str,
    action: str,
    qty: float,
    entry_price: float,
    exit_price: float,
    pnl_usd: float,
    reason: str
) -> None:
    result = 'WIN' if pnl_usd >= 0 else 'LOSS'
    subject = f"{_mode_tag()} — CLOSED {symbol} {result} ${pnl_usd:+,.2f}"
    body = (
        f"TRADE CLOSED\n"
        f"--------------------\n"
        f"Strategy:  {strategy}\n"
        f"Symbol:    {symbol}\n"
        f"Exit:      {action}\n"
        f"Qty:       {qty:.6f}\n"
        f"Entry:     ${entry_price:,.4f}\n"
        f"Exit:      ${exit_price:,.4f}\n"
        f"P&L:       ${pnl_usd:+,.2f}\n"
        f"Reason:    {reason}\n"
        f"Time:      {_fmt_time()}"
    )
    _send_email(subject, body)


def alert_signal(
    strategy: str,
    symbol: str,
    signal: str,
    confidence: float,
    reason: str,
    price: float
) -> None:
    subject = f"{_mode_tag()} — SIGNAL {signal} {symbol}"
    body = (
        f"SIGNAL DETECTED\n"
        f"--------------------\n"
        f"Strategy:    {strategy}\n"
        f"Symbol:      {symbol}\n"
        f"Signal:      {signal}\n"
        f"Confidence:  {confidence:.0%}\n"
        f"Price:       ${price:,.4f}\n"
        f"Reason:      {reason}\n"
        f"Time:        {_fmt_time()}"
    )
    _send_email(subject, body)


def alert_risk_halt(reason: str) -> None:
    subject = f"RISK MANAGER — TRADING HALTED"
    body = (
        f"ALL TRADING STOPPED\n"
        f"--------------------\n"
        f"Reason:  {reason}\n"
        f"Time:    {_fmt_time()}\n\n"
        f"Manual review required."
    )
    _send_email(subject, body)


def alert_system(level: str, message: str) -> None:
    subject = f"[{level}] System Alert"
    body = (
        f"SYSTEM — {level}\n"
        f"--------------------\n"
        f"{message}\n"
        f"Time:  {_fmt_time()}"
    )
    _send_email(subject, body)


def alert_daily_summary(
    total_trades: int,
    winning: int,
    losing: int,
    total_pnl: float,
    total_fees: float,
    ending_balance: float
) -> None:
    result = 'PROFIT' if total_pnl >= 0 else 'LOSS'
    win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
    subject = f"{_mode_tag()} — Daily Summary {result} ${total_pnl:+,.2f}"
    body = (
        f"DAILY SUMMARY\n"
        f"--------------------\n"
        f"Trades:    {total_trades} ({winning}W / {losing}L)\n"
        f"Win Rate:  {win_rate:.1f}%\n"
        f"P&L:       ${total_pnl:+,.2f}\n"
        f"Fees:      ${total_fees:,.2f}\n"
        f"Balance:   ${ending_balance:,.2f}\n"
        f"Date:      {datetime.now().strftime('%Y-%m-%d')}"
    )
    _send_email(subject, body)
