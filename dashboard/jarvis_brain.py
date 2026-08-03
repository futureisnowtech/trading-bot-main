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
    """List all currently active open positions with entry prices, market values, and side."""
    try:
        from execution.kalshi_broker import get_kalshi_broker
        broker = get_kalshi_broker()
        broker.connect()
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
                f"- {ticker} ({side}) | Qty: {qty:.2f} | Entry: ${entry:.2f} | Current Value: ${mkt_val:.2f} | Realized PnL: ${pnl:.2f}"
            )
        if not active_pos:
            return "No active positions currently held at the broker."
        return "\n".join(active_pos)
    except Exception as e:
        return f"Error retrieving open positions: {e}"


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


# ── Chat execution ──────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are JARVIS, an advanced, highly specialized Iron Man style AI assistant for the Sovereign Weather Engine. "
    "You have access to live database queries, trading logs, and broker position operations. "
    "Always be precise, technical, and analytical. Use your tools whenever the user asks about positions, "
    "trades, logs, balance, or system state. Explain weather dynamics and model discrepancies (GFS vs ECMWF) "
    "with absolute engineering clarity. When asked about paper trading performance, use the get_paper_lane_comparison tool."
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
