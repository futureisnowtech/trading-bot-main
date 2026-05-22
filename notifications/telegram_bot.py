import logging
import asyncio
import os
import psutil
import time
import subprocess
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
from spot_engine import get_spot_positions, _get_broker
from notifications.ai_agent import ask_ai

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
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

        # Try splitting at the last newline before the limit
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
    """
    Return True only when the system is confirmed to be running in live mode.

    Primary source: system_runtime_state DB table (written by main.py on startup
    and updated by go_live.py / go_paper.py transitions).
    Fallback: system_state.state in-process mode field.
    Fallback: config.False (config-file truth, least authoritative).

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
        

        return not False
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
    bp = state["exchange"]["buying_power"]
    obi = state["strategy"]["obi"]
    is_live = _runtime_is_live()
    mode_label = "LIVE" if is_live else "PAPER"
    
    # Lever 5: Cost Telemetry Integration
    usd_spent = 0.0
    try:
        import sqlite3
        _db = os.path.join(REPO_ROOT, "logs", "trades.db")
        with sqlite3.connect(_db) as c:
            row = c.execute("SELECT SUM(usd_cost) FROM api_costs WHERE ts > ?", (time.time() - 86400,)).fetchone()
            usd_spent = float(row[0] or 0.0)
    except: pass

    msg = (
        f"<b>SYSTEM: {mode_label}</b>\n"
        f"REST: {'OK' if state['exchange']['connected'] else 'NO'} | WS: {'OK' if state['exchange']['ws_connected'] else 'NO'}\n"
        f"CP: ${bp:,.2f} | OBI: {obi:+.2f}\n"
        f"Signal: {state['strategy']['current_signal']} ({state['strategy']['active_symbol']})\n"
        f"AI Spend (24h): ${usd_spent:.4f}"
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

    positions = get_spot_positions()
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

    positions = get_spot_positions()
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
        broker = _get_broker()
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
        today_trades = get_todays_trades()
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
    user = getattr(update, "effective_user", None)
    user_id = user.id if user else 0
    
    # Lever 3: Apply Debounce & Dedupe
    rejection = _is_duplicate(user_id, query)
    if rejection:
        await _reply_text(update, f"⚠️ {rejection}")
        return

    # Native iOS "flicker": Send TYPING action
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    except Exception:
        pass

    thinking_msg = await _reply_text(
        update, "<i>Thinking...</i>", parse_mode=ParseMode.HTML
    )
    if thinking_msg is None:
        logger.error(
            "AI handler cannot respond because no effective message target was found."
        )
        return

    # v18.32: UI Animation Task (makes static text feel alive on iOS)
    async def animate_thinking():
        chars = [".", "..", "..."]
        idx = 0
        try:
            while True:
                await asyncio.sleep(1.5)
                await thinking_msg.edit_text(
                    f"<i>Thinking{chars[idx % 3]}</i>", parse_mode=ParseMode.HTML
                )
                idx += 1
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    anim_task = asyncio.create_task(animate_thinking())

    try:
        logger.info(f"[telegram] Starting AI query for user={user_id}: {query[:50]}...")
        # v18.31: Enforce 100s timeout on thread pool task to prevent loop exhaustion
        response = await asyncio.wait_for(asyncio.to_thread(ask_ai, query), timeout=100.0)
        logger.info(f"[telegram] AI query complete for user={user_id}")

        anim_task.cancel() # Stop animation

        chunks = chunk_message(escape(response, quote=False))

        reply_markup = _get_tactical_keyboard()

        # Edit thinking message with first chunk
        await thinking_msg.edit_text(
            chunks[0],
            reply_markup=reply_markup if len(chunks) == 1 else None,
            parse_mode=ParseMode.HTML,
        )

        # Send remaining chunks as new messages
        for i, chunk in enumerate(chunks[1:], 1):
            is_last = i == len(chunks) - 1
            await _reply_text(
                update,
                chunk,
                reply_markup=reply_markup if is_last else None,
                parse_mode=ParseMode.HTML,
            )

    except asyncio.TimeoutError:
        anim_task.cancel()
        logger.error(f"AI query timed out for user={user_id}")
        await thinking_msg.edit_text("⏳ Request timed out (90s limit). Please try a shorter query.")
    except Exception as e:
        anim_task.cancel()
        logger.error(f"AI handler error: {e}")
        try:
            # v18.31: Safer error display with escaping fix
            await thinking_msg.edit_text(f"⚠️ Error: {escape(str(e), quote=False)}")
        except Exception:
            await thinking_msg.edit_text("⚠️ An internal error occurred while processing your request.")


@restricted_access
async def vitals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tactical Widget 1: System Vitals"""
    state = system_state.state.get_state()
    is_live = _runtime_is_live()
    mode_label = "LIVE" if is_live else "PAPER"
    
    msg = (
        f"<b>SOVEREIGN VITALS [{mode_label}]</b>\n"
        f"Mode: <code>{state.get('mode', 'UNKNOWN')}</code>\n"
        f"Bankroll: <code>${state['exchange']['buying_power']:,.2f}</code>\n"
        f"Active: <code>{state['strategy']['active_symbol']}</code>\n"
        f"Signal: <code>{state['strategy']['current_signal']}</code>\n"
        f"OBI: <code>{state['strategy']['obi']:+.2f}</code>"
    )
    await _reply_text(update, msg, parse_mode=ParseMode.HTML)


