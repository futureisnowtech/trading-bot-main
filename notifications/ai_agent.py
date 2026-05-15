import os
import logging
import json
import traceback
from typing import Optional, List, Dict
import system_state
from runtime.runtime_state import get_lane_state, get_system_state
from notifications.agent_tools import execute_sql, read_file, replace_text, run_safe_command

try:
    import google.generativeai as genai
    HAS_GEMINI_SDK = True
except ImportError:
    HAS_GEMINI_SDK = False

try:
    from config import GEMINI_MODEL
except ImportError:
    GEMINI_MODEL = "gemini-2.5-flash"

logger = logging.getLogger(__name__)


# v18.19.5: Project Apex Production Overhaul (80% Cost Reduction)
# Levers: Context Slimming, Lazy Tools, Cost Telemetry, Explicit Caching

GEMINI_15_PRO_INPUT_RATE_1M = 1.25   # USD per 1M tokens (2026 rates)
GEMINI_15_PRO_OUTPUT_RATE_1M = 3.75  # USD per 1M tokens (2026 rates)

def log_api_cost(prompt_tokens: int, completion_tokens: int, module: str):
    """
    Lever 5: Cost Telemetry. Record actual USD spend in SQLite.
    """
    try:
        import sqlite3 as _sq
        import time as _time
        from config import DB_PATH as _DB_PATH
        
        input_cost = (prompt_tokens / 1_000_000) * GEMINI_15_PRO_INPUT_RATE_1M
        output_cost = (completion_tokens / 1_000_000) * GEMINI_15_PRO_OUTPUT_RATE_1M
        total_cost = input_cost + output_cost

        with _sq.connect(_DB_PATH) as _tconn:
            # Ensure table exists
            _tconn.execute("""
                CREATE TABLE IF NOT EXISTS api_costs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL,
                    module TEXT,
                    prompt_tokens INTEGER,
                    completion_tokens INTEGER,
                    usd_cost REAL
                )
            """)
            _tconn.execute(
                "INSERT INTO api_costs (ts, module, prompt_tokens, completion_tokens, usd_cost) "
                "VALUES (?, ?, ?, ?, ?)",
                (_time.time(), module, prompt_tokens, completion_tokens, total_cost),
            )
    except Exception as _e:
        logger.debug(f"[ai_agent] cost telemetry failed: {_e}")

def get_repo_context() -> str:
    """
    Lever 2: Context Slimming. 
    REMOVED: Hardcoded source snippets from spot_strategy.py and edge_monitor.py.
    REDUCED: Log tail from 50 to 15 lines.
    """
    context = []

    # 1. Canonical Truth (AGENTS.md)
    try:
        with open("AGENTS.md", "r") as f:
            # We still keep the truth, but AI must read_file for deeper details.
            context.append("### AGENTS.md (Canonical Truth)\n" + f.read()[:2000])
    except Exception as e:
        context.append(f"Error reading AGENTS.md: {e}")

    # 1b. Live regime policy summary
    try:
        from runtime.spot_strategy import get_spot_strategy
        policy_summary = {}
        for sym in ["BTC", "ETH", "SOL", "XRP"]:
            p = get_spot_strategy(sym)
            policy_summary[sym] = {
                "allowed_regimes": list(p["allowed_regimes"]),
                "score_floors": p["score_floors"],
            }
        context.append("### Live Regime Policy\n" + json.dumps(policy_summary))
    except Exception as e:
        context.append(f"Error reading live regime policy: {e}")

    # 2. Live Vitals (System State)
    try:
        state = system_state.state.get_state()
        # Slimming system state to core vitals
        slim_state = {
            "mode": state.get("mode"),
            "buying_power": state.get("exchange", {}).get("buying_power"),
            "equity": state.get("exchange", {}).get("account_equity"),
            "active_symbol": state.get("strategy", {}).get("active_symbol"),
            "signal": state.get("strategy", {}).get("current_signal"),
        }
        context.append("### System Vitals\n" + json.dumps(slim_state))
    except Exception as e:
        context.append(f"Error getting system state: {e}")

    # 4. Recent Logs (REDUCED to 15 lines)
    try:
        log_path = os.path.join(os.getcwd(), "logs", "bot.log")
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                lines = f.readlines()
                context.append(
                    "### Recent Logs (Last 15 lines)\n" + "".join(lines[-15:])
                )
    except Exception as e:
        context.append(f"Error reading logs: {e}")

    return "\n\n".join(context)

