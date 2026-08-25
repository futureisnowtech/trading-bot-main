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
from typing import Optional, List

from config import DB_PATH, REPO_ROOT

logger = logging.getLogger(__name__)

def execute_sql(query: str) -> str:
    """
    Safe, read-only SQL execution for the AI agent.
    Supports SELECT, WITH, and PRAGMA queries.
    """
    q_upper = query.strip().upper()
    valid_start = any(q_upper.startswith(prefix) for prefix in ["SELECT", "WITH", "PRAGMA"])
    if not valid_start:
        return "Error: Only read-only SELECT, WITH, or PRAGMA queries are allowed."

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
        repo_root = os.path.abspath(REPO_ROOT)
        if not abs_path.startswith(repo_root) and not abs_path.startswith("/app"):
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
            limit = 10000 if is_doc else 5000

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


def get_production_policy_status() -> str:
    """Return build, execution, probability, RBI, and binding risk policy truth."""
    try:
        from runtime.operator_truth import (
            get_live_kalshi_status as _get_live_kalshi_status,
        )

        truth = _get_live_kalshi_status(
            include_recent_vetoes=False,
            include_recent_execution=False,
        )
        return json.dumps(truth.get("production_policy") or {}, indent=2)
    except Exception as e:
        logger.error("AI production policy summary error: %s", e)
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

