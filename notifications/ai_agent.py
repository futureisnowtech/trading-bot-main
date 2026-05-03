import os
import logging
import requests
import json
import sqlite3
import traceback
from typing import Optional
import system_state
from runtime.runtime_state import get_lane_state, get_system_state

try:
    from config import CLAUDE_MODEL
except ImportError:
    CLAUDE_MODEL = "claude-3-5-sonnet-latest"

logger = logging.getLogger(__name__)

def get_repo_context() -> str:
    """
    Gather canonical context from the repo for the AI agent.
    """
    context = []
    
    # 1. Canonical Truth (AGENTS.md)
    try:
        with open("AGENTS.md", "r") as f:
            context.append("### AGENTS.md (Canonical Truth)\n" + f.read()[:5000])
    except Exception as e:
        context.append(f"Error reading AGENTS.md: {e}")

    # 2. Live Vitals (System State)
    try:
        state = system_state.state.get_state()
        context.append("### Live Vitals (system_state)\n" + json.dumps(state, indent=2))
    except Exception as e:
        context.append(f"Error getting system state: {e}")

    # 3. Lane Vitals (Runtime State)
    try:
        crypto_lane = get_lane_state("crypto") or {}
        context.append("### Crypto Lane State (runtime_state)\n" + json.dumps(crypto_lane, indent=2))
    except Exception as e:
        context.append(f"Error getting lane state: {e}")

    # 4. Recent Logs (Last 50 lines)
    try:
        log_path = os.path.join(os.getcwd(), "logs", "bot.log")
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                lines = f.readlines()
                context.append("### Recent Logs (Last 50 lines)\n" + "".join(lines[-50:]))
    except Exception as e:
        context.append(f"Error reading logs: {e}")

    # 5. Recent System Events (Database)
    try:
        db_path = os.path.join(os.getcwd(), "logs", "trades.db")
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ts, level, message FROM system_events ORDER BY ts DESC LIMIT 5"
            ).fetchall()
            events = [dict(r) for r in rows]
            context.append("### Recent System Events\n" + json.dumps(events, indent=2))
    except Exception as e:
        context.append(f"Error reading system events: {e}")

    return "\n\n".join(context)

def ask_ai(query: str) -> str:
    """
    Analyze the user query using Claude with repo-wide context.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "Error: ANTHROPIC_API_KEY is not set."

    model = os.environ.get("CLAUDE_MODEL") or CLAUDE_MODEL
    if model == "claude-sonnet-4-6": # Handle placeholder in config.py
        model = "claude-3-5-sonnet-latest"
    
    context = get_repo_context()
    
    prompt = (
        "You are Gemini CLI, an AI agent inside a sophisticated algo-trading system.\n"
        "Your goal is to provide deep repo-intelligence based on live data.\n\n"
        f"### CONTEXT ###\n{context}\n\n"
        f"### USER QUERY ###\n{query}\n\n"
        "Analyze the context and answer the query. Be professional, concise, and senior.\n"
        "If suggesting actions, adhere to the 'Spot Truth-Lane Contract'."
    )

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    data = {
        "model": model,
        "max_tokens": 1000,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code != 200:
            error_data = response.json()
            msg = error_data.get("error", {}).get("message", "Unknown error")
            logger.error(f"AI Backend Error ({response.status_code}): {msg}")
            return f"AI Backend Error ({response.status_code}): {msg}"
            
        result = response.json()
        return result["content"][0]["text"]
    except Exception as e:
        logger.error(f"AI Agent exception: {e}")
        return f"Error connecting to AI backend: {str(e)}"
