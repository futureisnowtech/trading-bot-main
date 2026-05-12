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
from typing import Optional

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

def read_file(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Reads a file from the repository. Use start_line and end_line for large files."""
    try:
        # Prevent reading sensitive files or escaping repo root
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
            # Hard limit of 2000 lines for Telegram safety
            content = "".join(lines[:2000])
            if len(lines) > 2000:
                content += "\n... (truncated, use line numbers to read more)"
        
        return content
    except Exception as e:
        return f"Error reading file: {str(e)}"

def replace_text(file_path: str, old_string: str, new_string: str) -> str:
    """Surgically replaces text in a file. Requires exact string match for safety."""
    try:
        abs_path = os.path.abspath(file_path)
        if not abs_path.startswith(os.getcwd()):
            return "Error: Access denied."

        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if old_string not in content:
            return "Error: The exact string to replace was not found in the file."

        if content.count(old_string) > 1:
            return "Error: Multiple occurrences found. Provide more context to make the replacement unique."

        new_content = content.replace(old_string, new_string)
        
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return f"Successfully updated '{file_path}'."
    except Exception as e:
        return f"Error updating file: {str(e)}"

def run_safe_command(command: str) -> str:
    """Runs restricted shell commands (ls, grep, py_compile, git status)."""
    # Whitelist of allowed base commands
    allowed_bases = ["ls", "grep", "python3 -m py_compile", "git status", "git diff", "find"]
    
    cmd_base = command.split()[0]
    is_allowed = any(command.startswith(base) for base in allowed_bases)
    
    if not is_allowed:
        return f"Error: Command '{cmd_base}' is not in the safety whitelist."

    try:
        # Run with a 10s timeout
        result = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT, timeout=10).decode()
        return result if result else "Command executed successfully (no output)."
    except subprocess.CalledProcessError as e:
        return f"Command failed with output:\n{e.output.decode()}"
    except Exception as e:
        return f"Error executing command: {str(e)}"
