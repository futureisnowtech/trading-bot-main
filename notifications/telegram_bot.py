import logging
import asyncio
import os
import psutil
import time
import subprocess
from functools import wraps
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.constants import ParseMode

import system_state
from spot_engine import get_spot_positions, _get_broker

logger = logging.getLogger(__name__)

# Hardcoded production credentials
TOKEN = '8681504660:AAGddi9r0PEtqC1TFA4973SwsgytRH3x5BU'
AUTHORIZED_USER_ID = 8224826883

def restricted_access(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != AUTHORIZED_USER_ID:
            logger.warning(f"Unauthorized access attempt by {update.effective_user.id}")
            await update.message.reply_text("⛔ Access Denied.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

@restricted_access
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    bp = state["exchange"]["buying_power"]
    obi = state["strategy"]["obi"]
    msg = (
        f"<b>SYSTEM: ACTIVE</b>\n"
        f"CP: ${bp:,.2f} | OBI: {obi:+.2f}\n"
        f"Signal: {state['strategy']['current_signal']}"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

@restricted_access
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        log_path = os.path.join(os.getcwd(), "logs", "bot.log")
        if not os.path.exists(log_path):
            await update.message.reply_text("Log file not found.")
            return
        
        output = subprocess.check_output(["tail", "-n", "15", log_path]).decode("utf-8")
        await update.message.reply_text(f"<code>{output}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"Error fetching logs: {e}")

@restricted_access
async def metrics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    msg = (
        f"<b>System Metrics</b>\n"
        f"CPU: {state['system']['cpu_percent']:.1f}%\n"
        f"RAM: {state['system']['ram_percent']:.1f}%\n"
        f"Latency: {state['exchange']['latency_ms']}ms"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

@restricted_access
async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = get_spot_positions(paper=False)
    if not positions:
        await update.message.reply_text("No active spot positions.")
        return
    
    msg = "<b>Active Positions</b>\n"
    for p in positions:
        msg += f"• {p['symbol']}: {p['qty']:.4f} @ ${p['entry']:.2f}\n"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

@restricted_access
async def exposure_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = get_spot_positions(paper=False)
    total = sum(float(p.get("qty", 0)) * float(p.get("entry", 0)) for p in positions)
    await update.message.reply_text(f"Total Exposure: ${total:,.2f}")

@restricted_access
async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Rebooting system...")
    os._exit(0) # Docker will restart

@restricted_access
async def spread_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    micro = state["strategy"]["microprice"]
    await update.message.reply_text(f"Current Microprice: ${micro:,.2f}")

@restricted_access
async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 Triggering system audit...")
    # Logic to trigger audit can be added here

@restricted_access
async def cancel_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    broker = _get_broker(paper=False)
    if broker:
        broker.cancel_all_spot_orders()
        await update.message.reply_text("🛑 All active spot orders cancelled.")
    else:
        await update.message.reply_text("Broker unavailable.")

@restricted_access
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Generating daily report...")

@restricted_access
async def uptime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    upt = state["system"]["uptime_seconds"]
    h, m = divmod(upt // 60, 60)
    await update.message.reply_text(f"System Uptime: {h}h {m}m")

async def run_bot():
    """Start the Telegram bot manually to avoid loop conflicts."""
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
        asyncio.run(bot.send_message(chat_id=str(AUTHORIZED_USER_ID), text=text, parse_mode=ParseMode.HTML))
    except Exception as e:
        logger.error(f"Legacy send error: {e}")

def send_liftoff():
    send_message("🚀 <b>LIFTOFF</b>: The bot has completed its first cycle and is now live.")
