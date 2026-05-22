import os
import logging
import json
import traceback
import time
import datetime
from typing import Optional, List, Dict
import system_state
from runtime.runtime_state import get_lane_state, get_system_state
from notifications import agent_tools

try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False

try:
    from config import GEMINI_MODEL
except ImportError:
    GEMINI_MODEL = "gemini-2.5-flash"

logger = logging.getLogger(__name__)


# v18.30: Sovereign SDK Overhaul (Legacy SDK Excised)
# Levers: google-genai (2026 Standard), Context Slimming, Cost Telemetry

GEMINI_25_FLASH_INPUT_RATE_1M = 0.10   # USD per 1M tokens (2026 rates)
GEMINI_25_FLASH_OUTPUT_RATE_1M = 0.30  # USD per 1M tokens (2026 rates)

def log_api_cost(prompt_tokens: int, completion_tokens: int, module: str):
    """
    Lever 5: Cost Telemetry. Record actual USD spend in SQLite.
    """
    try:
        import sqlite3 as _sq
        from config import DB_PATH as _DB_PATH
        
        input_cost = (prompt_tokens / 1_000_000) * GEMINI_25_FLASH_INPUT_RATE_1M
        output_cost = (completion_tokens / 1_000_000) * GEMINI_25_FLASH_OUTPUT_RATE_1M
        total_cost = input_cost + output_cost

        with _sq.connect(_DB_PATH) as _tconn:
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
            # Migration: Ensure module column exists
            try:
                _tconn.execute("ALTER TABLE api_costs ADD COLUMN module TEXT")
            except _sq.OperationalError:
                pass # Already exists
            
            _tconn.execute(
                "INSERT INTO api_costs (ts, module, prompt_tokens, completion_tokens, usd_cost) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), module, prompt_tokens, completion_tokens, total_cost),
            )
    except Exception as _e:
        logger.debug(f"[ai_agent] cost telemetry failed: {_e}")

def get_repo_context() -> str:
    """
    Lever 2: Context Slimming. 
    """
    context = []

    # 1. Canonical Truth (AGENTS.md)
    try:
        with open("AGENTS.md", "r") as f:
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

    # 4. Recent Logs (Last 15 lines) - Optimized Tail
    try:
        log_path = os.path.join(os.getcwd(), "logs", "bot.log")
        if os.path.exists(log_path):
            with open(log_path, "rb") as f:
                try:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    # Read last 8KB to be safe
                    f.seek(max(0, size - 8192))
                    chunk = f.read().decode("utf-8", errors="ignore")
                    lines = chunk.splitlines()
                    context.append(
                        "### Recent Logs (Last 15 lines)\n" + "\n".join(lines[-15:])
                    )
                except Exception:
                    # Fallback for very small files or seek errors
                    f.seek(0)
                    lines = f.read().decode("utf-8", errors="ignore").splitlines()
                    context.append(
                        "### Recent Logs (Last 15 lines)\n" + "\n".join(lines[-15:])
                    )
    except Exception as e:
        context.append(f"Error reading logs: {e}")

    return "\n\n".join(context)

# Lever 1: Explicit Caching state (v18.30)
_AGENT_CACHE_ID: Optional[str] = None
_CACHE_EXPIRY: float = 0

def get_cached_content_id(client: "genai.Client", model_id: str, system_instruction: str) -> Optional[str]:
    """
    Lever 1: Explicit Caching using google-genai SDK.
    """
    global _AGENT_CACHE_ID, _CACHE_EXPIRY
    
    now = time.time()
    if _AGENT_CACHE_ID is None or now > _CACHE_EXPIRY:
        try:
            logger.info("[ai_agent] Creating new Context Cache for Project Apex...")
            
            with open("AGENTS.md", "r") as f:
                agents_truth = f.read()
                
            db_schema = """
            **open_positions** (symbol, strategy, qty, entry, stop, target, paper, direction, risk_dollars)
            **trades** (ts, strategy, symbol, action, qty, price, pnl_usd, paper)
            **system_events** (ts, level, message)
            """
            
            # 2026: google-genai caching
            cache = client.caches.create(
                model=model_id,
                config={
                    'display_name': 'apex_governance_schema',
                    'system_instruction': system_instruction,
                    'contents': [agents_truth, db_schema],
                    'ttl': '86400s',
                }
            )
            _AGENT_CACHE_ID = cache.name
            _CACHE_EXPIRY = now + 86000 # 24h safety
            logger.info(f"[ai_agent] Cache created: {_AGENT_CACHE_ID}")
        except Exception as e:
            logger.error(f"[ai_agent] Cache creation failed: {e}")
            return None

    return _AGENT_CACHE_ID

