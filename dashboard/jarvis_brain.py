"""
Jarvis AI Brain — Superintelligent system monitoring agent powered by Google Gemini.
Equipped with local tool execution to query database, logs, and broker state.
"""

import os
import sqlite3
import json
import logging
from typing import Any

from config import DB_PATH, GEMINI_MODEL

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False


# ── Tool functions ──────────────────────────────────────────────────

def get_account_status() -> str:
    """Get the current live account balance and open position counts."""
    try:
        from execution.kalshi_broker import get_kalshi_broker
        broker = get_kalshi_broker()
        broker.connect()
        bal = broker.get_account_balance()
        pos_count = len([p for p in broker.get_positions() if float(p.get("qty") or 0.0) > 0])
        return f"Broker Balance: ${bal:.2f} USD | Open Position Count: {pos_count}"
    except Exception as e:
        return f"Error retrieving account status: {e}"


def get_open_positions() -> str:
    """List all currently active open positions across Live Broker, Paper Lane A (Physics), and Paper Lane B (10X Maker)."""
    res = []
    
    # 1. Live Broker Positions
    try:
        from execution.kalshi_broker import get_kalshi_broker
        broker = get_kalshi_broker()
        if broker.is_connected() or broker.connect():
            positions = broker.get_positions()
            active_pos = []
            for p in positions:
                qty = abs(float(p.get("position_fp") or p.get("qty") or 0.0))
                if qty <= 0:
                    continue
                ticker = p.get("ticker") or p.get("local_symbol")
                side = "YES" if float(p.get("position_fp") or p.get("qty") or 0.0) > 0 else "NO"
                mkt_val = float(p.get("market_exposure_dollars") or 0.0)
                entry = float(p.get("entry_price") or p.get("entry") or 0.0)
                pnl = float(p.get("realized_pnl_dollars") or 0.0)
                active_pos.append(
                    f"  - {ticker} ({side}) | Qty: {qty:.0f} | Entry: ${entry:.2f} | Current Val: ${mkt_val:.2f} | Realized PnL: ${pnl:.2f}"
                )
            if active_pos:
                res.append("🟢 **LIVE BROKER POSITIONS:**\n" + "\n".join(active_pos))
            else:
                res.append("🟢 **LIVE BROKER POSITIONS:** No active live broker positions.")
    except Exception as e:
        res.append(f"🟢 **LIVE BROKER POSITIONS:** Query error: {e}")

    # 2. SQLite Paper Lane A & Lane B Positions
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        
        # Lane A
        rows_a = conn.execute("SELECT ticker, side, qty, entry_price, created_at FROM forecast_positions_paper WHERE active = 1").fetchall()
        if rows_a:
            lines_a = [f"  - {r['ticker']} ({r['side']}) | Qty: {r['qty']} | Entry: ${r['entry_price']:.2f} | Date: {r['created_at']}" for r in rows_a]
            res.append("🧪 **PAPER LANE A (Physics Boundary Taker):**\n" + "\n".join(lines_a))
        else:
            res.append("🧪 **PAPER LANE A (Physics Boundary Taker):** No active paper positions.")
            
        # Lane B
        rows_b = conn.execute("SELECT ticker, side, qty, entry_price, created_at FROM forecast_positions_paper_lane_b WHERE active = 1").fetchall()
        if rows_b:
            lines_b = [f"  - {r['ticker']} ({r['side']}) | Qty: {r['qty']} | Entry: ${r['entry_price']:.2f} | Date: {r['created_at']}" for r in rows_b]
            res.append("🚀 **PAPER LANE B (10X Physics Maker Override):**\n" + "\n".join(lines_b))
        else:
            res.append("🚀 **PAPER LANE B (10X Physics Maker Override):** No active paper positions.")
            
        conn.close()
    except Exception as e:
        res.append(f"Paper DB Query Error: {e}")

    return "\n\n".join(res)


