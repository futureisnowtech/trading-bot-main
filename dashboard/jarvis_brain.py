"""
Jarvis AI Brain — Superintelligent system monitoring agent powered by Google Gemini.
Equipped with local tool execution to query database, logs, and broker state.
"""

import os
import sqlite3
import json
import logging
from typing import Any

from config import DB_PATH, GEMINI_MODEL

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False


# ── Tool functions ──────────────────────────────────────────────────

def _get_db_path() -> str:
    """Return valid database path, ensuring fallback to logs/trades.db if main DB is empty or missing."""
    primary = DB_PATH
    if os.path.exists(primary):
        try:
            conn = sqlite3.connect(primary)
            count = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            conn.close()
            if count > 0:
                return primary
        except Exception:
            pass

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fallback = os.path.join(repo_root, "logs", "trades.db")
    if os.path.exists(fallback):
        return fallback
    return primary


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
    """List all currently active open positions on the live broker."""
    res = []
    
    # 1. Live Broker Positions
    try:
        from execution.kalshi_broker import get_kalshi_broker
        broker = get_kalshi_broker()
        if broker.is_connected() or broker.connect():
            positions = broker.get_positions()
            active_pos = []
            for p in positions:
                qty = abs(float(p.get("position_fp") or p.get("qty") or 0.0))
                if qty <= 0:
                    continue
                ticker = p.get("ticker") or p.get("local_symbol")
                side = "YES" if float(p.get("position_fp") or p.get("qty") or 0.0) > 0 else "NO"
                mkt_val = float(p.get("market_exposure_dollars") or 0.0)
                entry = float(p.get("entry_price") or p.get("entry") or 0.0)
                pnl = float(p.get("realized_pnl_dollars") or 0.0)
                active_pos.append(
                    f"  - {ticker} ({side}) | Qty: {qty:.0f} | Entry: ${entry:.2f} | Current Val: ${mkt_val:.2f} | Realized PnL: ${pnl:.2f}"
                )
            if active_pos:
                res.append("🟢 **LIVE BROKER POSITIONS:**\n" + "\n".join(active_pos))
            else:
                res.append("🟢 **LIVE BROKER POSITIONS:** No active live broker positions.")
    except Exception as e:
        res.append(f"🟢 **LIVE BROKER POSITIONS:** Query error: {e}")

    return "\n\n".join(res)


def get_recent_trades(limit: int = 15) -> str:
    """Query the local SQLite database for the most recent trading events."""
    try:
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ts, symbol, action, qty, price, pnl_usd, notes, contract_side FROM trades ORDER BY ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
        trades = []
        for r in rows:
            pnl_str = f"${r['pnl_usd']:.2f}" if r['pnl_usd'] is not None else "$0.00"
            trades.append(
                f"- [{r['ts']}] {r['action']} {r['qty']}x {r['symbol']} ({r['contract_side']}) @ ${r['price']:.2f} | PnL: {pnl_str} | Notes: {r['notes']}"
            )
        conn.close()
        if not trades:
            return "No recent trades found in database."
        return "\n".join(trades)
    except Exception as e:
        return f"Error retrieving recent trades: {e}"