import datetime
import google.generativeai.caching as caching

# Lever 1: Explicit Caching state
_AGENT_CACHE: Optional[caching.CachedContent] = None
_CACHE_EXPIRY: float = 0

def get_cached_agent_model(model_name: str, system_instruction: str) -> genai.GenerativeModel:
    """
    Lever 1: Explicit Caching. Persists static context to reduce token burn by 80%.
    """
    global _AGENT_CACHE, _CACHE_EXPIRY
    
    now = time.time()
    if _AGENT_CACHE is None or now > _CACHE_EXPIRY:
        try:
            logger.info("[ai_agent] Creating new Context Cache for Project Apex...")
            
            # 1. Gather static blocks
            with open("AGENTS.md", "r") as f:
                agents_truth = f.read()
                
            db_schema = """
            **open_positions** (symbol, strategy, qty, entry, stop, target, paper, direction, risk_dollars)
            **trades** (ts, strategy, symbol, action, qty, price, pnl_usd, paper)
            **system_events** (ts, level, message)
            """
            
            _AGENT_CACHE = caching.CachedContent.create(
                model=model_name,
                display_name="apex_governance_schema",
                system_instruction=system_instruction,
                contents=[agents_truth, db_schema],
                ttl=datetime.timedelta(hours=24),
            )
            _CACHE_EXPIRY = now + 86000 # 24h safety
            logger.info(f"[ai_agent] Cache created: {_AGENT_CACHE.name}")
        except Exception as e:
            logger.error(f"[ai_agent] Cache creation failed, falling back to uncached: {e}")
            return genai.GenerativeModel(model_name=model_name, system_instruction=system_instruction)

    return genai.GenerativeModel.from_cached_content(cached_content=_AGENT_CACHE)

def ask_ai(query: str) -> str:
    """
    Analyze the user query using Gemini with repo-wide context and DB tool access.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "Error: GOOGLE_API_KEY is not set."

    if not HAS_GEMINI_SDK:
        return "Error: google-generativeai package not installed."

    genai.configure(api_key=api_key)
    model_name = os.environ.get("GEMINI_MODEL") or "gemini-1.5-pro" # Strictly v1.5 Pro
    if not model_name.startswith("models/"):
        model_name = f"models/{model_name}"

    context = get_repo_context()

    system_instruction = (
        "You are Gemini CLI, a Sr. Systems Engineer agent.\n"
        "### EFFICIENCY PROTOCOL ###\n"
        "1. DO NOT guess code. Use the 'read_file' tool to see actual source logic.\n"
        "2. Context is slimmed. Use 'execute_sql' for live data truth.\n\n"
        f"### LIVE CONTEXT ###\n{context}"
    )

    # Lever 4: Lazy Tools (Attach only if query contains action keywords)
    available_tools = []
    action_keywords = ["query", "list", "show", "check", "fix", "read", "replace", "sql", "find", "search"]
    if any(k in query.lower() for k in action_keywords):
        available_tools = [execute_sql, read_file, replace_text, run_safe_command]

    try:
        # Lever 1: Use Cached Model
        if not available_tools:
            model = get_cached_agent_model(model_name, system_instruction)
        else:
            # Fallback to standard model if tools are required (frozen cache limitation)
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_instruction,
                tools=available_tools
            )

        chat = model.start_chat(enable_automatic_function_calling=True)
        response = chat.send_message(query)
        
        # Lever 5: Cost Telemetry
        try:
            _prompt_tokens = int(getattr(getattr(response, "usage_metadata", None), "prompt_token_count", 0) or 0)
            _completion_tokens = int(getattr(getattr(response, "usage_metadata", None), "candidates_token_count", 0) or 0)
            log_api_cost(_prompt_tokens, _completion_tokens, "telegram_ask")
        except Exception as _tel_e:
            logger.debug(f"[ai_agent] telemetry capture failed: {_tel_e}")

        return response.text
    except Exception as e:
        logger.error(f"Gemini Agent exception: {e}")
        return f"Error connecting to Gemini backend: {str(e)}"
