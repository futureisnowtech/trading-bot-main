"""
Jarvis AI Brain — Superintelligent system monitoring agent powered by Google Gemini.
Equipped with local tool execution to query database, logs, and broker state.
"""

import os
import sqlite3
import json
import logging
from typing import Any, dict, list
import google.generativeai as genai
from config import DB_PATH

logger = logging.getLogger(__name__)

# Initialize Gemini Client
api_key = os.getenv("GOOGLE_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

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
            side = "YES" if float(p.get("position_fp" or p.get("qty") or 0.0)) > 0 else "NO"
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
        
        # Check contract details
        contract = conn.execute(
            "SELECT * FROM forecast_contracts WHERE local_symbol = ?", (ticker,)
        ).fetchone()
        
        # Check position details
        pos = conn.execute(
            "SELECT * FROM forecast_positions WHERE ticker = ? AND active = 1", (ticker,)
        ).fetchone()
        
        # Check recent trade logs
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
        return "\n".join(res)
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

def flatten_position(ticker: str, side: str) -> str:
    """Manually exit and flatten a position on Kalshi and update database ledger."""
    try:
        from execution.kalshi_broker import get_kalshi_broker
        from forecast.db import mark_forecast_position_closed
        
        broker = get_kalshi_broker()
        broker.connect()
        right = "C" if side.upper() == "YES" else "P"
        
        # Sync positions first to make sure we know current qty
        broker.sync_positions()
        key = f"{ticker}_{right}"
        pos_info = broker._open_positions.get(key)
        if not pos_info:
            return f"Position for {ticker} ({side}) not found open at the broker."
            
        qty = int(round(float(pos_info.get("qty") or 0.0)))
        if qty <= 0:
            return f"No open contracts to close for {ticker}."
            
        flatten_res = broker.flatten_position(ticker, right, qty)
        mark_forecast_position_closed(ticker, exit_type="manual_jarvis_exit")
        return f"Flatten command sent: {flatten_res}"
    except Exception as e:
        return f"Error flattening position: {e}"

def run_jarvis_chat(messages: list[dict]) -> str:
    """Run chat interface using Gemini with functions enabled."""
    if not api_key:
        return "Jarvis Chat is inactive: GOOGLE_API_KEY environment variable is not set."
        
    try:
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=(
                "You are JARVIS, an advanced, highly specialized Iron Man style AI assistant for the Sovereign Weather Engine. "
                "You have access to live database queries, trading logs, and broker position operations. "
                "Always be precise, technical, and analytical. Use your tools whenever the user asks about positions, "
                "trades, logs, balance, or system state. Explain weather dynamics and model discrepancies (GFS vs ECMWF) "
                "with absolute engineering clarity. If the user asks to flatten/exit a contract, call the flatten_position tool."
            ),
            tools=[
                get_account_status,
                get_open_positions,
                get_recent_trades,
                get_ticker_analysis,
                get_latest_bot_logs,
                flatten_position
            ]
        )
        
        # Prepare history formatted for Gemini SDK
        chat = model.start_chat(enable_automatic_function_calling=True)
        response = None
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                response = chat.send_message(content)
                
        if response:
            return response.text
        return "No response generated."
    except Exception as e:
        logger.error("Jarvis execution error: %s", e)
        return f"Error executing JARVIS query: {e}"