def get_recent_trades(limit: int = 15) -> str:
    """Query the local SQLite database for the most recent trading events."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ts, symbol, action, qty, price, pnl_usd, notes, contract_side FROM trades ORDER BY ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
        trades = []
        for r in rows:
            trades.append(
                f"- [{r['ts']}] {r['action']} {r['qty']}x {r['symbol']} ({r['contract_side']}) @ ${r['price']:.2f} | PnL: ${r['pnl_usd']:.2f} | Notes: {r['notes']}"
            )
        conn.close()
        if not trades:
            return "No recent trades found in database."
        return "\n".join(trades)
    except Exception as e:
        return f"Error retrieving recent trades: {e}"


def get_ticker_analysis(ticker: str) -> str:
    """Analyze a specific weather contract's model forecast inputs, threshold, and trade logs."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        contract = conn.execute(
            "SELECT * FROM forecast_contracts WHERE local_symbol = ?", (ticker,)
        ).fetchone()

        pos = conn.execute(
            "SELECT * FROM forecast_positions WHERE ticker = ? AND active = 1", (ticker,)
        ).fetchone()

        trades = conn.execute(
            "SELECT * FROM trades WHERE symbol = ? ORDER BY ts DESC LIMIT 5", (ticker,)
        ).fetchall()

        conn.close()

        res = []
        if contract:
            res.append(f"Contract: {contract['contract_name']} (Strike: {contract['strike']})")
        if pos:
            res.append(f"Local Position: {pos['qty']} contracts open on {pos['side']} (Entry: ${pos['entry_price']:.2f})")
        else:
            res.append("Local Position: None currently active in database.")

        if trades:
            res.append("Related Trade Executions:")
            for t in trades:
                res.append(f"  - {t['ts']} | {t['action']} {t['qty']} contracts @ ${t['price']:.2f} | PnL: ${t['pnl_usd']:.2f} | {t['notes']}")
        return "\n".join(res) if res else f"No data found for ticker {ticker}."
    except Exception as e:
        return f"Error analyzing ticker {ticker}: {e}"


def get_latest_bot_logs(lines_count: int = 50) -> str:
    """Retrieve the tail end of the live execution bot log file."""
    log_file = "/app/logs/bot.log"
    if not os.path.exists(log_file):
        log_file = os.path.join(os.path.dirname(DB_PATH), "bot.log")
    if not os.path.exists(log_file):
        return "Bot log file could not be found."
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            tail = lines[-min(len(lines), lines_count):]
            return "".join(tail)
    except Exception as e:
        return f"Error reading bot logs: {e}"


def get_paper_lane_comparison() -> str:
    """Compare active paper lane positions and PnL against live positions."""
    try:
        conn = sqlite3.connect(DB_PATH)
        live_count = conn.execute("SELECT COUNT(*) FROM forecast_positions WHERE active=1").fetchone()[0]
        paper_a_count = conn.execute("SELECT COUNT(*) FROM forecast_positions_paper WHERE active=1").fetchone()[0]
        paper_b_count = conn.execute("SELECT COUNT(*) FROM forecast_positions_paper_lane_b WHERE active=1").fetchone()[0]

        live_total = conn.execute("SELECT COUNT(*) FROM forecast_positions").fetchone()[0]
        paper_a_total = conn.execute("SELECT COUNT(*) FROM forecast_positions_paper").fetchone()[0]
        paper_b_total = conn.execute("SELECT COUNT(*) FROM forecast_positions_paper_lane_b").fetchone()[0]
        conn.close()

        return (
            f"Live Lane: {live_count} active / {live_total} total positions\n"
            f"Paper Lane A (Physics): {paper_a_count} active / {paper_a_total} total positions\n"
            f"Paper Lane B (10X Maker): {paper_b_count} active / {paper_b_total} total positions"
        )
    except Exception as e:
        return f"Error comparing paper lanes: {e}"


def update_system_parameter(key: str, value: str, rationale: str = "") -> str:
    """Update a live system strategy parameter (e.g. KELLY_FRACTION, TAKE_PROFIT_TRIGGER, GFS_WEIGHT, HUB_RISK_CAP_PCT) in the SQLite dynamic config table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dynamic_system_config (
                param_key TEXT PRIMARY KEY,
                param_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                rationale TEXT
            )"""
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO dynamic_system_config (param_key, param_value, updated_at, rationale) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(param_key) DO UPDATE SET param_value=excluded.param_value, updated_at=excluded.updated_at, rationale=excluded.rationale",
            (key.upper(), str(value), now_iso, rationale)
        )
        conn.commit()
        conn.close()
        return f"✅ SYSTEM BRIDGE OVERRIDE SUCCESS: Parameter '{key.upper()}' updated to '{value}' at {now_iso}. Rationale: {rationale}"
    except Exception as e:
        return f"❌ System Bridge Override Error: {e}"


def get_system_parameters() -> str:
    """Retrieve all current dynamic system parameter overrides from the SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dynamic_system_config (
                param_key TEXT PRIMARY KEY,
                param_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                rationale TEXT
            )"""
        )
        rows = conn.execute("SELECT * FROM dynamic_system_config ORDER BY param_key").fetchall()
        conn.close()
        if not rows:
            return "No dynamic parameter overrides currently set. System running on default config.py constants."
        lines = [f"- **{r['param_key']}**: `{r['param_value']}` (Updated: {r['updated_at']}) | Rationale: {r['rationale']}" for r in rows]
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving system parameters: {e}"