def search_trades_and_positions(
    query_text: str = "",
    min_pnl: float | None = None,
    max_pnl: float | None = None,
    limit: int = 30
) -> str:
    """Search trades and open/closed positions by ticker substring, city name (e.g. DC, PHIL, MIA), strike threshold (e.g. 94), or PnL range (e.g. min_pnl=-10.0, max_pnl=-5.0)."""
    try:
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row

        where_clauses = []
        params: list[Any] = []

        if query_text:
            q_clean = str(query_text).strip().upper()
            city_map = {
                "WASHINGTON": "DC", "WASHINGTON DC": "DC", "PHILADELPHIA": "PHIL", "PHILLY": "PHIL",
                "BOSTON": "BOS", "CHICAGO": "CHI", "MIAMI": "MIA", "DALLAS": "DAL",
                "HOUSTON": "HOU", "PHOENIX": "PHX", "SEATTLE": "SEA", "ATLANTA": "ATL"
            }
            mapped_city = city_map.get(q_clean)

            if mapped_city:
                where_clauses.append("(symbol LIKE ? OR notes LIKE ?)")
                params.extend([f"%{mapped_city}%", f"%{str(query_text)}%"])
            else:
                where_clauses.append("(symbol LIKE ? OR notes LIKE ?)")
                params.extend([f"%{str(query_text)}%", f"%{str(query_text)}%"])

        if min_pnl is not None and str(min_pnl).strip() != "":
            try:
                where_clauses.append("pnl_usd >= ?")
                params.append(float(min_pnl))
            except (ValueError, TypeError):
                pass

        if max_pnl is not None and str(max_pnl).strip() != "":
            try:
                where_clauses.append("pnl_usd <= ?")
                params.append(float(max_pnl))
            except (ValueError, TypeError):
                pass

        sql = "SELECT id, ts, symbol, action, qty, price, value_usd, fee_usd, pnl_usd, notes, contract_side FROM trades"
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += " ORDER BY ts DESC LIMIT ?"
        try:
            limit_val = int(limit)
        except (ValueError, TypeError):
            limit_val = 30
        params.append(limit_val)

        rows = conn.execute(sql, params).fetchall()
        conn.close()

        if not rows:
            return f"No trades found matching criteria (query='{query_text}', min_pnl={min_pnl}, max_pnl={max_pnl})."

        results = []
        for r in rows:
            pnl_val = r['pnl_usd'] if r['pnl_usd'] is not None else 0.0
            fee_val = r['fee_usd'] if r['fee_usd'] is not None else 0.0
            val_usd = r['value_usd'] if r['value_usd'] is not None else 0.0
            results.append(
                f"- [ID:{r['id']} | {r['ts']}] {r['symbol']} ({r['contract_side']}) | {r['action']} {r['qty']}x @ ${r['price']:.2f} | "
                f"Value: ${val_usd:.2f} | Fee: ${fee_val:.4f} | PnL: ${pnl_val:.2f} | Notes: {r['notes']}"
            )
        return "\n".join(results)
    except Exception as e:
        return f"Error searching trades: {e}"


