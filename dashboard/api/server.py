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
        
        # 3. Active Spot Positions (with Live PnL & SRE X-Ray)
        spot_positions = []
        try:
            from execution.coinbase_spot_broker import get_spot_broker
            spot_broker = get_spot_broker()
            cursor.execute("SELECT * FROM open_positions WHERE qty > 0")
            for p in cursor.fetchall():
                p_dict = dict(p)
                sym = p_dict["symbol"]
                entry = p_dict["entry"]
                qty = p_dict["qty"]
                stop = p_dict.get("stop", 0.0)
                
                # Fetch live price for PnL
                try:
                    top = spot_broker.get_spot_top_of_book(sym)
                    current_price = float(top.get("best_bid") or entry)
                except:
                    current_price = entry
                
                live_pnl = (current_price - entry) * qty if current_price > 0 else 0.0
                risk_usd = (entry - stop) * qty if stop > 0 else 0.0

                # SRE X-Ray: Hold Conviction
                conviction_score = 50.0
                hold_rationale = "Neutral technical structure."
                try:
                    cursor.execute("SELECT final_spot_score FROM scan_candidates WHERE symbol=? ORDER BY ts DESC LIMIT 1", (sym,))
                    score_row = cursor.fetchone()
                    if score_row:
                        conviction_score = float(score_row[0] or 50.0)
                        if conviction_score > 60: hold_rationale = "Strong trend structure. High hold conviction."
                        elif conviction_score > 52: hold_rationale = "Momentum positive. Trend intact."
                        elif conviction_score < 48: hold_rationale = "Momentum decaying. Near thesis_decay threshold."
                        else: hold_rationale = "Consolidating. Monitoring for trend resumption."
                except: pass

                spot_positions.append({
                    "symbol": sym,
                    "qty": qty,
                    "entry_price": entry,
                    "current_price": current_price,
                    "live_pnl": live_pnl,
                    "risk_usd": risk_usd,
                    "conviction_score": conviction_score,
                    "hold_rationale": hold_rationale,
                    "stop": stop,
                    "target": p_dict.get("target", 0.0),
                    "strategy": p_dict.get("strategy", ""),
                    "reason": p_dict.get("entry_reason", "")
                })
        except Exception as e: 
            logging.debug(f"Spot PnL/X-Ray error: {e}")
        
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
                    
                    raw_positions = kb.get_positions()
                    for p in raw_positions:
                        # Enrich with DB metadata if available
                        try:
                            # Search by local_symbol and right
                            right = 'C' if p['side'] == 'YES' else 'P'
                            cursor.execute(
                                "SELECT entry, stop, ts_entry FROM open_positions WHERE symbol=? AND strategy LIKE 'forecast_%' ORDER BY ts_entry DESC LIMIT 1",
                                (p['local_symbol'],)
                            )
                            db_row = cursor.fetchone()
                            if db_row:
                                p['entry_price'] = float(db_row['entry'] or p['entry_price'])
                                p['stop'] = float(db_row['stop'] or 0.0)
                                p['ts_entry'] = db_row['ts_entry']
                        except: pass
                    forecast_positions = raw_positions
        except Exception as e:
            logging.error(f"Kalshi Sync Error: {e}")

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
        
        # 9. System Events & Forecast Evaluations & Health Audit
        events = []
        forecast_evaluations = []
        error_count = 0
        try:
            cursor.execute("SELECT * FROM system_events ORDER BY ts DESC LIMIT 100")
            rows = cursor.fetchall()
            for r in rows:
                msg = r["message"]
                level = r["level"]
                evt = {"ts": r["ts"], "level": level, "source": r["source"], "message": msg}
                if len(events) < 20: events.append(evt)
                
                if level in ("ERROR", "FATAL"):
                    error_count += 1
                
                # RC: Capture Forecast evaluations specifically for X-Ray
                msg_lower = msg.lower()
                if r["source"] == "ForecastRunner" and ("veto" in msg_lower or "entry" in msg_lower):
                    forecast_evaluations.append(evt)
        except sqlite3.OperationalError: pass

        # SRE: Data Integrity Score
        integrity_score = max(0, 100 - (error_count * 5))

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
            # Deduplicate by ensuring fc.right='C' (YES)
            cursor.execute("""
                SELECT fm.market_name, fc.local_symbol, fc.strike, 
                       (SELECT implied_prob FROM forecast_quotes fq WHERE fq.contract_id = fc.id ORDER BY ts DESC LIMIT 1) as prob
                FROM forecast_contracts fc
                JOIN forecast_markets fm ON fm.id = fc.market_id
                WHERE fc.active = 1 AND fc.right = 'C'
                ORDER BY fc.last_seen_at DESC
                LIMIT 10
            """)
            for r in cursor.fetchall():
                prob_val = r["prob"]
                if prob_val is None:
                    # v18.34: -1.0 for missing, rather than misleading 0%
                    prob_val = -1.0
                macro_radar.append({
                    "event": r["market_name"],
                    "symbol": r["local_symbol"],
                    "probability": round(float(prob_val) * 100, 1) if prob_val >= 0 else -1.0
                })
        except sqlite3.OperationalError: pass

        # 13. Strategy Edge Heatmap
        edge_heatmap = []
        try:
            cursor.execute("""
                SELECT strategy, SUM(pnl_usd) as pnl, COUNT(*) as trades, 
                       SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) as wins
                FROM trades 
                WHERE ts > datetime('now', '-7 days')
                GROUP BY strategy
            """)
            for r in cursor.fetchall():
                trades = r["trades"]
                wins = r["wins"]
                edge_heatmap.append({
                    "strategy": r["strategy"],
                    "pnl": round(float(r["pnl"] or 0.0), 2),
                    "win_rate": round((wins / trades) * 100, 1) if trades > 0 else 0.0
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
                "radar": macro_radar,
                "evaluations": forecast_evaluations
            },
            "live_hunt": live_hunt,
            "intelligence_summary": summary,
            "recent_trades": recent_trades,
            "events": events,
            "vitals": vitals,
            "sre": {
                "integrity_score": integrity_score,
                "edge_heatmap": edge_heatmap
            },
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

@app.post("/api/oracle")
async def ask_oracle(request: Request):
    """Bridge to Gemini for deep forensic analysis of the live system snapshot."""
    try:
        from config import GOOGLE_API_KEY
        if not GOOGLE_API_KEY:
            return {"answer": "Oracle Error: GOOGLE_API_KEY not configured in .env"}

        import google.generativeai as genai
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash')

        body = await request.json()
        user_query = body.get("question", "Summarize current system state.")
        
        # 1. Get full live context
        snapshot = await get_db_snapshot()
        
        # 2. Build Forensic Prompt
        system_prompt = (
            "You are the Sovereign Oracle, an elite SRE and Quantitative Trading Architect. "
            "You have full visibility into the bot's internal pipes. "
            "Analyze the following JSON snapshot and answer the user's question with expert, "
            "insightful, yet layman-friendly terms. Identify risks, explain rejections, and "
            "summarize PnL performance proactively.\n\n"
            f"LIVE SNAPSHOT: {json.dumps(snapshot)}\n\n"
            f"USER QUESTION: {user_query}"
        )

        response = await asyncio.to_thread(model.generate_content, system_prompt)
        return {"answer": response.text}
    except Exception as e:
        logging.error(f"Oracle Error: {e}")
        return {"answer": f"Oracle Error: {str(e)}"}

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
