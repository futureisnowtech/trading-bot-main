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
    """Read latest stats from SQLite and System Vitals."""
    global _LAST_KALSHI_SYNC
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. System Vitals (psutil)
        vitals = {
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage('/').percent,
            "load_avg": os.getloadavg() if hasattr(os, 'getloadavg') else [0,0,0]
        }

        # 2. Latest Spot Stats
        cursor.execute("SELECT * FROM lane_runtime_state WHERE lane_id='crypto'")
        crypto_row = cursor.fetchone()
        
        # 3. Active Spot Positions
        cursor.execute("SELECT * FROM open_positions WHERE qty > 0")
        spot_positions_raw = [dict(r) for r in cursor.fetchall()]
        
        spot_positions = []
        for p in spot_positions_raw:
            spot_positions.append({
                "symbol": p["symbol"],
                "qty": p["qty"],
                "entry_price": p["entry"],
                "stop": p.get("stop", 0.0),
                "target": p.get("target", 0.0),
                "strategy": p.get("strategy", ""),
                "reason": p.get("entry_reason", "")
            })
        
        # 4. Learner State (Vaccinations)
        cursor.execute("SELECT * FROM learner_state")
        learner_rows = [dict(r) for r in cursor.fetchall()]
        vaccinations = [{"symbol": r["symbol"], "score": r["score"]} for r in learner_rows if r["vaccinated"]]

        # 5. Latest Forecast Stats
        cursor.execute("SELECT COUNT(*) as count FROM forecast_markets WHERE active = 1")
        forecast_count = cursor.fetchone()["count"]
        
        # 6. Forecast Positions (Kalshi)
        forecast_positions = []
        try:
            from execution.kalshi_broker import get_kalshi_broker
            kb = get_kalshi_broker()
            if not kb.is_connected(): kb.connect()
            
            now = time.time()
            if now - _LAST_KALSHI_SYNC > 60:
                kb._sync_positions()
                _LAST_KALSHI_SYNC = now
            forecast_positions = kb.get_positions()
        except: pass

        # 7. 24H PnL
        cursor.execute("SELECT SUM(pnl_usd) as pnl FROM trades WHERE ts > datetime('now', '-1 day')")
        pnl_row = cursor.fetchone()
        pnl_24h = float(pnl_row["pnl"] or 0.0)
        
        # 8. Recent Trades + Metadata (v18.33: Join with edge_snapshots if possible)
        # We'll fetch notes from trades which often contains the final decision string
        cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 20")
        recent_trades = []
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
                "notes": row.get("notes", "") # Contains RBI/ML metadata
            })
        
        # 9. System Events
        cursor.execute("SELECT * FROM system_events ORDER BY ts DESC LIMIT 10")
        events = [{"ts": r["ts"], "level": r["level"], "source": r["source"], "message": r["message"]} for r in cursor.fetchall()]

        conn.close()
        
        return {
            "spot": {
                "equity": crypto_row["buying_power_usd"] + crypto_row["capital_deployed_usd"] if crypto_row else 0.0,
                "pnl_24h": pnl_24h,
                "positions": spot_positions,
                "vaccinations": vaccinations
            },
            "forecast": {
                "active_markets": forecast_count,
                "positions": forecast_positions,
                "max_positions": 10
            },
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
        return f.read()

# Setup static mount if directories exist
static_path = os.path.join(_API_DIR, "..", "web")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
