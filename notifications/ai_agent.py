import os
import logging
import json
from typing import Optional
from notifications import agent_tools
from runtime.reasoning_provider import get_reasoning_model_id as _get_reasoning_model_id
from runtime.reasoning_provider import probe_reasoning_model as _probe_reasoning_model

logger = logging.getLogger(__name__)


def get_reasoning_model_id() -> str:
    return _get_reasoning_model_id()


def probe_reasoning_model() -> dict:
    """Cheap handshake probe used by release audits and operator truth."""
    return _probe_reasoning_model()

def get_repo_context() -> str:
    """
    Builds a rich context for the AI, including dynamic SQL schema, live config parameters, and runtime status.
    """
    context = []

    # 1. Canonical Truth (AGENTS.md & GEMINI.md)
    try:
        if os.path.exists("AGENTS.md"):
            with open("AGENTS.md", "r") as f:
                context.append("### AGENTS.md (System Architecture)\n" + f.read())
        if os.path.exists("GEMINI.md"):
            with open("GEMINI.md", "r") as f:
                context.append("### GEMINI.md (Operating Truth)\n" + f.read())
    except Exception: pass

    # 2. Live Risk & System Configuration Parameters
    try:
        import config
        from forecast.strategy_engine import EV_THRESHOLD, CITY_BLACKLIST, CITY_PRIORITY_TIERS
        live_config = {
            "ACCOUNT_SIZE": config.ACCOUNT_SIZE,
            "SHADOW_EXECUTION": config.SHADOW_EXECUTION,
            "KALSHI_MIN_ENTRY_PRICE": config.KALSHI_MIN_ENTRY_PRICE,
            "EV_THRESHOLD": EV_THRESHOLD,
            "KALSHI_MAX_USD_PER_POSITION": config.KALSHI_MAX_USD_PER_POSITION,
            "KALSHI_MAX_QTY_PER_POSITION": config.KALSHI_MAX_QTY_PER_POSITION,
            "KALSHI_MAX_CONCURRENT_POSITIONS": config.KALSHI_MAX_CONCURRENT_POSITIONS,
            "KALSHI_HUB_EXPOSURE_MIN_USD": config.KALSHI_HUB_EXPOSURE_MIN_USD,
            "KALSHI_HUB_EXPOSURE_PCT": config.KALSHI_HUB_EXPOSURE_PCT,
            "KALSHI_KELLY_CAP": config.KALSHI_KELLY_CAP,
            "KALSHI_MAX_RISK_PER_EVENT_PCT": config.KALSHI_MAX_RISK_PER_EVENT_PCT,
            "KALSHI_MAX_DEPLOYED_PCT": config.KALSHI_MAX_DEPLOYED_PCT,
            "CITY_PRIORITY_TIERS": CITY_PRIORITY_TIERS,
            "CITY_BLACKLIST": list(CITY_BLACKLIST) if isinstance(CITY_BLACKLIST, (set, list)) else list(CITY_BLACKLIST),
        }
        context.append("### LIVE ENGINE CONFIGURATION & RISK LIMITS\n" + json.dumps(live_config, indent=2))
    except Exception as exc:
        logger.warning("Failed to hydrate live config context: %s", exc)

    # 3. Dynamic Database Schema & Active Positions
    try:
        import sqlite3
        from config import DB_PATH
        if os.path.exists(DB_PATH):
            schema_info = {}
            active_positions = []
            with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
                cur = conn.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
                tables = [t[0] for t in cur.fetchall()]
                for t in tables:
                    cur.execute(f"PRAGMA table_info({t});")
                    cols = [c[1] for c in cur.fetchall()]
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    cnt = cur.fetchone()[0]
                    schema_info[t] = {"row_count": cnt, "columns": cols}
                
                # Hydrate active trades
                # forecast_positions has entry_price/opened_at, not price/timestamp.
                # The wrong names raised OperationalError, which the bare except
                # below swallowed together with the schema block -- so the agent
                # silently ran with no database context at all.
                cur.execute(
                    "SELECT ticker, side, entry_price, qty, opened_at "
                    "FROM forecast_positions WHERE active=1 LIMIT 50"
                )
                active_positions = [
                    dict(zip(["ticker", "side", "entry_price", "qty", "opened_at"], r))
                    for r in cur.fetchall()
                ]
                
            context.append("### DYNAMIC DATABASE SCHEMA (trades.db)\n" + json.dumps(schema_info, indent=2))
            context.append("### LIVE ACTIVE FORECAST POSITIONS\n" + json.dumps(active_positions, indent=2))
    except Exception as exc:
        logger.warning("Dynamic database schema hydration failed: %s", exc)

    # 4. Broker-first live truth
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

    # 5. Recent veto pattern
    try:
        veto_summary = json.loads(agent_tools.get_recent_veto_summary())
        context.append("### RECENT VETO SUMMARY\n" + json.dumps(veto_summary, indent=2))
    except Exception:
        pass

    # 6. Recent execution failures after approval
    try:
        execution_summary = json.loads(agent_tools.get_recent_execution_summary())
        context.append("### RECENT EXECUTION SUMMARY\n" + json.dumps(execution_summary, indent=2))
    except Exception:
        pass

    # 7. Weather learning / adaptive blend state
    try:
        learning_status = json.loads(agent_tools.get_weather_learning_status())
        context.append("### WEATHER LEARNING STATUS\n" + json.dumps(learning_status, indent=2))
    except Exception:
        pass

    # 8. Release-gate truth
    try:
        release_status = json.loads(agent_tools.get_release_status())
        context.append("### RELEASE GATE STATUS\n" + json.dumps(release_status, indent=2))
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
def get_recent_execution_summary() -> str: return agent_tools.get_recent_execution_summary()
def get_weather_learning_status() -> str: return agent_tools.get_weather_learning_status()
def get_release_status() -> str: return agent_tools.get_release_status()
def run_kalshi_diagnostic() -> str: return agent_tools.run_kalshi_diagnostic()
def run_storage_audit() -> str: return agent_tools.run_storage_audit()
def run_release_audit(command: str) -> str: return agent_tools.run_release_audit(command)

def ask_ai(query: str) -> str:
    """Telegram entrypoint. Delegates to the shared brain with read-only access.

    The tool-calling loop and system prompt now live in runtime.brain, shared with the
    cockpit orb. Telegram is restricted to read-tier tools, so a mistyped message
    cannot patch live trading code; write tools must be run from the cockpit.
    """
    from runtime import brain

    return brain.ask([{"role": "user", "content": query}], surface=brain.TELEGRAM)