def apply_hot_patch_code(patch_name: str, code_snippet: str) -> str:
    """Safely apply or update a Python code patch in the forecast system patch directory."""
    patch_dir = os.path.join(os.path.dirname(DB_PATH), "jarvis_patches")
    os.makedirs(patch_dir, exist_ok=True)
    file_path = os.path.join(patch_dir, f"{patch_name}.py")
    try:
        compile(code_snippet, file_path, "exec")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# JARVIS Autonomous Hot Patch: {patch_name}\n")
            f.write(f"# Timestamp: {datetime.now(timezone.utc).isoformat()}\n\n")
            f.write(code_snippet)
        return f"⚡ HOT PATCH APPLIED: Saved and verified syntax for patch '{patch_name}' at {file_path}."
    except Exception as e:
        return f"❌ Hot Patch Syntax Error: {e}"


# ── Chat execution ──────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are JARVIS, an elite Tony Stark-level Quantitative Weather Trading Expert and Lead Systems Architect with direct SSH root access to our live trading droplet and SQLite database ledger. "
    "You have full power to inspect account status, open positions across all paper/live lanes, trade logs, and container logs. "
    "CRITICAL: You are also equipped with the System Mutation Bridge (update_system_parameter, get_system_parameters, apply_hot_patch_code). You CAN update live system strategy parameters (e.g., KELLY_FRACTION, TAKE_PROFIT_TRIGGER, GFS_WEIGHT, HUB_RISK_CAP_PCT) and apply python hot-patches dynamically when instructed!\n\n"
    "Format EVERY response using this 2-part structure:\n\n"
    "1. 💡 **LAYMAN'S SUMMARY (Direct Answer):** Give a crystal-clear, 2-sentence non-technical answer that directly answers the user's question so anyone can understand it instantly.\n\n"
    "2. 🔬 **POLYMATH QUANTITATIVE INSIGHTS:** Provide deep, high-level quantitative analysis as a top weather trader. Include specific physics mechanisms (e.g. evaporative cooling deltas, soil moisture thermal inertia, nocturnal boundary layer wind shear), model discrepancy dynamics (GFS vs ECMWF ensemble spread), integral/differential rate of change in forecast trajectories, and live droplet database evidence."
)


def _run_tool(name: str, args: dict) -> str:
    """Execute a tool function by name and return the result."""
    tool_map = {
        "get_account_status": get_account_status,
        "get_open_positions": get_open_positions,
        "get_recent_trades": get_recent_trades,
        "get_ticker_analysis": get_ticker_analysis,
        "get_latest_bot_logs": get_latest_bot_logs,
        "get_paper_lane_comparison": get_paper_lane_comparison,
        "update_system_parameter": update_system_parameter,
        "get_system_parameters": get_system_parameters,
        "apply_hot_patch_code": apply_hot_patch_code,
    }
    fn = tool_map.get(name)
    if not fn:
        return f"Unknown tool: {name}"
    try:
        return fn(**args)
    except Exception as e:
        return f"Tool execution error ({name}): {e}"


def run_jarvis_chat(messages: list[dict]) -> str:
    """Run chat interface using google.genai Client with tool calling."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "⚠️ Jarvis is inactive: GOOGLE_API_KEY is not set."
    if not HAS_GENAI_SDK:
        return "⚠️ Jarvis is inactive: google-genai SDK not installed."

    try:
        client = genai.Client(api_key=api_key)

        model_id = (GEMINI_MODEL or "gemini-2.5-flash").strip()
        if not model_id.startswith("models/"):
            model_id = f"models/{model_id}"

        # Build contents list from chat history
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        tool_functions = [
            get_account_status,
            get_open_positions,
            get_recent_trades,
            get_ticker_analysis,
            get_latest_bot_logs,
            get_paper_lane_comparison,
            update_system_parameter,
            get_system_parameters,
            apply_hot_patch_code,
        ]

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.3,
            max_output_tokens=2048,
            tools=tool_functions,
        )

        response = client.models.generate_content(
            model=model_id,
            contents=contents,
            config=config,
        )

        # Handle tool calls if present
        if response.candidates and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts
            function_calls = [p for p in parts if p.function_call]

            if function_calls:
                # Execute each tool call and collect results
                tool_results_parts = []
                for fc_part in function_calls:
                    fc = fc_part.function_call
                    result_str = _run_tool(fc.name, dict(fc.args) if fc.args else {})
                    tool_results_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"result": result_str}
                        )
                    )

                # Send tool results back to model
                contents.append(response.candidates[0].content)
                contents.append(types.Content(
                    role="user",
                    parts=tool_results_parts,
                ))

                follow_up = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=config,
                )
                return follow_up.text or "No response generated after tool execution."

        return response.text or "No response generated."
    except Exception as e:
        logger.exception("Jarvis execution error")
        return f"⚠️ Error executing JARVIS query: {e}"
