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
from VERSION import VERSION

app = FastAPI(title=f"Kalshi Weather Engine API ({VERSION})")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import psutil

async def get_db_snapshot():
    """v19.1.KALSHI: Pure Kalshi Weather API Snapshot."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. System Vitals
        vitals = {
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/').percent,
            "load_avg": os.getloadavg() if hasattr(os, 'getloadavg') else [0,0,0]
        }

        # 2. Forecast Data
        forecast_data = {"positions": [], "total_pnl": 0.0, "equity": 0.0, "active_markets": 0}
        
        try:
            cursor.execute("SELECT snapshot_json FROM lane_runtime_state WHERE lane_id='forecast'")
            row = cursor.fetchone()
            if row and row["snapshot_json"]:
                forecast_data = json.loads(row["snapshot_json"])
        except Exception as e:
            logging.debug(f"Snapshot read error: {e}")

        # 3. 24H PnL
        pnl_24h = 0.0
        try:
            cursor.execute("SELECT SUM(pnl_usd) as pnl FROM trades WHERE ts > datetime('now', '-1 day')")
            row = cursor.fetchone()
            if row: pnl_24h = float(row["pnl"] or 0.0)
        except: pass
        
        summary = "Kalshi Weather Engine is operational."
        if pnl_24h > 0:
            summary = f"System is performing well today (+${pnl_24h:,.2f})."
        elif pnl_24h < 0:
            summary = f"Navigating a challenging market today (-${abs(pnl_24h):,.2f})."
            
        if forecast_data.get("positions"):
            summary += f" Managing {len(forecast_data['positions'])} active weather contracts."

        # 4. Recent System Events
        events = []
        try:
            cursor.execute("SELECT ts, level, source, message FROM system_events ORDER BY ts DESC LIMIT 20")
            for r in cursor.fetchall():
                row = dict(r)
                if row.get("ts"):
                    try:
                        ts_obj = datetime.fromisoformat(row["ts"].replace(" ", "T"))
                        row["ts"] = ts_obj.strftime("%Y-%m-%d %H:%M:%S")
                    except:
                        row["ts"] = str(row["ts"]).replace("T", " ")
                events.append(row)
        except: pass

        # 5. SRE Health
        sre = {
            "integrity_score": 100,
            "broker_connected": True,
            "weather_active_markets": forecast_data.get("active_markets", 0)
        }

        conn.close()

        return {
            "forecast": forecast_data,
            "intelligence_summary": summary,
            "events": events,
            "vitals": vitals,
            "sre": sre,
            "system": {
                "time": datetime.now().strftime("%H:%M:%S"),
                "status": "OPERATIONAL",
                "uptime_ts": psutil.boot_time(),
                "version": VERSION
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
        await asyncio.sleep(5)

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