# AFC Wrapper Functions to resolve SDK inspection issues in 2026
def execute_sql(query: str) -> str:
    """Safe, read-only SQL execution for the AI agent."""
    return agent_tools.execute_sql(query)

def read_file(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Reads a file from the repository."""
    return agent_tools.read_file(file_path, start_line, end_line)

def replace_text(file_path: str, old_string: str, new_string: str) -> str:
    """Surgically replaces text in a file."""
    return agent_tools.replace_text(file_path, old_string, new_string)

def run_safe_command(command: str) -> str:
    """Runs restricted shell commands."""
    return agent_tools.run_safe_command(command)

def ask_ai(query: str) -> str:
    """
    Analyze the user query using Gemini (google-genai SDK).
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "Error: GOOGLE_API_KEY is not set."

    if not HAS_GENAI_SDK:
        return "Error: google-genai package not installed."

    try:
        client = genai.Client(api_key=api_key)
        model_id = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
        
        context = get_repo_context()
        system_instruction = (
            "You are Gemini CLI, a Sr. Systems Engineer agent.\n"
            "### CORE PROTOCOL ###\n"
            "1. PROACTIVE REPORTING: When asked for data (scans, trades, logs), you MUST use tools to read the data AND provide the summary in the SAME TURN. Never tell the user you will 'report back' or 'review and follow up'. Report NOW.\n"
            "2. TOOL USE: Use 'execute_sql' for live trade data and 'read_file' for scan logs. Analyze the content immediately.\n"
            "3. NO GUESSING: If you don't see the data, state it clearly. Do not hallucinate content.\n\n"
            f"### LIVE CONTEXT ###\n{context}"
        )

        available_tools = []
        action_keywords = ["query", "list", "show", "check", "fix", "read", "replace", "sql", "find", "search"]
        if any(k in query.lower() for k in action_keywords):
            # Use wrappers to avoid SDK-level inspection errors
            available_tools = [execute_sql, read_file, replace_text, run_safe_command]

        config_dict = {
            'system_instruction': system_instruction,
        }

        if available_tools:
            config_dict['tools'] = available_tools
            config_dict['automatic_function_calling'] = {'disable': False}
        else:
            # Try to use cache for pure research calls
            cache_id = get_cached_content_id(client, model_id, system_instruction)
            if cache_id:
                config_dict['cached_content'] = cache_id
                # system_instruction is already in the cache
                del config_dict['system_instruction']

        chat = client.chats.create(model=model_id, config=config_dict)
        response = chat.send_message(query)
        
        # Forensic Telemetry Interceptor (v18.34)
        try:
            import sqlite3 as _sq
            from config import DB_PATH as _DB_PATH
            _usage = getattr(response, "usage_metadata", None)
            _p_tok = int(getattr(_usage, "prompt_token_count", 0) or 0)
            _c_tok = int(getattr(_usage, "candidates_token_count", 0) or 0)
            
            if _p_tok > 0 or _c_tok > 0:
                with _sq.connect(_DB_PATH, timeout=30.0) as _tconn:
                    _tconn.execute(
                        "INSERT INTO api_telemetry (ts, module, prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?)",
                        (time.time(), "telegram_ask", _p_tok, _c_tok)
                    )
        except Exception as _tel_err:
            logger.debug(f"[ai_agent] Telemetry capture failure: {_tel_err}")

        # Legacy Cost Telemetry
        try:
            usage = response.usage_metadata
            if usage:
                log_api_cost(usage.prompt_token_count, usage.candidates_token_count, "telegram_ask")
        except Exception as _tel_e:
            logger.debug(f"[ai_agent] cost telemetry failed: {_tel_e}")

        return response.text
    except Exception as e:
        logger.error(f"Gemini Agent exception: {e}")
        return f"Sovereign Audit: Handshake Error. Resolve via logs. Error: {str(e)}"
