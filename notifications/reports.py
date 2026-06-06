import sqlite3
import logging
import json
from datetime import datetime, timezone, timedelta
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

def _get_db_path():
    from config import DB_PATH
    return DB_PATH

def generate_war_room_report(paper: bool = False) -> str:
    """
    Generate the 9:00 PM ET 'War Room' Report.
    PnL: +$X | Fees: $Y | Shadow-to-Live Variance: Z% | System Health: NOMINAL
    """
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # Today's date range (9PM ET to 9PM ET or just calendar today for simplicity)
    # Let's use last 24 hours for the report
    now = datetime.now(timezone.utc)
    start_time = (now - timedelta(hours=24)).isoformat()
    
    is_paper = 1 if paper else 0
    
    # 1. PnL and Fees
    try:
        res = conn.execute("""
            SELECT 
                SUM(pnl_usd) as total_pnl,
                SUM(fee_usd) as total_fees
            FROM trades
            WHERE ts >= ? AND paper = ?
        """, (start_time, is_paper)).fetchone()
        
        pnl = res['total_pnl'] or 0.0
        fees = res['total_fees'] or 0.0
    except Exception as e:
        logger.error(f"Error fetching PnL/Fees: {e}")
        pnl, fees = 0.0, 0.0

    # 2. 'The Leak' (Missed Profit)
    # Definition: Potential profit from candidates that met all criteria but weren't entered.
    try:
        leak_res = conn.execute("""
            SELECT SUM(co.ret_4h_pct * sc.size_usd / 100.0) as potential_profit
            FROM scan_candidates sc
            JOIN candidate_outcomes co ON sc.id = co.candidate_id
            WHERE sc.ts >= ? 
              AND sc.paper = ?
              AND sc.decision != 'entered'
              AND sc.composite_score >= 50
              AND sc.econ_approved = 1
              AND co.ret_4h_pct > 0
        """, (start_time, is_paper)).fetchone()
        leak = leak_res['potential_profit'] or 0.0
    except Exception as e:
        logger.debug(f"Error fetching Leak: {e}")
        leak = 0.0

    # 3. Shadow-to-Live Variance
    # (Placeholder logic: compare paper PnL vs Live PnL if both exist, 
    # or just use 0.0% if we are only in one mode)
    variance = 0.0 # Placeholder
    
    # 4. System Health
    health = "NOMINAL"
    try:
        from runtime.runtime_state import get_system_state
        state = get_system_state(db_path)
        if state.get('global_status') != 'OK':
            health = "DEGRADED"
        
        import kill_switch
        if kill_switch.is_halted():
            health = "HALTED"
    except Exception:
        pass

    conn.close()

    report = (
        f"📊 <b>9:00 PM ET War Room Report</b>\n"
        f"PnL: <code>{pnl:+.2f}</code> | Fees: <code>{fees:.2f}</code> | "
        f"Shadow-to-Live Variance: <code>{variance:.1f}%</code> | "
        f"System Health: <b>{health}</b>\n"
        f"The Leak (Missed Profit): <code>${leak:,.2f}</code>"
    )
    
    return report

def send_war_room_report():
    """Fetch and send the report via Telegram."""
    # Determine mode from env
    
    report = generate_war_room_report()
    
    try:
        from notifications.telegram_bot import send_message
        send_message(report)
    except Exception as e:
        logger.error(f"Failed to send War Room report: {e}")

