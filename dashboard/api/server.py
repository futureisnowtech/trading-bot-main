import os
import sys
import asyncio
import json
import logging
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

# In-memory store for the latest state to avoid hitting DB too hard
latest_state = {}

async def get_db_snapshot():
    """Read latest stats from SQLite."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Latest Spot Stats (from lane_runtime_state)
        cursor.execute("SELECT * FROM lane_runtime_state WHERE lane_id='crypto'")
        crypto_row = cursor.fetchone()
        
        # Active Spot Positions (from open_positions)
        cursor.execute("SELECT * FROM open_positions WHERE qty > 0")
        spot_positions_raw = [dict(r) for r in cursor.fetchall()]
        
        # Transform positions to include symbol and qty
        spot_positions = []
        for p in spot_positions_raw:
            spot_positions.append({
                "symbol": p["symbol"],
                "qty": p["qty"],
                "entry_price": p["entry"],
                "unrealized_pnl": 0.0 # Calculate if needed, but 0.0 is safe for now
            })
        
        # Latest Forecast Stats
        cursor.execute("SELECT COUNT(*) as count FROM forecast_markets WHERE active = 1")
        forecast_count = cursor.fetchone()["count"]
        
        # Recent Trades (Last 10 from trades)
        cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 10")
        recent_trades_raw = [dict(r) for r in cursor.fetchall()]
        
        recent_trades = []
        for t in recent_trades_raw:
            recent_trades.append({
                "timestamp": t["ts"],
                "symbol": t["symbol"],
                "side": t["action"],
                "price": t["price"],
                "qty": t["qty"],
                "strategy": t["strategy"]
            })
        
        conn.close()
        
        return {
            "spot": {
                "equity": crypto_row["buying_power_usd"] + crypto_row["capital_deployed_usd"] if crypto_row else 0.0,
                "pnl_24h": 0.0, # Placeholder unless we add PnL tracking table
                "positions": spot_positions,
            },
            "forecast": {
                "active_markets": forecast_count,
                "positions": [],
            },
            "recent_trades": recent_trades,
            "system": {
                "time": datetime.now().strftime("%H:%M:%S"),
                "status": "OPERATIONAL"
            }
        }
    except Exception as e:
        logging.error(f"DB Snapshot error: {e}")
        return {"error": str(e)}

@app.get("/api/state")
async def get_state():
    return await get_db_snapshot()

async def event_generator() -> AsyncGenerator[str, None]:
    """SSE Generator for real-time updates."""
    while True:
        data = await get_db_snapshot()
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(2) # 2-second heartbeat

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
