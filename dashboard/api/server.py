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

app = FastAPI(title="Algo Trading Terminal API (v19.1.ARCH)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import psutil

async def get_db_snapshot():
    """v19.1: Absolute Read-Only Cache Snapshot. Eliminates network calls from HUD."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. System Vitals (Local Machine)
        vitals = {
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/').percent,
            "load_avg": os.getloadavg() if hasattr(os, 'getloadavg') else [0,0,0]
        }

        # 2. Read Bot-Cached Snapshots (The Load Reducer)
        spot_data = {"positions": [], "equity": 0.0}
        forecast_data = {"positions": [], "total_pnl": 0.0}
        
        try:
            cursor.execute("SELECT lane_id, snapshot_json, readiness_state FROM lane_runtime_state")
            for row in cursor.fetchall():
                lid = row["lane_id"]
                s_json = row["snapshot_json"]
                if not s_json: continue
                
                parsed = json.loads(s_json)
                if lid in ("spot", "crypto"): spot_data = parsed
                elif lid == "forecast": forecast_data = parsed
        except Exception as e:
            logging.debug(f"Snapshot read error: {e}")

        # 3. 24H PnL Aggregation
        pnl_24h = 0.0
        try:
            cursor.execute("SELECT SUM(pnl_usd) as pnl FROM trades WHERE ts > datetime('now', '-1 day')")
            row = cursor.fetchone()
            if row: pnl_24h = float(row["pnl"] or 0.0)
        except: pass
        
        # 4. Intelligence Summary
        regime = "NEUTRAL"
        try:
            cursor.execute("SELECT last_regime FROM spot_regime_state ORDER BY ts DESC LIMIT 1")
            row = cursor.fetchone()
            if row: regime = row["last_regime"]
        except: pass

        summary = f"Bot is operational. Market weather is currently {regime}."
        if pnl_24h > 0:
            summary = f"System is performing well today (+${pnl_24h:,.2f})."
        elif pnl_24h < 0:
            summary = f"Navigating a challenging market today (-${abs(pnl_24h):,.2f})."
            
        if spot_data.get("positions"):
            symbols = ", ".join([p["symbol"] for p in spot_data["positions"]])
            summary += f" Managing {len(spot_data['positions'])} trades: {symbols}."

        # 5. Recent System Events
        events = []
        try:
            cursor.execute("SELECT ts, level, source, message FROM system_events ORDER BY ts DESC LIMIT 20")
            for r in cursor.fetchall():
                events.append(dict(r))
        except: pass

        # 6. Live Hunt (Candidates)
        live_hunt = []
        try:
            cursor.execute("SELECT symbol, direction, final_spot_score, entry_block_reason as reason, ts FROM scan_candidates ORDER BY ts DESC LIMIT 5")
            for r in cursor.fetchall():
                live_hunt.append({
                    "symbol": r["symbol"],
                    "direction": r["direction"],
                    "score": round(float(r["final_spot_score"] or 0.0), 1),
                    "reason": r["reason"] or "Passed Score",
                    "ts": r["ts"]
                })
        except: pass

        conn.close()

        return {
            "spot": {
                "equity": spot_data.get("equity", 0.0),
                "pnl_24h": pnl_24h,
                "positions": spot_data.get("positions", []),
                "regime": regime
            },
            "forecast": {
                "positions": forecast_data.get("positions", []),
                "total_pnl": forecast_data.get("total_pnl", 0.0)
            },
            "intelligence_summary": summary,
            "events": events,
            "live_hunt": live_hunt,
            "vitals": vitals,
            "system": {
                "time": datetime.now().strftime("%H:%M:%S"),
                "status": "OPERATIONAL",
                "uptime_ts": psutil.boot_time()
            }
        }
    except Exception as e:
        logging.error(f"HUD API Snapshot error: {e}")
        return {"error": str(e)}

@app.get("/api/state")
async def get_state():
    return await get_db_snapshot()

async def event_generator() -> AsyncGenerator[str, None]:
    while True:
        try:
            data = await get_db_snapshot()
            yield f"data: {json.dumps(data)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        await asyncio.sleep(5) # Throttle to 5s for ultra-low load

@app.get("/api/stream")
async def stream(request: Request):
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    with open(os.path.join(_API_DIR, "..", "web", "index.html"), "r") as f:
        html = f.read()
    return HTMLResponse(content=html)

static_path = os.path.join(_API_DIR, "..", "web")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
