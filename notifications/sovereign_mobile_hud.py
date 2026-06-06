"""
notifications/sovereign_mobile_hud.py — Sovereign Mobile HUD Brain.

Architectural Mandate:
1. Provide high-fidelity, interactive system insights on Telegram.
2. Focus on Kalshi Weather Alpha and Sovereign Philosophy (Sigma, Hubs, Swaps).
3. Act as a stateful "Sovereign Oracle" interface.
"""

import os
import time
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import DB_PATH, REPO_ROOT
from runtime.build_info import get_build_info

logger = logging.getLogger(__name__)

# ── Sovereign UI Themes ────────────────────────────────────────────────────────

THEME_HEADER = "═══ SOVEREIGN HUD v{} ═══"
ICON_LIVE = "🟢 LIVE"
ICON_PAPER = "⚪️ PAPER"
ICON_KALSHI = "🌪"
ICON_RISK = "🛡"

# ── Formatting Helpers ─────────────────────────────────────────────────────────

def format_currency(val: float) -> str:
    return f"${val:,.2f}"

def format_pnl(val: float) -> str:
    icon = "🟩" if val >= 0 else "🟥"
    return f"{icon} {val:+.2f}"

# ── Core Data Fetchers ────────────────────────────────────────────────────────

def get_system_vitals() -> Dict[str, Any]:
    """Fetch system-wide vitals from DB and process state."""
    build = get_build_info()
    vitals = {
        "version": build["app_version"],
        "mode": "LIVE",
        "cpu": 0.0,
        "ram": 0.0,
        "uptime": "0h 0m",
        "integrity": 100,
        "api_spend_24h": 0.0
    }
    
    try:
        import psutil
        vitals["cpu"] = psutil.cpu_percent()
        vitals["ram"] = psutil.virtual_memory().percent
        
        # Mode from DB
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            row = conn.execute("SELECT startup_ts FROM system_runtime_state ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                upt = time.time() - float(row[0] or time.time())
                h, m = divmod(upt // 60, 60)
                vitals["uptime"] = f"{int(h)}h {int(m)}m"
                
            # AI Spend
            spend = conn.execute("SELECT SUM(usd_cost) FROM api_costs WHERE ts > ?", (time.time() - 86400,)).fetchone()
            vitals["api_spend_24h"] = float(spend[0] or 0.0)
            
    except Exception as e:
        logger.error(f"[mobile_hud] Vitals fetch error: {e}")
        
    return vitals

def get_kalshi_state() -> Dict[str, Any]:
    """Fetch Kalshi-specific portfolio state."""
    state = {
        "balance": 0.0,
        "positions": [],
        "hubs": {},
        "active_markets": 0,
        "drift": {},
        "broker_connected": False,
        "release_verdict": "UNKNOWN",
        "entries_allowed": False,
    }
    
    try:
        from runtime.operator_truth import get_live_kalshi_status, get_release_status

        truth = get_live_kalshi_status()
        release = get_release_status(truth=truth)
        state["balance"] = float(truth.get("balance_usd") or 0.0)
        state["positions"] = list(truth.get("broker_positions") or [])
        state["active_markets"] = int(truth.get("active_markets") or 0)
        state["drift"] = dict(truth.get("position_drift") or {})
        state["broker_connected"] = bool(truth.get("broker_connected"))
        state["release_verdict"] = str(release.get("current_release_verdict") or "UNKNOWN")
        state["entries_allowed"] = bool(release.get("entries_allowed"))
            
        # Hub exposure logic
        from forecast.strategy_engine import _get_city_hub
        for p in state["positions"]:
            hub = _get_city_hub(p.get("ticker", ""))
            cost = p.get("qty", 0) * p.get("entry_price", 0)
            state["hubs"][hub] = state["hubs"].get(hub, 0.0) + cost
            
    except Exception as e:
        logger.error(f"[mobile_hud] Kalshi state error: {e}")
        
    return state

# ── Message Generators ─────────────────────────────────────────────────────────

def build_main_menu_msg() -> str:
    v = get_system_vitals()
    k = get_kalshi_state()
    
    msg = [
        THEME_HEADER.format(v["version"]),
        f"Status: {ICON_LIVE} | Up: {v['uptime']}",
        f"SRE Integrity: {v['integrity']}%",
        "",
        f"<b>{ICON_KALSHI} KALSHI WEATHER</b>",
        f"Balance: {format_currency(k['balance'])}",
        f"Positions: {len(k['positions'])}/15",
        f"Active Hubs: {len(k['hubs'])}",
        f"Release Gate: {k['release_verdict']}",
        f"Entries: {'LIVE' if k['entries_allowed'] else 'PAUSED'}",
        f"Drift: {'YES' if k.get('drift', {}).get('has_drift') else 'NO'}",
        "",
        "<i>Tap a module below for deep-dive analysis.</i>"
    ]
    return "\n".join(msg)

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(f"{ICON_KALSHI} Kalshi Deep-Dive", callback_data="hud_kalshi_main"),
        ],
        [
            InlineKeyboardButton(f"{ICON_RISK} Sovereign Philosophy", callback_data="hud_philosophy"),
            InlineKeyboardButton("🔄 Refresh", callback_data="hud_main_refresh"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_kalshi_deep_dive_msg() -> str:
    k = get_kalshi_state()
    
    msg = [
        f"<b>{ICON_KALSHI} KALSHI SOVEREIGN INTEL</b>",
        f"Equity: {format_currency(k['balance'])}",
        f"Broker: {'Connected' if k.get('broker_connected') else 'Disconnected'}",
        "",
        "<b>Current Positions:</b>"
    ]
    
    if not k["positions"]:
        msg.append("  - <i>Scanning for high-alpha entries...</i>")
    else:
        for p in k["positions"][:8]: # Limit to avoid massive messages
            sym = p.get("ticker", "")
            qty = p.get("qty", 0)
            side = p.get("side", "YES")
            cost = p.get("entry_price", 0.0)
            msg.append(f"• <code>{sym}</code> {side} x{qty} @ {cost:.3f}")
            
    msg.append("")
    if k.get("drift", {}).get("has_drift"):
        msg.append("<b>Truth Drift:</b>")
        for pos in k["drift"].get("broker_only", [])[:5]:
            msg.append(f"  - Broker only: <code>{pos['ticker']}</code> {pos['side']} x{pos['qty']}")
        for pos in k["drift"].get("db_only", [])[:5]:
            msg.append(f"  - DB only: <code>{pos['ticker']}</code> {pos['side']} x{pos['qty']}")
        msg.append("")

    msg.append("<b>Hub Exposure:</b>")
    for hub, exposure in k.get("hubs", {}).items():
        if hub == "UNKNOWN": continue
        warning = "⚠️" if exposure > 30 else ""
        msg.append(f"  - {hub}: {format_currency(exposure)} {warning}")
        
    return "\n".join(msg)

def get_kalshi_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("⬅️ Back to HUD", callback_data="hud_main_menu"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_philosophy_msg() -> str:
    msg = [
        "<b>📜 SOVEREIGN TRADING PHILOSOPHY</b>",
        "",
        "<b>1. The Sigma Lever (Volatility)</b>",
        "We treat model disagreement (Sigma) as a risk vector. Chaotic ensembles trigger automatic position size reduction.",
        "",
        "<b>2. Opportunistic Swaps</b>",
        "Capital is finite. We ruthlessly flatten sub-par positions if a new candidate offers >10% improvement in EV.",
        "",
        "<b>3. Hub Gating</b>",
        "Regional weather systems are correlated. We cap exposure per hub at $40 to prevent 'black swan' city-cluster washouts.",
        "",
        "<b>4. METAR Ground Truth</b>",
        "We exit early if airport sensors (METAR) diverge from the 3km HRRR models before resolution."
    ]
    return "\n".join(msg)