def generate_sovereign_payload() -> dict:
    """
    v19.1.9: Deterministic state aggregation for AI analysis.
    Zero-cost SQL and memory queries only.
    """
    db_path = _get_db_path()
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "weather_lane": {},
        "sre_health": {}
    }

    # 1. Weather Lane State
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        active_weather = conn.execute(
            "SELECT COUNT(*) FROM forecast_contracts WHERE active=1"
        ).fetchone()[0]
        open_positions = conn.execute(
            "SELECT ticker, qty, entry_price, side FROM forecast_positions WHERE active=1"
        ).fetchall()
        recent_fills = len(open_positions)

        lane_snapshot = {}
        try:
            lane_row = conn.execute(
                "SELECT snapshot_json FROM lane_runtime_state WHERE lane_id='forecast'"
            ).fetchone()
            if lane_row and lane_row["snapshot_json"]:
                lane_snapshot = json.loads(lane_row["snapshot_json"])
        except Exception:
            lane_snapshot = {}

        from data.kalshi_weather_monitor import _WEATHER_SHADOW_STATE
        edges_visible = len(_WEATHER_SHADOW_STATE)

        hub_exposure = {}
        try:
            from config import get_kalshi_position_exposure_usd
            from forecast.strategy_engine import _get_city_hub

            for row in open_positions:
                hub = _get_city_hub(row["ticker"])
                exposure = get_kalshi_position_exposure_usd(
                    float(row["qty"] or 0.0),
                    float(row["entry_price"] or 0.0),
                )
                hub_exposure[hub] = round(hub_exposure.get(hub, 0.0) + exposure, 2)
        except Exception:
            hub_exposure = {}

        rbi_row = conn.execute(
            """
            SELECT brier_score, win_rate, ensemble_accuracy, sample_size
            FROM weather_calibration
            ORDER BY ts DESC
            LIMIT 1
            """
        ).fetchone()
        rbi = dict(rbi_row) if rbi_row else {}

        payload["weather_lane"] = {
            "status": "OPERATIONAL",
            "active_markets": active_weather,
            "total_positions": recent_fills,
            "weather_edge_visibility": edges_visible,
            "equity_usd": lane_snapshot.get("equity", 0.0),
            "hub_exposure": hub_exposure,
            "rbi": rbi,
        }
        conn.close()
    except Exception as e:
        payload["weather_lane"]["error"] = str(e)

    # 2. SRE Health
    try:
        from runtime.runtime_state import get_system_state
        sys_state = get_system_state(db_path)
        
        # Count critical events in last 6h
        conn = sqlite3.connect(db_path)
        six_h_ago = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        critical_count = conn.execute(
            "SELECT COUNT(*) FROM system_events WHERE level IN ('CRITICAL', 'ERROR') AND ts >= ?", 
            (six_h_ago,)
        ).fetchone()[0]
        conn.close()

        payload["sre_health"] = {
            "integrity_score": sys_state.get("integrity_score", 100),
            "global_status": sys_state.get("global_status", "OK"),
            "critical_events_6h": critical_count
        }
    except Exception:
        payload["sre_health"]["status"] = "UNKNOWN"

    return payload

def send_sovereign_briefing():
    """
    Trigger the Analyst-in-the-Loop pipeline:
    Gather -> Synthesize -> Deliver.
    """
    logger.info("[Reports] Executing Sovereign Briefing cycle...")
    
    # 1. Deterministic Gathering
    payload = generate_sovereign_payload()
    
    # 2. Expert Synthesis (LLM Analyst)
    try:
        from notifications.ai_agent import ask_ai
        analysis = ask_ai(
            "Generate a concise Sovereign weather briefing from this payload. "
            "Focus on live weather execution risk, deployment state, and any obvious anomalies.\n\n"
            + json.dumps(payload, indent=2)
        )
    except Exception as e:
        analysis = f"⚠️ Analysis Engine Error: {e}"

    # 3. Format Final Message
    # Combine expert bullets with raw numeric footer
    stats_footer = (
        f"\n---\n"
        f"📍 <b>Live Metrics</b>\n"
        f"Weather Equity: ${payload.get('weather_lane', {}).get('equity_usd', 0):,.2f}\n"
        f"Weather Targets: {payload.get('weather_lane', {}).get('active_markets', 0)}\n"
        f"SRE Integrity: {payload.get('sre_health', {}).get('integrity_score', 0)}%"
    )
    
    final_message = f"🛡️ <b>SOVEREIGN ANALYST BRIEFING</b>\n\n{analysis}\n{stats_footer}"
    
    # 4. Deliver
    try:
        from notifications.telegram_bot import send_message
        send_message(final_message)
        logger.info("[Reports] Sovereign Briefing delivered ✅")
    except Exception as e:
        logger.error(f"Failed to deliver Sovereign Briefing: {e}")