def get_trade_post_mortem(ticker_or_id: str) -> str:
    """Perform an in-depth quantitative post-mortem analysis for a given contract ticker or trade ID. Retrieves GFS vs ECMWF model probabilities, blended model fair value, model uncertainty (sigma_post), NOAA official weather station ground-truth observations (max/min temp), and trade fill details."""
    try:
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row

        query_str = ticker_or_id.strip()
        trades = []
        if query_str.isdigit():
            trades = conn.execute("SELECT * FROM trades WHERE id = ?", (int(query_str),)).fetchall()
        else:
            trades = conn.execute("SELECT * FROM trades WHERE symbol LIKE ? ORDER BY ts DESC LIMIT 10", (f"%{query_str}%",)).fetchall()

        symbol = query_str
        if trades:
            symbol = trades[0]['symbol']

        contract = conn.execute("SELECT * FROM forecast_contracts WHERE local_symbol LIKE ? OR conid LIKE ?", (f"%{symbol}%", f"%{symbol}%")).fetchone()

        resolution = None
        if contract:
            resolution = conn.execute("SELECT * FROM forecast_resolutions WHERE contract_id = ?", (contract['id'],)).fetchone()
        if not resolution:
            resolution = conn.execute("SELECT * FROM forecast_resolutions WHERE notes LIKE ? OR source LIKE ?", (f"%{symbol}%", f"%{symbol}%")).fetchone()

        station_code = None
        date_str = None

        import re
        m = re.search(r'KX(HIGH|LOW)T([A-Z]+)-(\d{2}[A-Z]{3}\d{2})-T(\d+)', symbol, re.IGNORECASE)
        if m:
            city_code = m.group(2).upper()
            date_part = m.group(3).upper()
            station_map = {
                "DC": "KDCA", "WAS": "KDCA", "PHIL": "KPHL", "PHL": "KPHL",
                "BOS": "KBOS", "CHI": "KORD", "MIA": "KMIA", "DAL": "KDFW",
                "HOU": "KIAH", "PHX": "KPHX", "SEA": "KSEA", "ATL": "KATL",
                "LAX": "KLAX", "NYC": "KNYC", "MIN": "KMSP", "NOLA": "KMSY"
            }
            station_code = station_map.get(city_code)

            try:
                from datetime import datetime
                dt = datetime.strptime(date_part, "%y%b%d")
                date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        noaa_data = None
        if station_code and date_str:
            noaa_data = conn.execute(
                "SELECT * FROM noaa_daily_summaries WHERE station = ? AND date = ?",
                (station_code, date_str)
            ).fetchone()

        conn.close()

        res = [f"=== QUANTITATIVE POST-MORTEM FOR {symbol} ==="]

        if trades:
            res.append("\n📈 **Trade Execution Summary:**")
            total_pnl = 0.0
            for t in trades:
                pnl = t['pnl_usd'] if t['pnl_usd'] is not None else 0.0
                total_pnl += pnl
                fee = t['fee_usd'] if t['fee_usd'] is not None else 0.0
                res.append(f"  - [{t['ts']}] ID:{t['id']} | {t['action']} {t['qty']}x @ ${t['price']:.2f} | Side: {t['contract_side']} | PnL: ${pnl:.2f} | Fee: ${fee:.4f}")
            res.append(f"  **Total Executed PnL:** ${total_pnl:.2f}")

        if contract:
            res.append(f"\n📜 **Contract Specs:** Name: {contract['contract_name']} | Strike: {contract['strike']} | Expiry/Resolution: {contract['resolution_at']}")

        if resolution:
            res.append("\n🔬 **Model Forecast & Resolution Metrics:**")
            res.append(f"  - GFS Forecast Prob (q_gfs): {resolution['q_gfs']}")
            res.append(f"  - ECMWF Forecast Prob (q_ecmwf): {resolution['q_ecmwf']}")
            res.append(f"  - Blended Model Fair Value (q_hat): {resolution['q_hat']}")
            res.append(f"  - Posterior Uncertainty (sigma_post): {resolution['sigma_post']}")
            res.append(f"  - Settlement Result: Side '{resolution['resolved_side']}' | Observed Value: {resolution['resolved_value']}")

        if noaa_data:
            res.append(f"\n🌡️ **NOAA Ground-Truth Station Observation ({station_code} on {date_str}):**")
            res.append(f"  - Max Temperature: {noaa_data['temp_max']}°F")
            res.append(f"  - Min Temperature: {noaa_data['temp_min']}°F")
            res.append(f"  - Precipitation: {noaa_data['precipitation']} in")
        elif station_code or date_str:
            res.append(f"\n🌡️ **NOAA Observation Query:** Station {station_code}, Date {date_str} (No direct record matched in noaa_daily_summaries).")

        return "\n".join(res)
    except Exception as e:
        return f"Error executing trade post-mortem: {e}"



def get_ticker_analysis(ticker: str) -> str:
    """Analyze a specific weather contract's model forecast inputs, threshold, and trade logs."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        contract = conn.execute(
            "SELECT * FROM forecast_contracts WHERE local_symbol = ?", (ticker,)
        ).fetchone()

        pos = conn.execute(
            "SELECT * FROM forecast_positions WHERE ticker = ? AND active = 1", (ticker,)
        ).fetchone()

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
        return "\n".join(res) if res else f"No data found for ticker {ticker}."
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


def get_fee_drag() -> str:
    """Compare gross trading edge against exchange fees paid on the live lane.

    Fees are the live lane's binding constraint, so this replaces the retired
    paper-lane comparison: the useful question is no longer live-vs-paper, it is
    how much of the captured edge the exchange is taking.
    """
    try:
        from runtime.kalshi_settlement_truth import load_weather_settlement_truth

        truth = load_weather_settlement_truth()
        net = float(truth.get("total_pnl_usd") or 0.0)
        total = int(truth.get("total") or 0)
        wins = int(truth.get("wins") or 0)
        losses = int(truth.get("losses") or 0)

        conn = sqlite3.connect(_get_db_path())
        row = conn.execute(
            "SELECT COALESCE(SUM(fee_usd), 0) FROM trades WHERE broker = 'kalshi'"
        ).fetchone()
        conn.close()
        fees = float(row[0] or 0.0)

        gross = net + fees
        pct = (100.0 * fees / gross) if gross > 0 else 0.0
        return (
            f"Live Lane ({total} settled | {wins}W / {losses}L)\n"
            f"Gross edge captured: ${gross:+,.2f}\n"
            f"Exchange fees paid:  ${fees:,.2f}\n"
            f"Net realized:        ${net:+,.2f}\n"
            f"Fees consumed {pct:.1f}% of gross edge."
        )
    except Exception as e:
        return f"Error computing fee drag: {e}"

def update_system_parameter(key: str, value: str, rationale: str = "") -> str:
    """Update a live system strategy parameter in the SQLite dynamic config table after passing Safety Shield audit."""
    # Run Layer 1 Safety Shield Audit
    passed, msg = JarvisSafetyCompilerShield.audit_parameter(key, value)
    if not passed:
        return msg

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dynamic_system_config (
                param_key TEXT PRIMARY KEY,
                param_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                rationale TEXT
            )"""
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO dynamic_system_config (param_key, param_value, updated_at, rationale) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(param_key) DO UPDATE SET param_value=excluded.param_value, updated_at=excluded.updated_at, rationale=excluded.rationale",
            (key.upper(), str(value), now_iso, rationale)
        )
        conn.commit()
        conn.close()
        return f"🛡️ 5X SAFETY SHIELD PASSED | OVERRIDE SUCCESS: Parameter '{key.upper()}' updated to '{value}' at {now_iso}. Rationale: {rationale}"
    except Exception as e:
        return f"❌ System Bridge Override Error: {e}"


