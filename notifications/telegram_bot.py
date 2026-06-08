import logging
import asyncio
import os
import psutil
import time
import subprocess
import sqlite3
import requests
import json
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
from config import BOT_LOG_PATH, DB_PATH, REPO_ROOT, TELEGRAM_CHAT_ID
from notifications.ai_agent import ask_ai
from notifications import sovereign_mobile_hud as hud

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


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Synchronous Telegram send helper for runtime modules outside the bot loop."""
    if not TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Telegram send helper is not configured")

    resp = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")
    return True


def _load_forecast_snapshot() -> dict:
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT snapshot_json FROM lane_runtime_state WHERE lane_id='forecast'"
            ).fetchone()
            if row and row["snapshot_json"]:
                return json.loads(row["snapshot_json"])
    except Exception as exc:
        logger.debug("forecast snapshot load failed: %s", exc)
    return {}


def _build_local_audit_snapshot() -> tuple[str, str]:
    """Build a dashboard-free audit snapshot directly from DB and process state."""
    from runtime.incident_tracker import get_incident_summary
    from runtime.operator_truth import get_live_kalshi_status, get_release_status

    truth = get_live_kalshi_status()
    release = get_release_status(truth=truth)
    snapshot = truth.get("forecast_snapshot") or _load_forecast_snapshot()
    raw_lines: list[str] = []

    with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
        conn.row_factory = sqlite3.Row

        lane_row = conn.execute(
            """
            SELECT health, readiness_state, blocked_reason, connected
            FROM lane_runtime_state
            WHERE lane_id='forecast'
            """
        ).fetchone()
        active_markets_row = conn.execute(
            "SELECT COUNT(*) AS n FROM forecast_markets WHERE active=1"
        ).fetchone()
        open_positions = conn.execute(
            """
            SELECT ticker, qty, entry_price, side
            FROM forecast_positions
            WHERE active = 1 AND qty > 0
            ORDER BY opened_at ASC
            """
        ).fetchall()
        rbi_row = conn.execute(
            """
            SELECT brier_score, win_rate, ensemble_accuracy, sample_size
            FROM weather_calibration
            ORDER BY ts DESC
            LIMIT 1
            """
        ).fetchone()

    incidents = get_incident_summary(DB_PATH)
    balance = float(truth.get("balance_usd") or snapshot.get("equity", 0.0) or 0.0)
    lane_truth = truth.get("forecast_lane") or {}
    health = str(lane_truth.get("health") or (lane_row["health"] if lane_row else "UNKNOWN"))
    readiness = str(
        lane_truth.get("readiness_state")
        or (lane_row["readiness_state"] if lane_row else "UNKNOWN")
    )
    blocked_reason = str(
        lane_truth.get("blocked_reason") or (lane_row["blocked_reason"] if lane_row else "")
    )
    active_markets = int(truth.get("active_markets") or (active_markets_row["n"] if active_markets_row else 0) or 0)
    drift = truth.get("position_drift", {})
    broker_positions_count = int(truth.get("broker_positions_count") or 0)
    learning = truth.get("weather_learning") or {}
    learning_global = learning.get("global_blend") or {}
    release_verdict = str(release.get("current_release_verdict") or "UNKNOWN")
    entries_allowed = bool(release.get("entries_allowed"))
    provider_mode = str(release.get("provider_mode") or "unknown")
    entry_scope = str(release.get("entry_scope") or "UNKNOWN")
    hourly_support = release.get("hourly_city_support") or {}
    hourly_support_line = (
        f"Hourly Support: {int(hourly_support.get('exchange_verified_city_count') or 0)}/"
        f"{int(hourly_support.get('universe_city_count') or 0)} cities exchange-verified"
    )

    msg_lines = [
        "<b>SOVEREIGN KALSHI AUDIT</b>",
        f"Status: {health}",
        f"Readiness: {readiness}",
        f"Release Gate: {release_verdict}",
        f"Entries Allowed: {'YES' if entries_allowed else 'NO'}",
        f"Entry Scope: {entry_scope}",
        f"Open Incidents: {incidents.get('total_open', 0)}",
        f"CPU/RAM: {psutil.cpu_percent():.0f}% / {psutil.virtual_memory().percent:.0f}%",
        "",
        f"🌪 <b>WEATHER ENGINE</b> ({active_markets} active markets)",
        f"Equity: ${balance:,.2f}",
        f"Open Positions: {broker_positions_count}",
        f"Provider Mode: {provider_mode}",
        hourly_support_line,
    ]
    raw_lines.extend(
        [
            f"Status: {health}",
            f"Readiness: {readiness}",
            f"Release Gate: {release_verdict}",
            f"Entries Allowed: {'YES' if entries_allowed else 'NO'}",
            f"Entry Scope: {entry_scope}",
            f"Open Incidents: {incidents.get('total_open', 0)}",
            f"Active Markets: {active_markets}",
            f"Equity: ${balance:,.2f}",
            f"Open Positions: {broker_positions_count}",
            f"Provider Mode: {provider_mode}",
            hourly_support_line,
        ]
    )

    if int(learning_global.get("sample_size") or 0) > 0:
        gfs_weight = float(learning_global.get("gfs_weight") or 0.60)
        ec_weight = float(learning_global.get("ecmwf_weight") or 0.40)
        sample_size = int(learning_global.get("sample_size") or 0)
        line = f"Adaptive Blend: GFS={gfs_weight:.0%} ECMWF={ec_weight:.0%} n={sample_size}"
    else:
        line = "Adaptive Blend: baseline 60/40 (learner active but not yet tilted)"
    msg_lines.append(line)
    raw_lines.append(line)

    if blocked_reason:
        msg_lines.append(f"Blocked Reason: {blocked_reason}")
        raw_lines.append(f"Blocked Reason: {blocked_reason}")

    if drift.get("has_drift"):
        msg_lines.append("Truth Drift: YES")
        raw_lines.append("Truth Drift: YES")
    else:
        raw_lines.append("Truth Drift: NO")

    blockers = release.get("top_infrastructure_blockers") or []
    if blockers:
        msg_lines.append(f"Top Blocker: {blockers[0]}")
        raw_lines.append(f"Top Blocker: {blockers[0]}")

    broker_positions = truth.get("broker_positions") or []
    if broker_positions:
        for pos in broker_positions[:8]:
            line = (
                f"• {pos['ticker']}: {pos['side']} x{float(pos['qty'] or 0):g} "
                f"@ ${float(pos['entry_price'] or 0.0):.2f}"
            )
            msg_lines.append(line)
            raw_lines.append(line)
    else:
        msg_lines.append("• No active weather positions.")
        raw_lines.append("No active weather positions.")

    if rbi_row:
        brier = float(rbi_row["brier_score"] or 0.0)
        win_rate = float(rbi_row["win_rate"] or 0.0)
        sample_size = int(rbi_row["sample_size"] or 0)
        line = f"RBI: Brier={brier:.4f} WR={win_rate:.2%} n={sample_size}"
        msg_lines.extend(["", line])
        raw_lines.append(line)

    return "\n".join(msg_lines), "\n".join(raw_lines)


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
    from runtime.operator_truth import get_live_kalshi_status, get_release_status

    truth = get_live_kalshi_status()
    release = get_release_status(truth=truth)
    balance = float(truth.get("balance_usd") or 0.0)
    active_markets = int(truth.get("active_markets") or 0)
    broker_positions_count = int(truth.get("broker_positions_count") or 0)
    drift = truth.get("position_drift", {})

    # Lever 5: Cost Telemetry Integration
    usd_spent = 0.0
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as c:
            row = c.execute("SELECT SUM(usd_cost) FROM api_costs WHERE ts > ?", (time.time() - 86400,)).fetchone()
            usd_spent = float(row[0] or 0.0)
    except: pass

    msg = (
        f"<b>KALSHI WEATHER ENGINE: LIVE</b>\n"
        f"Balance: ${balance:,.2f}\n"
        f"Active Markets: {active_markets}\n"
        f"Broker Positions: {broker_positions_count}\n"
        f"Release Gate: {release.get('current_release_verdict')}\n"
        f"Entries Allowed: {'YES' if release.get('entries_allowed') else 'NO'}\n"
        f"Provider Mode: {release.get('provider_mode') or 'unknown'}\n"
        f"Truth Drift: {'YES' if drift.get('has_drift') else 'NO'}\n"
        f"Infra Blockers: {len(release.get('top_infrastructure_blockers') or [])}\n"
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
        log_path = BOT_LOG_PATH
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
    try:
        from runtime.operator_truth import get_live_kalshi_status

        truth = get_live_kalshi_status()
        rows = truth.get("broker_positions") or []
        drift = truth.get("position_drift", {})

        if not rows:
            await _reply_text(update, "No active forecast positions.")
            return

        msg = "<b>Active Forecast Positions</b>\n"
        for r in rows:
            msg += f"🎫 {r['ticker']} | {r['side']} x{float(r['qty'] or 0):g} @ ${float(r['entry_price'] or 0):.2f}\n"
        if drift.get("has_drift"):
            msg += "\n⚠️ Truth drift detected between broker and DB."
        await _reply_text(update, msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        await _reply_text(update, f"Error fetching positions: {e}")


@restricted_access
async def reboot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply_text(update, "Restarting bot process...")
    os._exit(0)


@restricted_access
async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from runtime.build_info import get_build_info

    build = get_build_info()
    msg = f"<b>SOVEREIGN KALSHI AUDIT ({build['app_version']})</b>\n\n"

    try:
        snapshot_msg, raw_text = _build_local_audit_snapshot()
        msg = snapshot_msg + "\n\n"
        msg += "\n🔮 <b>ORACLE STRATEGIC ANALYSIS:</b>\n"
        if update.message:
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
        msg += f"⚠️ <b>WARNING:</b> Local audit snapshot failed: {e}\n"
        
    await _reply_text(update, msg, parse_mode=ParseMode.HTML)


@restricted_access
async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
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
