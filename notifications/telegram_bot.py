import logging
import asyncio
import os
import psutil
import time
import subprocess
from html import escape
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
from telegram.constants import ParseMode

import system_state
from spot_engine import get_spot_positions, _get_broker
from notifications.ai_agent import ask_ai

logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AUTHORIZED_USER_ID = int(os.environ.get("TELEGRAM_AUTHORIZED_USER_ID", "8224826883"))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _effective_message(update: Update):
    return getattr(update, "effective_message", None)


async def _reply_text(update: Update, text: str, **kwargs):
    message = _effective_message(update)
    if message is None:
        logger.warning("Telegram reply target missing for update type %s", type(update))
        return None
    return await message.reply_text(text, **kwargs)


def _runtime_is_live() -> bool:
    """
    Return True only when the system is confirmed to be running in live mode.

    Primary source: system_runtime_state DB table (written by main.py on startup
    and updated by go_live.py / go_paper.py transitions).
    Fallback: system_state.state in-process mode field.
    Fallback: config.PAPER_TRADING (config-file truth, least authoritative).

    Returns False (paper) in all ambiguous cases — fail-safe.
    """
    # 1. Try runtime DB (canonical)
    try:
        import sqlite3

        _db = os.path.join(REPO_ROOT, "logs", "trades.db")
        with sqlite3.connect(_db, timeout=2) as c:
            row = c.execute(
                "SELECT process_mode FROM system_runtime_state ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row and row[0] == "live":
                return True
            if row and row[0] == "paper":
                return False
    except Exception:
        pass
    # 2. In-process system_state (set by main.py at launch)
    try:
        mode = system_state.state.get_state().get("mode", "PAPER")
        if mode == "LIVE":
            return True
        if mode == "PAPER":
            return False
    except Exception:
        pass
    # 3. Config fallback (least authoritative — may be stale)
    try:
        from config import PAPER_TRADING

        return not PAPER_TRADING
    except Exception:
        pass
    return False  # fail-safe: assume paper


def _live_actions_allowed() -> bool:
    """
    Destructive Telegram actions (cancel_all, etc.) require:
      - runtime mode IS live (from DB truth)
      - TELEGRAM_ALLOW_LIVE_ACTIONS=true in environment
    Both conditions must be true.
    """
    return (
        _runtime_is_live()
        and os.environ.get("TELEGRAM_ALLOW_LIVE_ACTIONS", "").lower() == "true"
    )


def restricted_access(func):
    @wraps(func)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        user = getattr(update, "effective_user", None)
        user_id = getattr(user, "id", None)
        if user_id != AUTHORIZED_USER_ID:
            logger.warning(f"Unauthorized access attempt by {user_id}")
            await _reply_text(update, "Access Denied.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


@restricted_access
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    bp = state["exchange"]["buying_power"]
    obi = state["strategy"]["obi"]
    is_live = _runtime_is_live()
    mode_label = "LIVE" if is_live else "PAPER"
    msg = (
        f"<b>SYSTEM: {mode_label}</b>\n"
        f"REST: {'OK' if state['exchange']['connected'] else 'NO'} | WS: {'OK' if state['exchange']['ws_connected'] else 'NO'}\n"
        f"CP: ${bp:,.2f} | OBI: {obi:+.2f}\n"
        f"Signal: {state['strategy']['current_signal']} ({state['strategy']['active_symbol']})"
    )
    await _reply_text(update, msg, parse_mode=ParseMode.HTML)


@restricted_access
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        log_path = os.path.join(REPO_ROOT, "logs", "bot.log")
        if not os.path.exists(log_path):
            await _reply_text(update, "Log file not found.")
            return
        output = subprocess.check_output(["tail", "-n", "15", log_path]).decode("utf-8")
        await _reply_text(
            update, f"<code>{escape(output)}</code>", parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await _reply_text(update, f"Error fetching logs: {e}")


@restricted_access
async def metrics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    msg = (
        f"<b>System Metrics</b>\n"
        f"CPU: {state['system']['cpu_percent']:.1f}%\n"
        f"RAM: {state['system']['ram_percent']:.1f}%\n"
        f"Latency: {state['exchange']['latency_ms']}ms"
    )
    await _reply_text(update, msg, parse_mode=ParseMode.HTML)


@restricted_access
async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show positions scoped to the current runtime mode.
    In paper mode: queries paper=True spot positions.
    In live mode: queries live broker positions.
    """
    is_live = _runtime_is_live()
    paper = not is_live
    mode_label = "LIVE" if is_live else "PAPER"

    positions = get_spot_positions(paper=paper)
    if not positions:
        await _reply_text(update, f"No active spot positions ({mode_label} mode).")
        return

    msg = f"<b>Active Positions [{mode_label}]</b>\n"
    for p in positions:
        msg += f"- {p['symbol']}: {p['qty']:.4f} @ ${p['entry']:.2f}\n"
    await _reply_text(update, msg, parse_mode=ParseMode.HTML)


@restricted_access
async def exposure_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show exposure scoped to the current runtime mode.
    """
    is_live = _runtime_is_live()
    paper = not is_live
    mode_label = "LIVE" if is_live else "PAPER"

    positions = get_spot_positions(paper=paper)
    total = sum(float(p.get("qty", 0)) * float(p.get("entry", 0)) for p in positions)
    await _reply_text(update, f"Total Exposure [{mode_label}]: ${total:,.2f}")


@restricted_access
async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply_text(update, "Restarting bot process...")
    os._exit(0)  # Docker will restart


@restricted_access
async def spread_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    obi = state["strategy"]["obi"]
    micro = state["strategy"]["microprice"]
    await _reply_text(
        update,
        f"Order-book imbalance: {obi:+.2f}\nMicroprice: ${micro:,.2f}",
    )


@restricted_access
async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    is_live = _runtime_is_live()
    mode_label = "LIVE" if is_live else "PAPER"
    issues = []
    if not state["exchange"]["connected"]:
        issues.append("REST Disconnected")
    if not state["exchange"]["ws_connected"]:
        issues.append("WS Disconnected")
    if state["system"]["cpu_percent"] > 90:
        issues.append("High CPU Usage")

    if not issues:
        await _reply_text(
            update, f"Audit Passed [{mode_label}]: System integrity verified."
        )
    else:
        await _reply_text(
            update, f"Audit Issues [{mode_label}]:\n- " + "\n- ".join(issues)
        )


@restricted_access
async def cancel_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cancel all open spot orders.

    Requires BOTH:
      1. runtime mode == live  (DB truth)
      2. TELEGRAM_ALLOW_LIVE_ACTIONS=true in environment

    If runtime is paper, fails closed and refuses the action explicitly.
    If environment flag is missing, refuses the action explicitly.
    """
    is_live = _runtime_is_live()

    if not is_live:
        await _reply_text(
            update,
            "cancel_all REFUSED: runtime mode is PAPER. "
            "Destructive actions are only permitted in live mode.",
        )
        return

    if not _live_actions_allowed():
        await _reply_text(
            update,
            "cancel_all REFUSED: TELEGRAM_ALLOW_LIVE_ACTIONS is not set to 'true'. "
            "Set this environment variable on the server to permit live destructive actions.",
        )
        return

    try:
        broker = _get_broker(paper=False)
        if broker:
            broker.cancel_all_spot_orders()
            await _reply_text(update, "All active spot orders cancelled [LIVE].")
        else:
            await _reply_text(update, "Broker unavailable.")
    except Exception as e:
        await _reply_text(update, f"cancel_all error: {e}")


@restricted_access
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from logging_db.trade_logger import get_todays_trades

    is_live = _runtime_is_live()
    mode_label = "LIVE" if is_live else "PAPER"
    paper = not is_live
    try:
        today_trades = get_todays_trades(paper=paper)
        today = time.strftime("%Y-%m-%d")
        wins = len([t for t in today_trades if float(t.get("pnl_usd", 0)) > 0])
        total_pnl = sum(float(t.get("pnl_usd", 0)) for t in today_trades)

        msg = (
            f"<b>Daily Report ({today}) [{mode_label}]</b>\n"
            f"Trades: {len(today_trades)}\n"
            f"Win Rate: {(wins / len(today_trades) * 100 if today_trades else 0):.1f}%\n"
            f"Net PnL: ${total_pnl:+.2f}"
        )
        await _reply_text(update, msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        await _reply_text(update, f"Error generating report: {e}")


@restricted_access
async def uptime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    upt = state["system"]["uptime_seconds"]
    h, m = divmod(upt // 60, 60)
    await _reply_text(update, f"System Uptime: {h}h {m}m")


@restricted_access
async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await _reply_text(update, "Please provide a question. Usage: /ask <question>")
        return
    await _handle_ai_query(update, query)


@restricted_access
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    await _handle_ai_query(update, update.message.text)


async def _handle_ai_query(update: Update, query: str):
    thinking_msg = await _reply_text(
        update, "<i>Thinking...</i>", parse_mode=ParseMode.HTML
    )
    if thinking_msg is None:
        logger.error(
            "AI handler cannot respond because no effective message target was found."
        )
        return

    try:
        response = await asyncio.to_thread(ask_ai, query)

        keyboard = [
            [
                InlineKeyboardButton("🔍 View Logs", callback_data="cmd_logs"),
                InlineKeyboardButton("📉 Show OBI", callback_data="cmd_spread"),
            ],
            [
                InlineKeyboardButton("🔄 Restart Bot", callback_data="cmd_reboot"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await thinking_msg.edit_text(
            escape(response), reply_markup=reply_markup, parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"AI handler error: {e}")
        await thinking_msg.edit_text(f"Error: {str(e)}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        logger.warning("Button handler invoked without a callback query.")
        return
    await query.answer()

    if query.data == "cmd_logs":
        await logs_command(update, context)
    elif query.data == "cmd_spread":
        await spread_command(update, context)
    elif query.data == "cmd_reboot":
        await reboot_command(update, context)
    else:
        logger.warning("Unknown Telegram callback action: %s", query.data)
        await _reply_text(update, "Unknown action.")


@restricted_access
async def everything_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Aggregated command: status + audit + metrics + uptime + positions + exposure + report + spread.
    """
    from logging_db.trade_logger import get_todays_trades

    state = system_state.state.get_state()
    is_live = _runtime_is_live()
    mode_label = "LIVE" if is_live else "PAPER"
    paper = not is_live

    # 1. Operational Vitals
    upt = state["system"]["uptime_seconds"]
    h, m = divmod(upt // 60, 60)

    issues = []
    if not state["exchange"]["connected"]:
        issues.append("REST Disconnected")
    if not state["exchange"]["ws_connected"]:
        issues.append("WS Disconnected")
    if state["system"]["cpu_percent"] > 90:
        issues.append("High CPU")
    audit_str = "PASSED" if not issues else f"ISSUES: {', '.join(issues)}"

    # 2. Portfolio & Risk
    positions = get_spot_positions(paper=paper)
    total_exposure = sum(
        float(p.get("qty", 0)) * float(p.get("entry", 0)) for p in positions
    )

    pos_str = ""
    if not positions:
        pos_str = "None"
    else:
        for p in positions:
            pos_str += f"\n  • {p['symbol']}: {p['qty']:.4f} @ ${p['entry']:.2f}"

    # 3. Daily Performance
    try:
        today_trades = get_todays_trades(paper=paper)
        wins = len([t for t in today_trades if float(t.get("pnl_usd", 0)) > 0])
        total_pnl = sum(float(t.get("pnl_usd", 0)) for t in today_trades)
        perf_str = f"PnL: ${total_pnl:+.2f} | WR: {(wins / len(today_trades) * 100 if today_trades else 0):.1f}% ({len(today_trades)} trd)"
    except:
        perf_str = "PnL: Error fetching"

    msg = (
        f"<b>═══ SOVEREIGN SNAPSHOT [{mode_label}] ═══</b>\n\n"
        f"<b>[SYSTEM]</b>\n"
        f"Status: REST:{'OK' if state['exchange']['connected'] else 'NO'} | WS:{'OK' if state['exchange']['ws_connected'] else 'NO'}\n"
        f"Audit: {audit_str}\n"
        f"Uptime: {h}h {m}m | Latency: {state['exchange']['latency_ms']}ms\n"
        f"Load: CPU {state['system']['cpu_percent']:.1f}% | RAM {state['system']['ram_percent']:.1f}%\n\n"
        f"<b>[STRATEGY]</b>\n"
        f"Signal: {state['strategy']['current_signal']} ({state['strategy']['active_symbol']})\n"
        f"OBI: {state['strategy']['obi']:+.2f} | Micro: ${state['strategy']['microprice']:,.2f}\n\n"
        f"<b>[PORTFOLIO]</b>\n"
        f"BP: ${state['exchange']['buying_power']:,.2f}\n"
        f"Exposure: ${total_exposure:,.2f}\n"
        f"Positions: {pos_str}\n\n"
        f"<b>[PERFORMANCE]</b>\n"
        f"{perf_str}\n"
        f"════════════════════════"
    )
    await _reply_text(update, msg, parse_mode=ParseMode.HTML)


async def run_bot():
    """Start the Telegram bot manually to avoid loop conflicts."""
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set")
    try:
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("status", status_command))
        app.add_handler(CommandHandler("logs", logs_command))
        app.add_handler(CommandHandler("metrics", metrics_command))
        app.add_handler(CommandHandler("positions", positions_command))
        app.add_handler(CommandHandler("exposure", exposure_command))
        app.add_handler(CommandHandler("reboot", reboot_command))
        app.add_handler(CommandHandler("spread", spread_command))
        app.add_handler(CommandHandler("audit", audit_command))
        app.add_handler(CommandHandler("cancel_all", cancel_all_command))
        app.add_handler(CommandHandler("report", report_command))
        app.add_handler(CommandHandler("uptime", uptime_command))
        app.add_handler(CommandHandler("everything", everything_command))
        app.add_handler(CommandHandler("ask", ask_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
        app.add_handler(CallbackQueryHandler(button_handler))

        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        logger.info("Telegram Bot (Command Suite) is now live and polling.")

        # Block until the bot is stopped (which it won't be in this daemon thread)
        stop_event = asyncio.Event()
        await stop_event.wait()

    except Exception as e:
        logger.error(f"Telegram run_bot error: {e}")


def start_bot_thread():
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot())
        except Exception as e:
            logger.error(f"Telegram thread loop error: {e}")
        finally:
            loop.close()

    import threading

    t = threading.Thread(target=_run, daemon=True, name="TelegramBotThread")
    t.start()
    return t


# Legacy compatibility for sync sends
from telegram import Bot as LegacyBot


def send_message(text: str):
    try:
        bot = LegacyBot(token=TOKEN)
        asyncio.run(
            bot.send_message(
                chat_id=str(AUTHORIZED_USER_ID), text=text, parse_mode=ParseMode.HTML
            )
        )
    except Exception as e:
        logger.error(f"Legacy send error: {e}")


def send_liftoff():
    send_message(
        "<b>LIFTOFF</b>: The bot has completed its first cycle and is now live."
    )
