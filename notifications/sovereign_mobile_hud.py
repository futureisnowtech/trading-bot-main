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
        "short_sha": build.get("build_short_sha") or build.get("short_sha") or "",
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
        "production_policy": {},
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
        state["production_policy"] = dict(truth.get("production_policy") or {})

        # Hub exposure logic
        from forecast.strategy_engine import _get_city_hub
        for p in state["positions"]:
            hub = _get_city_hub(p.get("ticker", ""))
            exposure = p.get("market_exposure_usd")
            if exposure is None:
                held_price = p.get("held_side_entry_price")
                if held_price is None:
                    held_price = p.get("entry_price", 0)
                exposure = float(p.get("qty", 0) or 0) * float(held_price or 0)
            state["hubs"][hub] = state["hubs"].get(hub, 0.0) + float(exposure or 0)

    except Exception as e:
        logger.error(f"[mobile_hud] Kalshi state error: {e}")

    return state

# ── Message Generators ─────────────────────────────────────────────────────────

def build_main_menu_msg() -> str:
    v = get_system_vitals()
    k = get_kalshi_state()

    policy = k.get("production_policy") or {}
    risk = policy.get("risk") or {}
    rbi = policy.get("rbi2") or {}
    msg = [
        THEME_HEADER.format(f"{v['version']} ({v['short_sha'] or 'SHA unavailable'})"),
        f"Status: {ICON_LIVE} | Up: {v['uptime']}",
        f"SRE Integrity: {v['integrity']}%",
        "",
        f"<b>{ICON_KALSHI} KALSHI WEATHER</b>",
        f"Balance: {format_currency(k['balance'])}",
        f"Positions: {len(k['positions'])}/{int(risk.get('max_concurrent_positions') or 0)}",
        f"Active Hubs: {len(k['hubs'])}",
        f"Release Gate: {k['release_verdict']}",
        f"Entries: {'LIVE' if k['entries_allowed'] else 'PAUSED'}",
        f"Drift: {'YES' if k.get('drift', {}).get('has_drift') else 'NO'}",
        "Execution: taker-only IOC",
        "Probability: deterministic GFS/ECMWF + AIGFS + bounded physics",
        (
            f"RBI: {rbi.get('status') or 'unknown'} "
            f"({float(rbi.get('observed_days') or 0.0):.1f}/"
            f"{float(rbi.get('minimum_days') or 0.0):g} days, "
            f"{int(rbi.get('independent_event_count') or 0)}/"
            f"{int(rbi.get('required_independent_events') or 0)} events)"
        ),
        "",
        "<i>Tap below to see whether the bot can trade, what it is holding, and what needs attention.</i>"
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
    risk = (k.get("production_policy") or {}).get("risk") or {}

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
            cost = p.get("held_side_entry_price")
            if cost is None:
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
        if hub == "UNKNOWN":
            continue
        warning = "⚠️" if exposure >= float(risk.get("hub_exposure_cap_usd") or float("inf")) else ""
        msg.append(f"  - {hub}: {format_currency(exposure)} {warning}")
    msg.append(
        f"  Cap: {format_currency(float(risk.get('hub_exposure_cap_usd') or 0.0))} "
        f"({risk.get('hub_exposure_rule') or 'unknown rule'})"
    )

    return "\n".join(msg)

def get_kalshi_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("⬅️ Back to HUD", callback_data="hud_main_menu"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_philosophy_msg() -> str:
    k = get_kalshi_state()
    policy = k.get("production_policy") or {}
    risk = policy.get("risk") or {}
    rbi = policy.get("rbi2") or {}
    msg = [
        "<b>📜 PRODUCTION DECISION POLICY</b>",
        "",
        "<b>1. Deterministic Physics Probability</b>",
        "GFS and ECMWF form the governed blend; actual AIGFS disagreement scales uncertainty, optional HRRR informs near-term daily highs, and bounded physics adjusts temperature before the probability CDF. No commercial Open-Meteo ensemble is used.",
        "",
        "<b>2. Taker-Only Execution</b>",
        "Every new entry crosses a freshly checked executable ask as IOC. Unfilled quantity is canceled instead of resting on the book.",
        "",
        "<b>3. Fee-Inclusive Risk Gating</b>",
        f"Base position cap ${float(risk.get('base_position_cap_usd') or 0.0):g}; event cap {float(risk.get('max_risk_per_event_pct') or 0.0):.0%}; deployed cap {float(risk.get('max_deployed_pct') or 0.0):.0%}; live hub cap ${float(risk.get('hub_exposure_cap_usd') or 0.0):.2f}.",
        "",
        "<b>4. Physical Guardrails</b>",
        f"A {float(risk.get('minimum_model_headroom_f') or 0.0):g}F model-headroom floor and near-term METAR cooling veto can block an entry before capital is committed.",
        "",
        "<b>5. RBI 2.0 Governance</b>",
        f"Official outcomes train the challenger during the current epoch. Learned weights remain inactive until at least {float(rbi.get('minimum_days') or 0.0):g} days and {int(rbi.get('required_independent_events') or 0)} independent events are present, then a human must promote them."
    ]
    return "\n".join(msg)
