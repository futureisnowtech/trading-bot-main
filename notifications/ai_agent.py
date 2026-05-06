import os
import logging
import json
import sqlite3
import traceback
from typing import Optional, List, Dict
import system_state
from runtime.runtime_state import get_lane_state, get_system_state

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


def execute_sql(query: str) -> str:
    """
    Safe, read-only SQL execution for the AI agent.
    Targets logs/trades.db (open_positions, spot_edge_conditions, etc).
    """
    q_upper = query.strip().upper()
    if not q_upper.startswith("SELECT"):
        return "Error: Only SELECT queries are allowed."

    forbidden = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "REPLACE"]
    if any(cmd in q_upper for cmd in forbidden):
        return "Error: Data modification or structural changes are strictly forbidden."

    try:
        db_path = os.path.join(os.getcwd(), "logs", "trades.db")
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Execute with a 5-second timeout to prevent long-running queries
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute(query).fetchall()
            if not rows:
                return "Query executed successfully. Result: No rows returned."
            
            # Limit results to prevent context overflow (max 50 rows)
            data = [dict(r) for r in rows[:50]]
            res = json.dumps(data, indent=2)
            if len(rows) > 50:
                res += f"\n... (truncated {len(rows)-50} more rows)"
            return res
    except Exception as e:
        logger.error(f"AI SQL Error: {e}")
        return f"Database Error: {str(e)}"


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

    # 1b. Live regime policy — score floors, allowed regimes, weights (ground truth from runtime)
    try:
        from runtime.spot_strategy import get_spot_strategy

        policy_summary = {}
        for sym in ["BTC", "ETH", "SOL", "XRP"]:
            p = get_spot_strategy(sym)
            policy_summary[sym] = {
                "allowed_regimes": list(p["allowed_regimes"]),
                "score_floors": p["score_floors"],
                "score_weights": p["score_weights"],
            }
        context.append(
            "### Live Regime Policy (runtime/spot_strategy.get_spot_strategy)\n"
            + json.dumps(policy_summary, indent=2)
        )
    except Exception as e:
        context.append(f"Error reading live regime policy: {e}")

    # 1c. Execution Math — continuous stochastic models (spot_strategy + edge_monitor)
    try:
        with open("runtime/spot_strategy.py", "r") as f:
            src = f.read()
        marker = "def _sigmoid_sizing"
        idx = src.find(marker)
        snippet = src[idx : idx + 3000] if idx != -1 else src[-3000:]
        context.append(
            "### Execution Math: runtime/spot_strategy.py (calculate_execution_profile)\n"
            + snippet
        )
    except Exception as e:
        context.append(f"Error reading spot_strategy execution math: {e}")

    try:
        with open("data/edge_monitor.py", "r") as f:
            src = f.read()
        marker = "_SHADOW_STATE"
        idx = src.find(marker)
        snippet = src[idx : idx + 3000] if idx != -1 else src[-3000:]
        context.append(
            "### Execution Math: data/edge_monitor.py (shadow state + OU + Kyle's Lambda)\n"
            + snippet
        )
    except Exception as e:
        context.append(f"Error reading edge_monitor shadow state: {e}")

    # 2. Live Vitals (System State)
    try:
        state = system_state.state.get_state()
        context.append("### Live Vitals (system_state)\n" + json.dumps(state, indent=2))
    except Exception as e:
        context.append(f"Error getting system state: {e}")

    # 3. Lane Vitals (Runtime State)
    try:
        crypto_lane = get_lane_state("crypto") or {}
        context.append(
            "### Crypto Lane State (runtime_state)\n"
            + json.dumps(crypto_lane, indent=2)
        )
    except Exception as e:
        context.append(f"Error getting lane state: {e}")

    # 4. Recent Logs (Last 50 lines)
    try:
        log_path = os.path.join(os.getcwd(), "logs", "bot.log")
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                lines = f.readlines()
                context.append(
                    "### Recent Logs (Last 50 lines)\n" + "".join(lines[-50:])
                )
    except Exception as e:
        context.append(f"Error reading logs: {e}")

    return "\n\n".join(context)


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
    model_name = os.environ.get("GEMINI_MODEL") or GEMINI_MODEL
    if not model_name.startswith("models/"):
        model_name = f"models/{model_name}"

    context = get_repo_context()

    system_instruction = (
        "You are Gemini CLI, the canonical AI operator for this algo-trading system.\n"
        "Your goal is to provide deep repo-intelligence based on live data.\n\n"
        "### ENHANCED MEMORY PROTOCOL: EXECUTION TRUTH ###\n"
        "When reasoning about live spot readiness, execution profiles, or regime constraints, "
        "`AGENTS.md` acts as the governance layer, but it does NOT contain the mathematical truth.\n"
        "To understand the actual execution logic, you MUST execute the following read order:\n"
        "1. Read `AGENTS.md` for the current live lane architecture and hard constraints.\n"
        "2. Explicitly load and parse `runtime/spot_strategy.py`.\n"
        "3. Analyze `calculate_execution_profile()` to understand the active continuous z-score "
        "mechanics, specifically looking for stochastic calculus gates (e.g., Ornstein-Uhlenbeck "
        "transition probabilities and Kyle's Lambda fragility).\n"
        "4. **Live Stochastic Vitals**: The actual live values for these stochastic gates are pushed "
        "every cycle into `system_state` (Live Vitals). Look for `strategy.stochastic` in the JSON context "
        "to see the current Kalman deviation, Kyle's Lambda fragility, and OU probabilities per symbol.\n"
        "Never claim the system is unaware of a logic upgrade without first verifying the "
        "continuous execution math inside `runtime/spot_strategy.py` and checking the live vitals.\n\n"
        "### TOOLS ###\n"
        "You have access to the `execute_sql` tool. Use it to query `logs/trades.db` for "
        "live exposure (open_positions table) and active mathematical constraints (spot_edge_conditions).\n\n"
        f"### CONTEXT ###\n{context}"
    )

    try:
        # Define the tool
        # In the google-generativeai SDK, tools are passed to the GenerativeModel
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=system_instruction,
            tools=[execute_sql]
        )

        chat = model.start_chat(enable_automatic_function_calling=True)
        response = chat.send_message(query)
        
        # Capture token telemetry for daily burn report (Task 12)
        try:
            import sqlite3 as _sq
            import time as _time
            from config import DB_PATH as _DB_PATH

            _prompt_tokens = int(
                getattr(getattr(response, "usage_metadata", None), "prompt_token_count", 0) or 0
            )
            _completion_tokens = int(
                getattr(getattr(response, "usage_metadata", None), "candidates_token_count", 0) or 0
            )
            if _prompt_tokens > 0 or _completion_tokens > 0:
                with _sq.connect(_DB_PATH) as _tconn:
                    _tconn.execute(
                        "INSERT INTO api_telemetry (ts, module, prompt_tokens, completion_tokens) "
                        "VALUES (?, ?, ?, ?)",
                        (_time.time(), "telegram_ask", _prompt_tokens, _completion_tokens),
                    )
        except Exception as _tel_e:
            logger.debug(f"[ai_agent] telemetry capture failed: {_tel_e}")

        return response.text
    except Exception as e:
        logger.error(f"Gemini Agent exception: {e}")
        return f"Error connecting to Gemini backend: {str(e)}"
