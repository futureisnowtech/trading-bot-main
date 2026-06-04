import logging
import asyncio
import os
import psutil
import time
import subprocess
import sqlite3
from typing import Dict, Optional
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
from telegram.constants import ParseMode, ChatAction

import system_state
from config import REPO_ROOT, DB_PATH
from notifications.ai_agent import ask_ai
from notifications import sovereign_mobile_hud as hud
from forecast.db import get_open_forecast_positions

# v18.19.5: Project Apex Production Overhaul (80% Cost Reduction)
# Lever 3: Debounce & Dedupe
import hashlib

_LAST_QUERY_TIME: Dict[int, float] = {}
_QUERY_HASH_CACHE: Dict[str, float] = {}

def _is_duplicate(user_id: int, query: str) -> Optional[str]:
    """
    Lever 3: 5s Rate Limit + 60s Dedupe.
    Returns rejection message if duplicate/rate-limited, else None.
    """
    now = time.time()
    
    # 5s per-user rate limit
    last_time = _LAST_QUERY_TIME.get(user_id, 0)
    if now - last_time < 5:
        return f"⏳ Rate limit: Please wait {5 - int(now - last_time)}s."
    
    # 60s query hash dedupe
    q_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()
    if q_hash in _QUERY_HASH_CACHE:
        if now - _QUERY_HASH_CACHE[q_hash] < 60:
            return "🔁 Duplicate query detected. Please wait 60s before repeating the same question."
    
    _LAST_QUERY_TIME[user_id] = now
    _QUERY_HASH_CACHE[q_hash] = now
    
    # Cleanup old hashes (simple LRU)
    if len(_QUERY_HASH_CACHE) > 100:
        expired = [k for k, v in _QUERY_HASH_CACHE.items() if now - v > 60]
        for k in expired: del _QUERY_HASH_CACHE[k]
        
    return None

logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AUTHORIZED_USER_ID = int(os.environ.get("TELEGRAM_AUTHORIZED_USER_ID", "8224826883"))
_BOT_STARTED: bool = False


def _effective_message(update: Update):
    return getattr(update, "effective_message", None)


