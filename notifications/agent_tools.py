"""
notifications/agent_tools.py — Authorized tools for the Telegram Gemini Agent.

These tools allow the Gemini Agent to interact with the codebase via Telegram.
Strictly restricted to avoid accidental system destruction.
"""

from __future__ import annotations

import os
import subprocess
import logging
import json
import sqlite3
import sys
import shlex
from typing import Optional, List

from config import DB_PATH

logger = logging.getLogger(__name__)

def execute_sql(query: str) -> str:
    """
    Safe, read-only SQL execution for the AI agent.
    Targets the active runtime DB. Available tables: forecast_positions, trades,
    system_events, api_costs, forecast_markets.
    """
    q_upper = query.strip().upper()
    if not q_upper.startswith("SELECT"):
        return "Error: Only SELECT queries are allowed."

    forbidden = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "REPLACE"]
    if any(cmd in q_upper for cmd in forbidden):
        return "Error: Data modification or structural changes are strictly forbidden."

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute(query).fetchall()
            if not rows:
                return "Query executed successfully. Result: No rows returned."
            
            data = [dict(r) for r in rows[:50]]
            res = json.dumps(data, indent=2)
            if len(rows) > 50:
                res += f"\n... (truncated {len(rows)-50} more rows)"
            return res
    except Exception as e:
        logger.error(f"AI SQL Error: {e}")
        return f"Database Error: {str(e)}"

