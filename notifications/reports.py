import sqlite3
import logging
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
    from config import PAPER_TRADING
    report = generate_war_room_report(paper=PAPER_TRADING)
    
    try:
        from notifications.telegram_bot import send_message
        send_message(report)
    except Exception as e:
        logger.error(f"Failed to send War Room report: {e}")