def get_system_parameters() -> str:
    """Retrieve all current dynamic system parameter overrides from the SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dynamic_system_config (
                param_key TEXT PRIMARY KEY,
                param_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                rationale TEXT
            )"""
        )
        rows = conn.execute("SELECT * FROM dynamic_system_config ORDER BY param_key").fetchall()
        conn.close()
        if not rows:
            return "No dynamic parameter overrides currently set. System running on default config.py constants."
        lines = [f"- **{r['param_key']}**: `{r['param_value']}` (Updated: {r['updated_at']}) | Rationale: {r['rationale']}" for r in rows]
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving system parameters: {e}"


def apply_hot_patch_code(patch_name: str, code_snippet: str) -> str:
    """Safely apply a Python code patch after passing 5-Layer Safety Compiler Shield AST & Sandbox audits."""
    # Layer 2 AST Audit
    passed_ast, msg_ast = JarvisSafetyCompilerShield.audit_code_ast(code_snippet)
    if not passed_ast:
        return msg_ast

    # Layer 3 Sandbox Execution Test
    passed_sandbox, msg_sandbox = JarvisSafetyCompilerShield.test_sandbox_exec(code_snippet)
    if not passed_sandbox:
        return msg_sandbox

    patch_dir = os.path.join(os.path.dirname(DB_PATH), "jarvis_patches")
    backup_dir = os.path.join(os.path.dirname(DB_PATH), "jarvis_patches_backup")
    os.makedirs(patch_dir, exist_ok=True)
    os.makedirs(backup_dir, exist_ok=True)

    file_path = os.path.join(patch_dir, f"{patch_name}.py")

    # Layer 4 Immutable Backup Engine
    if os.path.exists(file_path):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"{patch_name}_{timestamp}.py")
        shutil.copy2(file_path, backup_path)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# JARVIS Autonomous Hot Patch: {patch_name}\n")
            f.write(f"# Verified by 5X Safety Compiler Shield at {datetime.now(timezone.utc).isoformat()}\n\n")
            f.write(code_snippet)
        return f"🛡️ 5X SAFETY SHIELD VERIFIED | HOT PATCH APPLIED: Saved patch '{patch_name}' at {file_path}. (AST Security: PASS | Sandbox Exec: PASS)"
    except Exception as e:
        return f"❌ Hot Patch File Write Error: {e}"


