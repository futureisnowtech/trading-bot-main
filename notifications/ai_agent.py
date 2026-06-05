import os
import logging
import json
import time
from typing import Optional, List, Dict
from notifications import agent_tools
from config import GEMINI_MODEL

try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False

# Oracle model follows the repo-wide Gemini config by default.
GEMINI_REASONING_MODEL = os.getenv("GEMINI_REASONING_MODEL", GEMINI_MODEL).strip() or GEMINI_MODEL

logger = logging.getLogger(__name__)


def get_reasoning_model_id() -> str:
    model = (GEMINI_REASONING_MODEL or GEMINI_MODEL or "").strip()
    if not model:
        model = "gemini-2.5-flash"
    if model.startswith("models/"):
        return model
    return f"models/{model}"

def get_repo_context() -> str:
    """
    Builds a rich context for the AI, including the filesystem layout and SQL schema.
    """
    context = []

    # 1. Canonical Truth (AGENTS.md)
    try:
        with open("AGENTS.md", "r") as f:
            context.append("### AGENTS.md (System Architecture)\n" + f.read())
    except Exception: pass

    # 2. Database Schema
    db_schema = """
### DATABASE SCHEMA (logs/trades.db)
- **forecast_positions**: ticker (TEXT), qty (REAL), entry_price (REAL), side (TEXT), active (INT), opened_at (TEXT)
- **trades**: ts (TEXT), strategy (TEXT), symbol (TEXT), action (TEXT), qty (REAL), price (REAL), pnl_usd (REAL), broker (TEXT)
- **system_events**: ts (TEXT), level (TEXT), source (TEXT), message (TEXT)
- **api_costs**: ts (REAL), module (TEXT), prompt_tokens (INT), completion_tokens (INT), usd_cost (REAL)
- **forecast_markets**: market_symbol (TEXT), market_name (TEXT), active (INT)
    """
    context.append(db_schema)

    # 3. Broker-first live truth
    try:
        live_status = json.loads(agent_tools.get_live_kalshi_status())
        slim_truth = {
            "broker_connected": live_status.get("broker_connected"),
            "balance_usd": live_status.get("balance_usd"),
            "active_markets": live_status.get("active_markets"),
            "broker_positions_count": live_status.get("broker_positions_count"),
            "db_positions_count": live_status.get("db_positions_count"),
            "position_drift": live_status.get("position_drift", {}).get("has_drift"),
            "forecast_lane": live_status.get("forecast_lane", {}),
        }
        context.append("### LIVE OPERATOR TRUTH\n" + json.dumps(slim_truth, indent=2))
    except Exception:
        pass

    # 4. Recent veto pattern
    try:
        veto_summary = json.loads(agent_tools.get_recent_veto_summary())
        context.append("### RECENT VETO SUMMARY\n" + json.dumps(veto_summary, indent=2))
    except Exception:
        pass

    return "\n\n".join(context)

def execute_sql(query: str) -> str: return agent_tools.execute_sql(query)
def read_file(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str: return agent_tools.read_file(file_path, start_line, end_line)
def list_files(dir_path: str = ".") -> str: return agent_tools.list_files(dir_path)
def replace_text(file_path: str, old_string: str, new_string: str) -> str: return agent_tools.replace_text(file_path, old_string, new_string)
def run_safe_command(command: str) -> str: return agent_tools.run_safe_command(command)
def get_live_kalshi_status() -> str: return agent_tools.get_live_kalshi_status()
def get_recent_veto_summary() -> str: return agent_tools.get_recent_veto_summary()
def run_kalshi_diagnostic() -> str: return agent_tools.run_kalshi_diagnostic()
def run_storage_audit() -> str: return agent_tools.run_storage_audit()

def ask_ai(query: str) -> str:
    """
    Advanced reasoning agent for the Kalshi Weather Engine.
    Mandate: Use tools immediately to answer questions. Never just describe intent.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key: return "Error: GOOGLE_API_KEY is not set."
    if not HAS_GENAI_SDK: return "Error: google-genai package not installed."

    try:
        client = genai.Client(api_key=api_key)
        model_id = get_reasoning_model_id()
        
        context = get_repo_context()
        system_instruction = (
            "You are the Sovereign SRE Oracle for the Kalshi Weather Prediction Engine.\n"
            "Your primary goal is to provide deep, evidence-based analysis and technical execution.\n\n"
            "### OPERATIONAL MANDATE ###\n"
            "1. **ACTION FIRST**: If asked a question about system state, cash, positions, vetoes, or code, you MUST call the appropriate tool in your first turn. Prefer broker-first live-truth tools before SQL when live status is requested.\n"
            "2. **EMPIRICAL PROOF**: Base your analysis on actual data from the DB or files. Do not speculate.\n"
            "3. **MULTI-STEP REASONING**: Use your tools in sequence if needed. For example, list files -> read file -> analyze.\n"
            "4. **TECHNICAL PRECISION**: You are a Lead Architect. Be concise, direct, and technically accurate.\n"
            "5. **NO HALLUCINATIONS**: If a tool returns no data, state that clearly.\n"
            "6. **TRUTH BUCKETS**: Separate verified facts, inferred causes, and unverified items in your answer.\n\n"
            f"### CONTEXTUAL TRUTH ###\n{context}"
        )

        tools = [
            get_live_kalshi_status,
            get_recent_veto_summary,
            run_kalshi_diagnostic,
            run_storage_audit,
            execute_sql,
            read_file,
            list_files,
            replace_text,
            run_safe_command,
        ]

        config_dict = {
            'system_instruction': system_instruction,
            'tools': tools,
            'automatic_function_calling': {'disable': False},
            'safety_settings': [{'category': c, 'threshold': 'BLOCK_NONE'} for c in ['HARM_CATEGORY_HARASSMENT', 'HARM_CATEGORY_HATE_SPEECH', 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'HARM_CATEGORY_DANGEROUS_CONTENT', 'HARM_CATEGORY_CIVIC_INTEGRITY']]
        }

        # Use a high-capacity model for complex reasoning
        chat = client.chats.create(model=model_id, config=config_dict)
        response = chat.send_message(query)
        
        if not response or not response.text:
            return "SRE Oracle Error: Model failed to provide a textual response. Check logs for tool execution status."

        return response.text
    except Exception as e:
        logger.error(f"SRE Oracle exception: {e}")
        return f"🚨 Oracle Handshake Error: {str(e)}"