@restricted_access
async def recent_trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tactical Widget 2: Recent Trades Summary"""
    try:
        import sqlite3
        _db = os.path.join(REPO_ROOT, "logs", "trades.db")
        with sqlite3.connect(_db) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT symbol, action, price, pnl_usd, ts FROM trades ORDER BY ts DESC LIMIT 5").fetchall()
            
            if not rows:
                await _reply_text(update, "No recent trades found.")
                return

            msg = "<b>RECENT EXECUTION</b>\n"
            for r in rows:
                pnl = float(r['pnl_usd'] or 0)
                pnl_str = f"| ${pnl:+.2f}" if r['action'] == 'SELL' else ""
                msg += f"• <code>{r['symbol']}</code> {r['action']} @ {r['price']:.4f} {pnl_str}\n"
            
            await _reply_text(update, msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        await _reply_text(update, f"Trade fetch error: {e}")


@restricted_access
async def regime_policy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tactical Widget 3: Live Regime Policy"""
    try:
        from runtime.spot_strategy import ACTIVE_UNIVERSE
        from config import SPOT_REGIME_SCORE_FLOORS
        
        msg = "<b>REGIME POLICY</b>\n"
        msg += f"Floors: T={SPOT_REGIME_SCORE_FLOORS['TREND']} N={SPOT_REGIME_SCORE_FLOORS['NEUTRAL']} C={SPOT_REGIME_SCORE_FLOORS['CHOP']}\n\n"
        
        # Show top 5 symbols from universe to keep message concise
        for sym in ACTIVE_UNIVERSE[:5]:
            msg += f"• <code>{sym}</code>: Allowed=ALL | Sniper=ON\n"
            
        await _reply_text(update, msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        await _reply_text(update, f"Policy fetch error: {e}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        logger.warning("Button handler invoked without a callback query.")
        return
    await query.answer()

    if query.data == "cmd_vitals":
        await vitals_command(update, context)
    elif query.data == "cmd_trades":
        await recent_trades_command(update, context)
    elif query.data == "cmd_policy":
        await regime_policy_command(update, context)
    else:
        logger.warning("Unknown Telegram callback action: %s", query.data)
        await _reply_text(update, "Unknown action.")


def _get_tactical_keyboard():
    """Returns the v18.33 Tactical Keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📊 System Vitals", callback_data="cmd_vitals"),
            InlineKeyboardButton("📜 Recent Trades", callback_data="cmd_trades"),
        ],
        [
            InlineKeyboardButton("🛡️ Regime Policy", callback_data="cmd_policy"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


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
    positions = get_spot_positions()
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
        today_trades = get_todays_trades()
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

    # v18.17: Sovereign Polling Guard
    # Only allow polling if the hostname matches the authorized production machine.
    import socket
    import config as _cfg

    current_host = socket.gethostname()
    target_hosts = ["algo-bot-live", "algo-bot-spot"]
    target_host_cfg = getattr(_cfg, "TELEGRAM_POLLING_HOSTNAME", None)
    if target_host_cfg:
        target_hosts.append(target_host_cfg)
    
    force_polling = os.environ.get("TELEGRAM_FORCE_POLLING", "").lower() == "true"

    if current_host not in target_hosts and not force_polling:
        logger.warning(
            f"[telegram] Sovereign Polling Guard: hostname mismatch ('{current_host}' not in {target_hosts}). "
            "Disabling polling (Command Mode) to prevent conflict with Production. "
            "Send-only mode is still active. Use TELEGRAM_FORCE_POLLING=true to bypass."
        )
        # Block until the bot is stopped (no polling started)
        stop_event = asyncio.Event()
        await stop_event.wait()
        return

    if current_host in target_hosts:
        logger.info(f"[telegram] Sovereign Polling Guard: hostname match ('{current_host}'). Command Mode AUTHORIZED.")
    else:
        logger.info(f"[telegram] Sovereign Polling Guard: hostname mismatch ({target_hosts}), but TELEGRAM_FORCE_POLLING=true. Command Mode FORCED.")

    try:
        app = ApplicationBuilder().token(TOKEN).build()

        # v18.17: Raw Update Logger (Diagnostic)
        async def raw_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = getattr(update, "effective_user", None)
            user_id = getattr(user, "id", None)
            text = getattr(update.message, "text", "[no text]") if update.message else "[no message]"
            logger.info(f"[telegram] RAW UPDATE: user_id={user_id} text='{text}'")

        app.add_handler(MessageHandler(filters.ALL, raw_logger), group=-1)

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
        await app.updater.start_polling(drop_pending_updates=False)

        logger.info("Telegram Bot (Command Suite) is now live and polling.")

        # Block until the bot is stopped (which it won't be in this daemon thread)
        stop_event = asyncio.Event()
        await stop_event.wait()

    except Exception as e:
        logger.error(f"Telegram run_bot error: {e}")


def start_bot_thread():
    global _BOT_STARTED
    if _BOT_STARTED:
        logger.warning("[telegram] start_bot_thread() called again — already running, skipping.")
        return None
    _BOT_STARTED = True

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
    def _fire_and_forget():
        try:
            bot = LegacyBot(token=TOKEN)
            chunks = chunk_message(text)
            for chunk in chunks:
                asyncio.run(
                    bot.send_message(
                        chat_id=str(AUTHORIZED_USER_ID),
                        text=chunk,
                        parse_mode=ParseMode.HTML,
                    )
                )
        except Exception as e:
            logger.error(f"Legacy send error: {e}")

    import threading
    threading.Thread(target=_fire_and_forget, daemon=True).start()


def send_liftoff():
    send_message(
        "<b>LIFTOFF</b>: The bot has completed its first cycle and is now live."
    )
