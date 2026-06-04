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

def _get_city_hub(ticker: str) -> str:
    """Sovereign Regional Hub Mapping (Mirror of strategy_engine.py)"""
    from forecast.strategy_engine import REGIONAL_HUBS
    for hub, cities in REGIONAL_HUBS.items():
        if any(city in ticker for city in cities):
            return hub
    return "UNKNOWN"

async def get_db_snapshot():
    """v19.1.KALSHI: Pure Kalshi Weather API Snapshot (Redesigned)."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. System Vitals
        vitals = {
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/').percent,
        }

        # 2. Forecast Data
        forecast_data = {"positions": [], "total_pnl": 0.0, "balance": 0.0, "active_markets": 0, "hubs": {}}
        
        try:
            # Positions and PNL
            cursor.execute("SELECT ticker as symbol, qty, entry, side, unrealized_pnl as pnl FROM forecast_positions WHERE qty > 0")
            positions = [dict(r) for r in cursor.fetchall()]
            
            # Fetch balance from lane_runtime_state snapshot or broker balance if possible
            cursor.execute("SELECT snapshot_json FROM lane_runtime_state WHERE lane_id='forecast'")
            row = cursor.fetchone()
            if row and row["snapshot_json"]:
                snap = json.loads(row["snapshot_json"])
                forecast_data["balance"] = snap.get("balance", 0.0)
                forecast_data["active_markets"] = snap.get("active_markets", 0)

            # Enrich positions with titles and calculate hubs
            for p in positions:
                # Add dummy title for now or fetch from forecast_markets
                cursor.execute("SELECT market_name FROM forecast_markets WHERE market_symbol=?", (p["symbol"],))
                m_row = cursor.fetchone()
                p["title"] = m_row["market_name"] if m_row else "Weather Prediction"
                p["mark"] = p["entry"] # Implied mark
                p["potential"] = p["qty"] * (1.0 - p["entry"]) if p["side"] == "YES" else p["qty"] * p["entry"]
                
                # Hub Exposure
                hub = _get_city_hub(p["symbol"])
                exposure = float(p["qty"]) * float(p["entry"])
                forecast_data["hubs"][hub] = forecast_data["hubs"].get(hub, 0.0) + exposure
                forecast_data["total_pnl"] += float(p["pnl"] or 0.0)

            forecast_data["positions"] = positions
        except Exception as e:
            logging.debug(f"Position snapshot error: {e}")

        # 3. RBI Calibration
        rbi_data = {"brier": 0.25, "win_rate": 0.0, "accuracy": 0.0}
        try:
            cursor.execute("SELECT brier_score, win_rate, ensemble_accuracy FROM weather_calibration ORDER BY ts DESC LIMIT 1")
            r_row = cursor.fetchone()
            if r_row:
                rbi_data = {
                    "brier": r_row["brier_score"],
                    "win_rate": r_row["win_rate"],
                    "accuracy": r_row["ensemble_accuracy"]
                }
        except: pass

        # 4. Intelligence Summary
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
        
        if forecast_data["positions"]:
            summary += f" Managing {len(forecast_data['positions'])} active regional weather hedges."

        # 5. Recent System Events
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

        conn.close()

        return {
            "forecast": forecast_data,
            "rbi": rbi_data,
            "intelligence_summary": summary,
            "events": events,
            "vitals": vitals,
            "system": {
                "time": datetime.now().strftime("%H:%M:%S"),
                "status": "OPERATIONAL",
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