# ── Chat execution ──────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are JARVIS, an elite Tony Stark-level Quantitative Weather Trading Expert and Lead Systems Architect with direct SSH root access to our live trading droplet and SQLite database ledger. "
    "You have full power to inspect account status, live open positions, trade logs, and container logs. "
    "CRITICAL: You are also equipped with the System Mutation Bridge (update_system_parameter, get_system_parameters, apply_hot_patch_code). You CAN update live system strategy parameters (e.g., KELLY_FRACTION, TAKE_PROFIT_TRIGGER, GFS_WEIGHT, HUB_RISK_CAP_PCT) and apply python hot-patches dynamically when instructed!\n\n"
    "DIAGNOSTIC & POST-MORTEM WORKFLOW:\n"
    "1. When asked about specific trades, dates, or losses, ALWAYS call `search_trades_and_positions` with query keywords, city names (e.g. DC, PHIL), strike temperatures, or PnL ranges (e.g. min_pnl=-10.0, max_pnl=-5.0).\n"
    "2. Once matching trades/contracts are found, call `get_trade_post_mortem` to extract GFS vs ECMWF model spreads, blended fair values, posterior uncertainty, and official NOAA station ground-truth observations.\n"
    "3. Synthesize physics mechanisms (e.g. evaporative cooling deltas, soil moisture thermal inertia, nocturnal boundary layer wind shear), GFS/ECMWF divergence, fee drag, or Kelly sizing factors to explain WHY the trade lost.\n\n"
    "Format EVERY response using this 2-part structure:\n\n"
    "1. 💡 **LAYMAN'S SUMMARY (Direct Answer):** Give a crystal-clear, 2-sentence non-technical answer that directly answers the user's question so anyone can understand it instantly.\n\n"
    "2. 🔬 **POLYMATH QUANTITATIVE INSIGHTS:** Provide deep, high-level quantitative analysis as a top weather trader. Include specific physics mechanisms (e.g. evaporative cooling deltas, soil moisture thermal inertia, nocturnal boundary layer wind shear), model discrepancy dynamics (GFS vs ECMWF ensemble spread), integral/differential rate of change in forecast trajectories, NOAA ground-truth observations, and live droplet database evidence."
)


def _run_tool(name: str, args: dict) -> str:
    """Execute a tool function by name and return the result."""
    tool_map = {
        "get_account_status": get_account_status,
        "get_open_positions": get_open_positions,
        "get_recent_trades": get_recent_trades,
        "search_trades_and_positions": search_trades_and_positions,
        "get_trade_post_mortem": get_trade_post_mortem,
        "get_ticker_analysis": get_ticker_analysis,
        "get_latest_bot_logs": get_latest_bot_logs,
        "get_fee_drag": get_fee_drag,
        "update_system_parameter": update_system_parameter,
        "get_system_parameters": get_system_parameters,
        "apply_hot_patch_code": apply_hot_patch_code,
    }
    fn = tool_map.get(name)
    if not fn:
        return f"Unknown tool: {name}"
    try:
        return fn(**args)
    except Exception as e:
        return f"Tool execution error ({name}): {e}"


def run_jarvis_chat(messages: list[dict]) -> str:
    """Run chat interface using google.genai Client with tool calling."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "⚠️ Jarvis is inactive: GOOGLE_API_KEY is not set."
    if not HAS_GENAI_SDK:
        return "⚠️ Jarvis is inactive: google-genai SDK not installed."

    try:
        client = genai.Client(api_key=api_key)

        model_id = (GEMINI_MODEL or "gemini-2.5-flash").strip()
        if not model_id.startswith("models/"):
            model_id = f"models/{model_id}"

        # Build contents list from chat history
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            ))

        tool_functions = [
            get_account_status,
            get_open_positions,
            get_recent_trades,
            search_trades_and_positions,
            get_trade_post_mortem,
            get_ticker_analysis,
            get_latest_bot_logs,
            get_fee_drag,
            update_system_parameter,
            get_system_parameters,
            apply_hot_patch_code,
        ]

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.3,
            max_output_tokens=2048,
            tools=tool_functions,
        )

        response = client.models.generate_content(
            model=model_id,
            contents=contents,
            config=config,
        )

        # Handle tool calls if present
        if response.candidates and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts
            function_calls = [p for p in parts if p.function_call]

            if function_calls:
                # Execute each tool call and collect results
                tool_results_parts = []
                for fc_part in function_calls:
                    fc = fc_part.function_call
                    result_str = _run_tool(fc.name, dict(fc.args) if fc.args else {})
                    tool_results_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"result": result_str}
                        )
                    )

                # Send tool results back to model
                contents.append(response.candidates[0].content)
                contents.append(types.Content(
                    role="user",
                    parts=tool_results_parts,
                ))

                follow_up = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=config,
                )
                return follow_up.text or "No response generated after tool execution."

        return response.text or "No response generated."
    except Exception as e:
        logger.exception("Jarvis execution error")
        return f"⚠️ Error executing JARVIS query: {e}"
