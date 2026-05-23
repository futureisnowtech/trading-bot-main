import os
import sys
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import AsyncGenerator
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Resolve project root
_API_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_API_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DB_PATH

app = FastAPI(title="Algo Trading Terminal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import psutil

# In-memory store for the latest state to avoid hitting DB too hard
latest_state = {}
_LAST_KALSHI_SYNC = 0

async def get_db_snapshot():
    """Read latest stats from SQLite and System Vitals with high-fidelity error handling."""
    global _LAST_KALSHI_SYNC
    try:
        # v18.34: Harden connection for multi-process isolation
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. System Vitals (psutil)
        # Note: calling twice with interval=0.1 to ensure non-zero CPU readings
        cpu = psutil.cpu_percent()
        if cpu == 0.0: cpu = psutil.cpu_percent(interval=0.1)
        
        vitals = {
            "cpu": cpu,
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/').percent,
            "load_avg": os.getloadavg() if hasattr(os, 'getloadavg') else [0,0,0]
        }

        # 2. Latest Spot Stats
        crypto_row = None
        try:
            cursor.execute("SELECT * FROM lane_runtime_state WHERE lane_id='crypto'")
            crypto_row = cursor.fetchone()
        except sqlite3.OperationalError: pass # Table missing
        
        # 3. Active Spot Positions
        spot_positions = []
        try:
            cursor.execute("SELECT * FROM open_positions WHERE qty > 0")
            for p in cursor.fetchall():
                p_dict = dict(p)
                spot_positions.append({
                    "symbol": p_dict["symbol"],
                    "qty": p_dict["qty"],
                    "entry_price": p_dict["entry"],
                    "stop": p_dict.get("stop", 0.0),
                    "target": p_dict.get("target", 0.0),
                    "strategy": p_dict.get("strategy", ""),
                    "reason": p_dict.get("entry_reason", "")
                })
        except sqlite3.OperationalError: pass
        
        # 4. Learner State (Vaccinations)
        vaccinations = []
        try:
            cursor.execute("SELECT * FROM learner_state")
            for r in cursor.fetchall():
                if r["vaccinated"]:
                    vaccinations.append({"symbol": r["symbol"], "score": r["score"]})
        except sqlite3.OperationalError: pass

        # 5. Latest Forecast Stats
        forecast_count = 0
        try:
            cursor.execute("SELECT COUNT(*) as count FROM forecast_markets WHERE active = 1")
            row = cursor.fetchone()
            if row: forecast_count = row["count"]
        except sqlite3.OperationalError: pass
        
        # 6. Forecast Positions (Kalshi)
        forecast_positions = []
        try:
            from config import FORECAST_LANE_ACTIVE, KALSHI_ENABLED
            if FORECAST_LANE_ACTIVE or KALSHI_ENABLED:
                from execution.kalshi_broker import get_kalshi_broker
                kb = get_kalshi_broker()
                now = time.time()
                
                if not kb.is_connected():
                    if now - getattr(kb, "_last_connect_attempt", 0) > 60:
                        kb._last_connect_attempt = now
                        kb.connect()
                
                if kb.is_connected():
                    if now - _LAST_KALSHI_SYNC > 60:
                        kb._sync_positions()
                        _LAST_KALSHI_SYNC = now
                    forecast_positions = kb.get_positions()
        except: pass

        # 7. 24H PnL
        pnl_24h = 0.0
        try:
            cursor.execute("SELECT SUM(pnl_usd) as pnl FROM trades WHERE ts > datetime('now', '-1 day')")
            pnl_row = cursor.fetchone()
            if pnl_row: pnl_24h = float(pnl_row["pnl"] or 0.0)
        except sqlite3.OperationalError: pass
        
        # 8. Recent Trades + Metadata
        recent_trades = []
        try:
            cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 20")
            for t in cursor.fetchall():
                row = dict(t)
                recent_trades.append({
                    "timestamp": row["ts"],
                    "symbol": row["symbol"],
                    "side": row["action"],
                    "price": row["price"],
                    "qty": row["qty"],
                    "strategy": row["strategy"],
                    "pnl_usd": float(row.get("pnl_usd") or 0.0),
                    "won": row.get("won"),
                    "notes": row.get("notes", "")
                })
        except sqlite3.OperationalError: pass
        
        # 9. System Events
        events = []
        try:
            cursor.execute("SELECT * FROM system_events ORDER BY ts DESC LIMIT 10")
            for r in cursor.fetchall():
                events.append({"ts": r["ts"], "level": r["level"], "source": r["source"], "message": r["message"]})
        except sqlite3.OperationalError: pass

        # 10. Live Hunt (Scan Candidates)
        live_hunt = []
        reason_map = {
            "strategy_veto": "Market structure too weak for current regime",
            "spot_size_below_minimum": "Account balance too low for minimum order after risk scaling",
            "below_regime_floor": "Signal strength did not meet strict regime requirements",
            "spread_cap_exceeded": "Market spread too wide (liquidity protection active)",
            "depth_below_minimum": "Insufficient order book depth for safe entry",
            "spot_balance_unavailable": "API authentication failure (CDP Key Error)",
            "cooldown_active": "Resting symbol to prevent over-trading",
            "Passed Score": "Setup validated. Entering execution pipeline."
        }
        try:
            cursor.execute("SELECT symbol, direction, final_spot_score, entry_block_reason as reason, ts FROM scan_candidates ORDER BY ts DESC LIMIT 5")
            for r in cursor.fetchall():
                raw_reason = r["reason"] or "Passed Score"
                live_hunt.append({
                    "symbol": r["symbol"],
                    "direction": r["direction"],
                    "score": round(float(r["final_spot_score"] or 0.0), 1),
                    "reason": raw_reason,
                    "reason_layman": reason_map.get(raw_reason, raw_reason),
                    "ts": r["ts"]
                })
        except sqlite3.OperationalError: pass

        # 11. Intelligence Summary & Market Weather
        regime = "NEUTRAL"
        try:
            cursor.execute("SELECT last_regime FROM spot_regime_state ORDER BY ts DESC LIMIT 1")
            row = cursor.fetchone()
            if row: regime = row["last_regime"]
        except sqlite3.OperationalError: pass

        summary = f"Bot is operational. Market weather is currently {regime}."
        if pnl_24h > 0:
            summary = f"System is performing well today with a net profit of ${pnl_24h:,.2f}."
        elif pnl_24h < 0:
            summary = f"System is navigating a challenging market (Daily P&L: -${abs(pnl_24h):,.2f}). Guardrails are active."
            
        if spot_positions:
            symbols = ", ".join([p["symbol"] for p in spot_positions])
            summary += f" Currently managing active spot positions in {symbols}."
        else:
            summary += " Currently in 'Watch' mode, waiting for high-conviction entry vectors."

        # 12. Macro Radar (X-Ray into monitored events)
        macro_radar = []
        try:
            # Join contracts and quotes to get real-time implied probability
            cursor.execute("""
                SELECT fm.market_name, fc.local_symbol, fc.strike, 
                       (SELECT implied_prob FROM forecast_quotes fq WHERE fq.contract_id = fc.id ORDER BY ts DESC LIMIT 1) as prob
                FROM forecast_contracts fc
                JOIN forecast_markets fm ON fm.id = fc.market_id
                WHERE fc.active = 1
                ORDER BY fc.last_seen_at DESC
                LIMIT 5
            """)
            for r in cursor.fetchall():
                macro_radar.append({
                    "event": r["market_name"],
                    "symbol": r["local_symbol"],
                    "probability": round(float(r["prob"] or 0.5) * 100, 1)
                })
        except sqlite3.OperationalError: pass

        conn.close()
        
        # Calculate Equity safely
        equity = 0.0
        if crypto_row:
            cr = dict(crypto_row)
            equity = (cr.get("buying_power_usd", 0.0) or 0.0) + (cr.get("capital_deployed_usd", 0.0) or 0.0)

        return {
            "spot": {
                "equity": equity,
                "pnl_24h": pnl_24h,
                "positions": spot_positions,
                "vaccinations": vaccinations,
                "regime": regime
            },
            "forecast": {
                "active_markets": forecast_count,
                "positions": forecast_positions,
                "max_positions": 10,
                "radar": macro_radar
            },
            "live_hunt": live_hunt,
            "intelligence_summary": summary,
            "recent_trades": recent_trades,
            "events": events,
            "vitals": vitals,
            "system": {
                "time": datetime.now().strftime("%H:%M:%S"),
                "status": "OPERATIONAL",
                "uptime": psutil.boot_time()
            }
        }
    except Exception as e:
        logging.error(f"DB Snapshot error: {e}")
        return {"error": str(e)}

@app.get("/api/state")
async def get_state():
    return await get_db_snapshot()

async def event_generator() -> AsyncGenerator[str, None]:
    """SSE Generator with keep-alive and error safety."""
    while True:
        try:
            data = await get_db_snapshot()
            yield f"data: {json.dumps(data)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        await asyncio.sleep(2)

@app.get("/api/stream")
async def stream(request: Request):
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# Serve the static frontend
# Note: In a real deploy, these would be in dashboard/web/
# For simplicity in this implementation, I'll provide an HTML endpoint or serve static
@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    with open(os.path.join(_API_DIR, "..", "web", "index.html"), "r") as f:
        html = f.read()
    return HTMLResponse(content=html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

# Setup static mount if directories exist
static_path = os.path.join(_API_DIR, "..", "web")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