def chunk_message(text: str, limit: int = 4000) -> list[str]:
    """
    Split a string into chunks that fit within Telegram's 4096 character limit.
    Attempts to split on newlines first, then spaces.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        split_pos = text.rfind("\n", 0, limit)
        if split_pos == -1:
            # Fallback to the last space
            split_pos = text.rfind(" ", 0, limit)

        if split_pos == -1:
            # Absolute fallback: hard cut at the limit
            split_pos = limit

        chunks.append(text[:split_pos].strip())
        text = text[split_pos:].strip()
    return chunks


async def _reply_text(update: Update, text: str, **kwargs):
    message = _effective_message(update)
    if message is None:
        logger.warning("Telegram reply target missing for update type %s", type(update))
        return None
    return await message.reply_text(text, **kwargs)


def _runtime_is_live() -> bool:
    """Return True since Kalshi lane is strictly live."""
    return True


def restricted_access(func):
    @wraps(func)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs
    ):
        user = getattr(update, "effective_user", None)
        user_id = getattr(user, "id", None)
        logger.debug(f"[telegram] Message from user_id={user_id} (authorized={AUTHORIZED_USER_ID})")
        if user_id != AUTHORIZED_USER_ID:
            logger.warning(f"Unauthorized access attempt by {user_id}")
            await _reply_text(update, "Access Denied.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


@restricted_access
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    
    # Kalshi specifics
    balance = 0.0
    active_markets = 0
    try:
        from execution.kalshi_broker import get_kalshi_broker
        broker = get_kalshi_broker()
        balance = broker.get_account_balance()
        
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT COUNT(*) FROM forecast_markets WHERE active=1").fetchone()
            active_markets = row[0] if row else 0
    except: pass

    # Lever 5: Cost Telemetry Integration
    usd_spent = 0.0
    try:
        with sqlite3.connect(DB_PATH) as c:
            row = c.execute("SELECT SUM(usd_cost) FROM api_costs WHERE ts > ?", (time.time() - 86400,)).fetchone()
            usd_spent = float(row[0] or 0.0)
    except: pass

    msg = (
        f"<b>KALSHI WEATHER ENGINE: LIVE</b>\n"
        f"Balance: ${balance:,.2f}\n"
        f"Active Markets: {active_markets}\n"
        f"AI Spend (24h): ${usd_spent:.4f}"
    )
    await _reply_text(update, msg, parse_mode=ParseMode.HTML)


@restricted_access
async def hud_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The Sovereign Mobile HUD Entry Point."""
    msg = hud.build_main_menu_msg()
    reply_markup = hud.get_main_menu_keyboard()
    await _reply_text(update, msg, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


@restricted_access
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        log_path = os.path.join(REPO_ROOT, "logs", "bot.log")
        if not os.path.exists(log_path):
            await _reply_text(update, "Log file not found.")
            return
        output = subprocess.check_output(["tail", "-n", "15", log_path]).decode("utf-8")
        await _reply_text(
            update, f"<code>{escape(output, quote=False)}</code>", parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await _reply_text(update, f"Error fetching logs: {e}")


@restricted_access
async def metrics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = system_state.state.get_state()
    msg = (
        f"<b>System Metrics</b>\n"
        f"CPU: {state['system']['cpu_percent']:.1f}%\n"
        f"RAM: {state['system']['ram_percent']:.1f}%"
    )
    await _reply_text(update, msg, parse_mode=ParseMode.HTML)


@restricted_access
async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show open forecast positions."""
    # SRE FIX: DB mapping alignment
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT ticker, qty, entry, unrealized_pnl FROM forecast_positions WHERE qty > 0").fetchall()
            
        if not rows:
            await _reply_text(update, "No active forecast positions.")
            return

        msg = "<b>Active Forecast Positions</b>\n"
        for r in rows:
            msg += f"🎫 {r['ticker']} | {r['qty']} @ ${r['entry']:.2f}\n"
        await _reply_text(update, msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        await _reply_text(update, f"Error fetching positions: {e}")


@restricted_access
async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply_text(update, "Restarting bot process...")
    os._exit(0)


@restricted_access
async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import requests
    
    from VERSION import VERSION
    msg = f"<b>SOVEREIGN KALSHI AUDIT ({VERSION})</b>\n\n"
    raw_text = ""
    
    try:
        # Dashboard API check
        try:
            resp = requests.get("http://127.0.0.1:8080/api/state", timeout=3)
        except requests.exceptions.RequestException:
            resp = requests.get("http://algo-dashboard:8080/api/state", timeout=3)
            
        data = resp.json()
        sys_info = data.get("system", {})
        vitals = data.get("vitals", {})
        sre = data.get("sre", {})
        
        line = f"🖥 <b>Status:</b> {sys_info.get('status', 'OK')}\n"
        msg += line; raw_text += line
        line = f"🛡 <b>Data Integrity:</b> {sre.get('integrity_score', 100)}%\n"
        msg += line; raw_text += line
        line = f"⚙️ <b>Load:</b> CPU {vitals.get('cpu', 0):.0f}% | RAM {vitals.get('ram', 0):.0f}%\n\n"
        msg += line; raw_text += line
        
        # Forecast Lane
        forecast = data.get("forecast", {})
        f_pos = forecast.get("positions", [])
        active_markets = forecast.get("active_markets", 0)
        
        line = f"🌪 <b>WEATHER ENGINE (Markets: {active_markets})</b>\n"
        msg += line; raw_text += line
        if f_pos:
            for p in f_pos:
                line = f"   - {p.get('symbol')}: {p.get('qty')} {p.get('side')} | Cost: ${p.get('entry', 0):.4f}\n"
                msg += line; raw_text += line
        else:
            line = "   - No active weather positions (Monitoring)\n"
            msg += line; raw_text += line
            
        # Oracle Analysis
        msg += "\n🔮 <b>ORACLE STRATEGIC ANALYSIS:</b>\n"
        await update.message.reply_chat_action(ChatAction.TYPING)
        
        prompt = (
            "You are the Sovereign SRE Oracle for the Kalshi Weather Engine. "
            "Analyze this system audit snapshot. Identify any strategic gaps or risk anomalies.\n\n"
            f"### AUDIT SNAPSHOT ###\n{raw_text}"
        )
        
        try:
            analysis = await asyncio.wait_for(asyncio.to_thread(ask_ai, prompt), timeout=60.0)
            msg += f"<i>{escape(analysis or 'AI returned no content.', quote=False)}</i>"
        except Exception as ai_err:
            msg += f"<i>Oracle analysis failed: {ai_err}</i>"
            
    except Exception as e:
        msg += f"⚠️ <b>WARNING:</b> Failed to reach SRE Dashboard API.\n"
        
    await _reply_text(update, msg, parse_mode=ParseMode.HTML)


@restricted_access
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            # Simplified report for forecast resolutions
            closed = conn.execute(
                "SELECT ticker, qty, entry_price, side, exit_type FROM forecast_positions WHERE active=0 AND closed_at LIKE ?",
                (f"{today}%",)
            ).fetchall()

            msg = (
                f"<b>Daily Forecast Report ({today})</b>\n"
                f"Resolved Positions: {len(closed)}\n"
            )
            for p in closed:
                msg += f"- {p['ticker']}: {p['qty']} {p['side']} ({p['exit_type']})\n"
            
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
    await _handle_ai_query(update, context, query)


@restricted_access
async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    await _handle_ai_query(update, context, update.message.text)


async def _handle_ai_query(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    user = getattr(update, "effective_user", None)
    user_id = user.id if user else 0
    
    rejection = _is_duplicate(user_id, query)
    if rejection:
        await _reply_text(update, f"⚠️ {rejection}")
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    except Exception:
        pass

    thinking_msg = await _reply_text(update, "<i>Thinking...</i>", parse_mode=ParseMode.HTML)
    if thinking_msg is None: return

    try:
        response = await asyncio.wait_for(asyncio.to_thread(ask_ai, query), timeout=100.0)
        if response is None: response = "Error: AI Agent returned a null response."
        chunks = chunk_message(escape(response, quote=False))

        await thinking_msg.edit_text(chunks[0], parse_mode=ParseMode.HTML)
        for chunk in chunks[1:]:
            await _reply_text(update, chunk, parse_mode=ParseMode.HTML)

    except Exception as e:
        await thinking_msg.edit_text(f"⚠️ Error: {escape(str(e), quote=False)}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None: return
    await query.answer()

    if query.data == "hud_main_menu":
        await query.edit_message_text(hud.build_main_menu_msg(), reply_markup=hud.get_main_menu_keyboard(), parse_mode=ParseMode.HTML)
    elif query.data == "hud_kalshi_main":
        await query.edit_message_text(hud.build_kalshi_deep_dive_msg(), reply_markup=hud.get_kalshi_menu_keyboard(), parse_mode=ParseMode.HTML)
    elif query.data == "hud_philosophy":
        await query.edit_message_text(hud.build_philosophy_msg(), reply_markup=hud.get_kalshi_menu_keyboard(), parse_mode=ParseMode.HTML)
    elif query.data == "hud_main_refresh":
        await query.edit_message_text(hud.build_main_menu_msg(), reply_markup=hud.get_main_menu_keyboard(), parse_mode=ParseMode.HTML)


async def run_bot():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set")

    try:
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("status", status_command))
        app.add_handler(CommandHandler("hud", hud_command))
        app.add_handler(CommandHandler("logs", logs_command))
        app.add_handler(CommandHandler("metrics", metrics_command))
        app.add_handler(CommandHandler("positions", positions_command))
        app.add_handler(CommandHandler("reboot", reboot_command))
        app.add_handler(CommandHandler("audit", audit_command))
        app.add_handler(CommandHandler("report", report_command))
        app.add_handler(CommandHandler("uptime", uptime_command))
        app.add_handler(CommandHandler("ask", ask_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))
        app.add_handler(CallbackQueryHandler(button_handler))

        await app.initialize()
        await app.start()
        # SRE FIX: drop_pending_updates helps resolve polling conflicts on rapid restart
        await app.updater.start_polling(drop_pending_updates=True)

        logger.info("Kalshi Weather Bot is now live.")
        stop_event = asyncio.Event()
        await stop_event.wait()

    except Exception as e:
        logger.error(f"Telegram run_bot error: {e}")


def start_bot_thread():
    global _BOT_STARTED
    if _BOT_STARTED:
        return None
    _BOT_STARTED = True

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_bot())
        except Exception: pass
        finally: loop.close()

    import threading
    t = threading.Thread(target=_run, daemon=True, name="TelegramBotThread")
    t.start()
    return t