def read_file(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Reads a file from the repository. Use start_line and end_line for large files (e.g., logs)."""
    try:
        abs_path = os.path.abspath(file_path)
        if not abs_path.startswith(os.getcwd()):
            return "Error: Access denied. Cannot read files outside of the repository root."
        
        if not os.path.exists(abs_path):
            return f"Error: File '{file_path}' does not exist."

        with open(abs_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        if start_line is not None or end_line is not None:
            s = (start_line - 1) if start_line else 0
            e = end_line if end_line else len(lines)
            content = "".join(lines[s:e])
        else:
            is_doc = file_path.endswith('.md') or file_path.endswith('.txt')
            limit = 10000 if is_doc else 2000
            
            content = "".join(lines[:limit])
            if len(lines) > limit:
                content += f"\n... (truncated at {limit} lines. Use start_line/end_line to read more.)"
        
        return content
    except Exception as e:
        return f"Error reading file: {str(e)}"

def list_files(dir_path: str = ".") -> str:
    """Lists files in a directory to help explore the codebase."""
    try:
        abs_path = os.path.abspath(dir_path)
        if not abs_path.startswith(os.getcwd()):
            return "Error: Access denied."
        
        items = os.listdir(abs_path)
        res = []
        for item in sorted(items):
            full = os.path.join(abs_path, item)
            suffix = "/" if os.path.isdir(full) else ""
            res.append(f"{item}{suffix}")
        return "\n".join(res)
    except Exception as e:
        return f"Error listing files: {str(e)}"

def replace_text(file_path: str, old_string: str, new_string: str) -> str:
    """Surgically replaces text in a file. Requires exact string match. USE SPARINGLY."""
    try:
        abs_path = os.path.abspath(file_path)
        if not abs_path.startswith(os.getcwd()):
            return "Error: Access denied."

        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if old_string not in content:
            return "Error: Exact match not found."

        if content.count(old_string) > 1:
            return "Error: Multiple occurrences. Provide more context."

        new_content = content.replace(old_string, new_string)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return f"Successfully updated '{file_path}'."
    except Exception as e:
        return f"Error updating file: {str(e)}"

def get_live_kalshi_status() -> str:
    """Return broker-first live Kalshi truth, including DB drift and lane state."""
    try:
        from runtime.operator_truth import get_live_kalshi_status as _get_live_kalshi_status

        return json.dumps(_get_live_kalshi_status(), indent=2)
    except Exception as e:
        logger.error("AI live Kalshi status error: %s", e)
        return f"Error: {str(e)}"

def get_recent_veto_summary() -> str:
    """Return recent ForecastRunner veto reasons and counts."""
    try:
        from runtime.operator_truth import get_recent_veto_summary as _get_recent_veto_summary

        return json.dumps(_get_recent_veto_summary(), indent=2)
    except Exception as e:
        logger.error("AI veto summary error: %s", e)
        return f"Error: {str(e)}"

def get_recent_execution_summary() -> str:
    """Return recent execution-blocked and post-submit execution outcomes."""
    try:
        from runtime.operator_truth import (
            get_recent_execution_summary as _get_recent_execution_summary,
        )

        return json.dumps(_get_recent_execution_summary(), indent=2)
    except Exception as e:
        logger.error("AI execution summary error: %s", e)
        return f"Error: {str(e)}"


def get_weather_learning_status() -> str:
    """Return the latest weather RBI calibration and adaptive blend state."""
    try:
        from runtime.operator_truth import (
            get_weather_learning_status as _get_weather_learning_status,
        )

        return json.dumps(_get_weather_learning_status(), indent=2)
    except Exception as e:
        logger.error("AI weather learning summary error: %s", e)
        return f"Error: {str(e)}"

def get_release_status() -> str:
    """Return the current release-gate verdict and live blocker summary."""
    try:
        from runtime.operator_truth import get_release_status as _get_release_status

        return json.dumps(_get_release_status(), indent=2)
    except Exception as e:
        logger.error("AI release status error: %s", e)
        return f"Error: {str(e)}"

def run_kalshi_diagnostic() -> str:
    """Run the repo's live Kalshi connectivity diagnostic script."""
    script_path = os.path.join(os.getcwd(), "scripts", "verify_kalshi_connection.py")
    if not os.path.exists(script_path):
        return "Error: scripts/verify_kalshi_connection.py not found."
    try:
        result = subprocess.check_output(
            [sys.executable, script_path],
            stderr=subprocess.STDOUT,
            timeout=45,
            text=True,
        )
        return result if result else "Success (no output)."
    except subprocess.CalledProcessError as e:
        return e.output or f"Error: command exited {e.returncode}"
    except Exception as e:
        return f"Error: {str(e)}"

def run_storage_audit() -> str:
    """Run the repo's storage audit script."""
    script_path = os.path.join(os.getcwd(), "scripts", "storage_audit.py")
    if not os.path.exists(script_path):
        return "Error: scripts/storage_audit.py not found."
    try:
        result = subprocess.check_output(
            [sys.executable, script_path],
            stderr=subprocess.STDOUT,
            timeout=45,
            text=True,
        )
        return result if result else "Success (no output)."
    except subprocess.CalledProcessError as e:
        return e.output or f"Error: command exited {e.returncode}"
    except Exception as e:
        return f"Error: {str(e)}"

def run_release_audit(command: str) -> str:
    """Run the canonical release audit in local, remote, or promote mode."""
    allowed = {
        "local": [sys.executable, "scripts/release_audit.py", "--local", "--format", "json"],
        "remote": [sys.executable, "scripts/release_audit.py", "--remote", "--format", "json"],
        "promote": [sys.executable, "scripts/release_audit.py", "--promote", "--format", "json"],
    }
    key = str(command or "").strip().lower()
    if key not in allowed:
        return "Error: command must be one of: local, remote, promote."
    try:
        result = subprocess.check_output(
            allowed[key],
            stderr=subprocess.STDOUT,
            timeout=900,
            text=True,
        )
        return result if result else "Success (no output)."
    except subprocess.CalledProcessError as e:
        return e.output or f"Error: command exited {e.returncode}"
    except Exception as e:
        return f"Error: {str(e)}"

def run_safe_command(command: str) -> str:
    """Runs restricted shell commands (grep, py_compile, git status, git diff)."""
    allowed_exact = {
        "python3 scripts/verify_kalshi_connection.py",
        "python scripts/verify_kalshi_connection.py",
        f"{sys.executable} scripts/verify_kalshi_connection.py",
        "python3 scripts/storage_audit.py",
        "python scripts/storage_audit.py",
        f"{sys.executable} scripts/storage_audit.py",
        f"{sys.executable} scripts/release_audit.py --local",
        f"{sys.executable} scripts/release_audit.py --remote",
    }
    allowed_bases = ["grep", "python3 -m py_compile", "git status", "git diff", "find"]
    is_allowed = any(command.startswith(base) for base in allowed_bases)

    try:
        parts = shlex.split(command)
    except Exception:
        parts = []
    if len(parts) == 2 and parts[0] in {"python", "python3", sys.executable}:
        is_allowed = parts[1] in {
            "scripts/verify_kalshi_connection.py",
            "scripts/storage_audit.py",
        }

    if command not in allowed_exact and not is_allowed:
        return "Error: Command not in whitelist."

    if command.startswith("git ") and not os.path.isdir(os.path.join(os.getcwd(), ".git")):
        return "Error: Git metadata is not deployed in this runtime; git commands are unavailable here."

    try:
        result = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT, timeout=15).decode()
        return result if result else "Success (no output)."
    except Exception as e:
        return f"Error: {str(e)}"
