"""
Jarvis AI Brain — Superintelligent system monitoring agent powered by Google Gemini.
Equipped with local tool execution to query database, logs, and broker state.
"""

import os
import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import Any

from config import DB_PATH, GEMINI_MODEL, HUB_PARAMS, TRADE_DATA_START_DATE

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    HAS_GENAI_SDK = True
except ImportError:
    HAS_GENAI_SDK = False


class JarvisSafetyCompilerShield:
    """Allow only dynamic parameters consumed by the production decision path."""

    _ALLOWED_KEYS = {"KELLY_FRACTION"}

    @staticmethod
    def audit_parameter(key: str, value: str) -> tuple[bool, str]:
        key_u = str(key or "").strip().upper()
        if not key_u:
            return False, "❌ Safety Shield: empty parameter key."
        is_hub_floor = key_u.endswith(".HARD_RBI_THRESHOLD") and (
            key_u.removesuffix(".HARD_RBI_THRESHOLD") in HUB_PARAMS
        )
        if key_u not in JarvisSafetyCompilerShield._ALLOWED_KEYS and not is_hub_floor:
            return False, (
                f"❌ Safety Shield: '{key_u}' is not a recognized override-able parameter "
                "consumed by the production decision path."
            )
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return False, "❌ Safety Shield: parameter value must be numeric."
        if key_u == "KELLY_FRACTION" and not 0.0 < numeric_value <= 0.50:
            return False, "❌ Safety Shield: KELLY_FRACTION must be in (0, 0.50]."
        if is_hub_floor and not 0.50 <= numeric_value <= 0.95:
            return False, "❌ Safety Shield: hub RBI threshold must be in [0.50, 0.95]."
        return True, "✅ Safety Shield: parameter audit passed."


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
    """List all currently active open positions from canonical broker-first truth."""
    try:
        from runtime.operator_truth import get_live_kalshi_status

        truth = get_live_kalshi_status()
        positions = truth.get("broker_positions") or []
        drift = truth.get("position_drift") or {}
        if not positions:
            msg = "No active live broker positions."
            if drift.get("has_drift"):
                msg += "\nWarning: broker/database drift is currently present."
            return msg

        lines = ["LIVE BROKER POSITIONS:"]
        for pos in positions:
            held_entry = pos.get("held_side_entry_price")
            if held_entry is None:
                held_entry = pos.get("entry_price")
            lines.append(
                f"  - {pos.get('ticker')} ({pos.get('side')}) | "
                f"Qty: {float(pos.get('qty') or 0):g} | "
                f"Held-side entry: ${float(held_entry or 0.0):.2f}"
            )
        if drift.get("has_drift"):
            lines.append("")
            lines.append("Warning: broker/database drift is currently present.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error retrieving open positions: {e}"


def get_recent_trades(limit: int = 15) -> str:
    """Query the local SQLite database for the most recent trading events."""
    try:
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT ts, symbol, action, qty, price, pnl_usd, notes, contract_side "
            "FROM trades WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (TRADE_DATA_START_DATE, limit),
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

        where_clauses = ["ts >= ?"]
        params: list[Any] = [TRADE_DATA_START_DATE]

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
            trades = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND ts >= ?",
                (int(query_str), TRADE_DATA_START_DATE),
            ).fetchall()
        else:
            trades = conn.execute(
                "SELECT * FROM trades WHERE symbol LIKE ? AND ts >= ? ORDER BY ts DESC LIMIT 10",
                (f"%{query_str}%", TRADE_DATA_START_DATE),
            ).fetchall()

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
            "SELECT * FROM trades WHERE symbol = ? AND ts >= ? ORDER BY ts DESC LIMIT 5",
            (ticker, TRADE_DATA_START_DATE),
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


def get_performance_attribution() -> str:
    """Break down live-era realized PnL by city, contract type, and maker/taker fill.

    A single aggregate win rate hides where the edge actually lives. Uses only
    settlements from POST_PAPER_START_DATE onward (live trading, not paper-lane or
    pre-cutover data) -- this is real trading performance, not a backtest over
    historical model data. Maker/taker classification is inferred by matching each
    settlement's actual fee against the maker-rate and taker-rate fee the exchange
    would have charged at that price and size; it is a reasonable estimate, not an
    exact per-fill record, since settlement rows do not carry a maker/taker flag.
    """
    try:
        from collections import defaultdict

        from config import POST_PAPER_START_DATE, estimate_kalshi_fee_per_contract
        from execution.kalshi_broker import KalshiBroker
        from forecast.weather_contracts import weather_trade_bucket
        from runtime.kalshi_settlement_truth import (
            _parse_session_start,
            city_from_ticker,
            settlement_pnl_usd,
        )

        broker = KalshiBroker()
        if not broker.connect():
            return "Error: could not connect to the broker."

        min_ts = int(_parse_session_start(POST_PAPER_START_DATE).timestamp())
        settlements = broker.get_settlements(min_ts=min_ts)

        def _f(v):
            try:
                return float(v or 0)
            except (TypeError, ValueError):
                return 0.0

        by_city: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
        by_bucket: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
        by_fill: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "fees": 0.0})

        for row in settlements:
            ticker = str(row.get("ticker") or "")
            bucket = weather_trade_bucket(ticker, contract_name=str(row.get("event_ticker") or ticker))
            if bucket == "Other Weather":
                continue

            pnl = settlement_pnl_usd(row)
            is_win = pnl > 0.001

            for d, k in ((by_city, city_from_ticker(ticker)), (by_bucket, bucket)):
                d[k]["n"] += 1
                d[k]["pnl"] += pnl
                if is_win:
                    d[k]["wins"] += 1

            yes_c, no_c = _f(row.get("yes_count_fp")), _f(row.get("no_count_fp"))
            yes_cost, no_cost = _f(row.get("yes_total_cost_dollars")), _f(row.get("no_total_cost_dollars"))
            fee = _f(row.get("fee_cost"))
            count, cost = (yes_c, yes_cost) if yes_c > 0 else (no_c, no_cost)
            if count > 0:
                price = cost / count
                taker_fee = estimate_kalshi_fee_per_contract(price, qty=count, maker=False) * count
                maker_fee = estimate_kalshi_fee_per_contract(price, qty=count, maker=True) * count
                label = "maker" if abs(fee - maker_fee) < abs(fee - taker_fee) else "taker"
            else:
                label = "unknown"
            by_fill[label]["n"] += 1
            by_fill[label]["pnl"] += pnl
            by_fill[label]["fees"] += fee
            if is_win:
                by_fill[label]["wins"] += 1

        def render(d: dict, title: str) -> str:
            lines = [f"By {title}:"]
            for k, v in sorted(d.items(), key=lambda kv: -kv[1]["pnl"]):
                wr = 100.0 * v["wins"] / v["n"] if v["n"] else 0.0
                extra = f" fees=${v['fees']:.2f}" if "fees" in v else ""
                lines.append(f"  {k:10s} n={int(v['n']):3d} win={wr:5.1f}% pnl=${v['pnl']:+7.2f}{extra}")
            return "\n".join(lines)

        return (
            "Current production entry route: taker-only IOC. The fill-type section "
            "below is retrospective classification of historical settlements only.\n\n"
            + "\n\n".join(
                [
                    render(by_city, "city"),
                    render(by_bucket, "contract type"),
                    render(by_fill, "historical fill type (inferred)"),
                ]
            )
        )
    except Exception as e:
        return f"Error computing performance attribution: {e}"


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

        # Fees come from settlement truth, not the trades table. The trades table is
        # incomplete for the live era (its most recent row predates current activity),
        # so summing it understated fees by ~4x.
        fees = float(truth.get("total_fees_usd") or 0.0)

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
        prior_row = conn.execute(
            "SELECT param_value FROM dynamic_system_config WHERE param_key = ?", (key.upper(),)
        ).fetchone()
        prior_value = prior_row[0] if prior_row else "(default from config.py)"
        conn.execute(
            "INSERT INTO dynamic_system_config (param_key, param_value, updated_at, rationale) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(param_key) DO UPDATE SET param_value=excluded.param_value, updated_at=excluded.updated_at, rationale=excluded.rationale",
            (key.upper(), str(value), now_iso, rationale)
        )
        conn.commit()
        conn.close()
        # dynamic_system_config is keyed by param and UPSERTs, so the prior value and
        # its rationale would otherwise be gone the moment this runs. Preserve the
        # change itself in system_events so recall_brain_history can answer "what was
        # this before and why did we change it."
        try:
            from logging_db.trade_logger import log_event

            log_event(
                "INFO", "param_change",
                f"{key.upper()}: {prior_value} -> {value} | rationale: {rationale or '(none given)'}",
            )
        except Exception:
            pass
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


def get_operator_brief() -> str:
    """Return a plain-English owner summary of live health, trading state, and next action."""
    try:
        from runtime import approvals
        from runtime.operator_truth import get_live_kalshi_status, get_release_status

        truth = get_live_kalshi_status()
        release = get_release_status(truth=truth)
        pending = approvals.list_pending()
        policy = truth.get("production_policy") or {}
        execution_policy = policy.get("execution") or {}
        probability_policy = policy.get("probability") or {}
        rbi_policy = policy.get("rbi2") or {}
        risk_policy = policy.get("risk") or {}

        drift = truth.get("position_drift") or {}
        blockers = release.get("top_infrastructure_blockers") or []
        critical_incidents = release.get("critical_incidents") or []
        pending_count = len(pending)
        entries_allowed = bool(release.get("entries_allowed"))
        broker_connected = bool(truth.get("broker_connected"))
        verdict = str(release.get("current_release_verdict") or "UNKNOWN")

        if blockers:
            next_action = f"Investigate blocker: {blockers[0]}"
        elif drift.get("has_drift"):
            next_action = "Reconcile broker vs database positions before trusting local bookkeeping."
        elif critical_incidents:
            next_action = f"Review critical incident: {critical_incidents[0].get('sample_message') or critical_incidents[0].get('source')}"
        elif pending_count:
            next_action = f"Review {pending_count} pending cockpit approval request(s)."
        else:
            next_action = "No immediate operator action is required."

        lines = [
            "OWNER BRIEF",
            f"Trading status: {'ACTIVE' if entries_allowed else 'PAUSED'} ({verdict})",
            (
                "Meaning: the bot is allowed to place new trades right now."
                if entries_allowed
                else "Meaning: the bot is blocked from opening new trades right now."
            ),
            f"Broker connection: {'CONNECTED' if broker_connected else 'DISCONNECTED'}",
            f"Open weather positions: {int(truth.get('broker_positions_count') or 0)}",
            f"Active markets scanning: {int(truth.get('active_markets') or 0)}",
            (
                "Book sync: broker and database agree."
                if not drift.get("has_drift")
                else "Book sync: broker and database do NOT agree."
            ),
            f"Open incidents: {int((release.get('open_incidents') or {}).get('total_open') or 0)}",
            f"Pending change requests: {pending_count}",
            f"Weather data mode: {release.get('provider_mode') or 'unknown'}",
            (
                f"Build: v{policy.get('version') or 'unknown'} "
                f"({policy.get('short_sha') or 'SHA unavailable'})"
            ),
            f"Entry execution: {execution_policy.get('entry_route') or 'unknown'}",
            (
                "Probability path: "
                f"{probability_policy.get('model_path') or 'unknown'} "
                f"({probability_policy.get('physics_method') or 'physics method unknown'})"
            ),
            (
                "RBI 2.0: "
                f"{rbi_policy.get('status') or 'unknown'}; "
                f"{float(rbi_policy.get('observed_days') or 0.0):.1f}/"
                f"{float(rbi_policy.get('minimum_days') or 0.0):g} days, "
                f"{int(rbi_policy.get('independent_event_count') or 0)}/"
                f"{int(rbi_policy.get('required_independent_events') or 0)} official events; "
                "human promotion required."
            ),
            (
                "Risk ceilings: "
                f"{int(risk_policy.get('max_qty_per_position') or 0)} contracts / "
                f"${float(risk_policy.get('base_position_cap_usd') or 0.0):g} base position / "
                f"{float(risk_policy.get('max_risk_per_event_pct') or 0.0):.0%} per event / "
                f"{float(risk_policy.get('max_deployed_pct') or 0.0):.0%} deployed / "
                f"{float(risk_policy.get('minimum_model_headroom_f') or 0.0):g}F headroom."
            ),
        ]
        if blockers:
            lines.append(f"Main blocker: {blockers[0]}")
        lines.append(f"Next thing to look at: {next_action}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error building operator brief: {e}"


def get_trading_readiness_summary(lookback_hours: int = 24) -> str:
    """Explain, in plain English, whether the bot can trade and what is blocking entries."""
    try:
        from runtime import approvals
        from runtime.operator_truth import (
            get_live_kalshi_status,
            get_recent_execution_summary,
            get_recent_veto_summary,
            get_release_status,
        )

        truth = get_live_kalshi_status()
        release = get_release_status(truth=truth)
        vetoes = truth.get("recent_vetoes") or get_recent_veto_summary(lookback_hours=lookback_hours)
        execution = truth.get("recent_execution") or get_recent_execution_summary(lookback_hours=lookback_hours)
        lane = truth.get("forecast_lane") or {}
        blockers = release.get("top_infrastructure_blockers") or []
        pending = approvals.list_pending()

        top_veto = ((vetoes.get("top_reasons") or [{}])[:1] or [{}])[0]
        top_exec = ((execution.get("top_outcomes") or [{}])[:1] or [{}])[0]

        lines = []
        if release.get("entries_allowed"):
            lines.append("The bot is currently allowed to place new trades.")
            lines.append("There is no hard release-gate blocker stopping entries right now.")
        else:
            lines.append("The bot is currently blocked from placing new trades.")
            if blockers:
                lines.append(f"Main hard blocker: {blockers[0]}")

        blocked_reason = str(lane.get("blocked_reason") or "").strip()
        if blocked_reason:
            lines.append(f"Lane-level block reason: {blocked_reason}")

        if top_veto.get("reason"):
            lines.append(
                f"Most common recent pass/fail filter: {top_veto['reason']} "
                f"({int(top_veto.get('count') or 0)} times)."
            )
        else:
            lines.append("Recent veto summary: no repeated strategy filter dominated the lookback window.")

        if top_exec.get("outcome"):
            lines.append(
                f"Most common recent execution issue: {top_exec['outcome']} "
                f"({int(top_exec.get('count') or 0)} times)."
            )
        else:
            lines.append("Recent execution summary: no repeated order-execution failure dominated the lookback window.")

        if pending:
            lines.append(f"Pending cockpit approvals waiting on you: {len(pending)}.")
        else:
            lines.append("There are no pending cockpit approvals waiting on you.")

        if release.get("entries_allowed") and not top_veto.get("reason") and not top_exec.get("outcome"):
            lines.append("Bottom line: the bot appears free to trade and is likely just waiting for a strong enough weather setup.")

        return "\n".join(lines)
    except Exception as e:
        return f"Error building trading readiness summary: {e}"


# ── Chat execution ──────────────────────────────────────────────────


def get_rbi2_status_summary() -> str:
    """Show champion, challenger, validation, and official evidence count."""
    from intelligence.rbi2 import get_rbi2_status
    return json.dumps(get_rbi2_status(db_path=_get_db_path()), indent=2, default=str)


def get_cerebro_brief(status: str = "", limit: int = 12) -> str:
    """Search Cerebro's archived, prospectively scored insights."""
    from intelligence.cerebro import get_cerebro_status, list_experiments, list_insights, list_runs
    status_token = str(status or "").upper().strip()
    insight_statuses = {"ACTIVE", "CONFIRMED", "FALSIFIED", "INCONCLUSIVE"}
    experiment_statuses = {
        "PROPOSED",
        "APPROVED_FOR_SHADOW",
        "SHADOW_ACTIVE",
        "ACTION_PENDING",
        "COMPLETED_CONFIRMED",
        "COMPLETED_FALSIFIED",
        "COMPLETED_INCONCLUSIVE",
    }
    run_statuses = {"RUNNING", "COMPLETE", "FAILED"}
    return json.dumps({
        "status": get_cerebro_status(db_path=_get_db_path()),
        "insights": list_insights(status=status_token if status_token in insight_statuses else "", limit=limit, db_path=_get_db_path()),
        "experiments": list_experiments(status=status_token if status_token in experiment_statuses else "", limit=limit, db_path=_get_db_path()),
        "runs": list_runs(limit=min(limit, 10), status=status_token if status_token in run_statuses else "", db_path=_get_db_path()),
    }, indent=2, default=str)


def recall_brain_history(query: str = "", limit: int = 10) -> str:
    """Search past JARVIS/Telegram conversations and parameter changes.

    Use this for questions like "what did we decide about X last week" or "has this
    come up before" -- both surfaces log every turn to the same history, so this
    covers conversations that happened on the other surface too.
    """
    try:
        conn = sqlite3.connect(_get_db_path())
        conn.row_factory = sqlite3.Row
        sql = "SELECT ts, source, message FROM system_events WHERE source LIKE 'brain:%' OR source = 'param_change'"
        params: list[Any] = []
        if query:
            sql += " AND message LIKE ?"
            params.append(f"%{query}%")
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        if not rows:
            return f"No history found matching '{query}'." if query else "No brain history recorded yet."
        return "\n\n".join(f"[{r['ts']}] ({r['source']})\n{r['message']}" for r in rows)
    except Exception as e:
        return f"Error recalling history: {e}"


def request_change(
    action: str,
    enabled: bool | None = None,
    key: str = "",
    value: str = "",
    artifact_id: str = "",
    insight_id: str = "",
    rationale: str = "",
) -> str:
    """Propose a system change for cockpit approval. Does not change anything itself.

    Use this on a read-only surface (Telegram) when the operator wants to act on a
    finding but write access is not available here. Valid actions:
    update_system_parameter (pass key, value), promote_release
    (no extra params), promote_rbi_artifact (pass artifact_id), or
    create_cerebro_experiment (pass insight_id). The change only takes effect if
    approved from the cockpit.
    """
    from runtime import approvals

    params = {}
    if enabled is not None:
        params["enabled"] = enabled
    if key:
        params["key"] = key
    if value:
        params["value"] = value
    if artifact_id:
        params["artifact_id"] = artifact_id
    if insight_id:
        params["insight_id"] = insight_id
    if rationale:
        params["rationale"] = rationale
    return approvals.request_change(action, params, rationale)


def propose_cerebro_experiment(insight_id: str, rationale: str = "") -> str:
    """Queue a cockpit approval to create a shadow-only experiment from an insight."""
    return request_change(
        "create_cerebro_experiment",
        insight_id=insight_id,
        rationale=rationale or "Operator selected a Cerebro insight for governed shadow follow-up.",
    )


def list_cerebro_experiments(status: str = "", limit: int = 12) -> str:
    """List archived Cerebro experiments and recent intelligence-cycle runs."""
    from intelligence.cerebro import list_experiments, list_runs

    return json.dumps(
        {
            "experiments": list_experiments(status=status, limit=limit, db_path=_get_db_path()),
            "runs": list_runs(limit=min(limit, 10), db_path=_get_db_path()),
        },
        indent=2,
        default=str,
    )


def list_pending_approvals() -> str:
    """List proposed changes waiting for cockpit approval."""
    from runtime import approvals

    pending = approvals.list_pending()
    if not pending:
        return "No pending approvals."
    lines = [
        f"#{p['id']} [{p['surface']}] {p['action']}({p['params_json']}) -- {p['rationale'] or 'no rationale given'} "
        f"(queued {p['created_at']})"
        for p in pending
    ]
    return "\n".join(lines)


def get_entry_funnel(lookback_hours: int = 24) -> str:
    """Why the bot did or did not enter trades recently: candidates, vetoes, outcomes.

    Use this when asked why there are no new trades, whether the bot is still working,
    or what is blocking entries.
    """
    from notifications import agent_tools as at

    parts = []
    try:
        from runtime.operator_truth import get_release_status as _rs

        rs = _rs() or {}
        parts.append(
            f"Release gate: {rs.get('current_release_verdict')} | "
            f"entries_allowed={rs.get('entries_allowed')} | "
            f"blockers={rs.get('top_infrastructure_blockers') or []}"
        )
    except Exception as e:
        parts.append(f"Release gate: unavailable ({e})")

    parts.append("--- Vetoes ---\n" + str(at.get_recent_veto_summary()))
    parts.append("--- Execution outcomes ---\n" + str(at.get_recent_execution_summary()))
    return "\n\n".join(parts)


def show_panel(name: str) -> str:
    """Display a data panel in the cockpit console.

    Valid names: alerts (release blockers and broker-vs-ledger drift), open_book
    (live positions, exposure, mark PnL), risk (risk ceilings in force), runtime
    (disk/db/quote-cache health, hub exposure, veto tape), events (raw system event
    tape), trades (recent trade rows). Use this when the operator asks to see or be
    shown something, rather than describing the data in prose.
    """
    from dashboard.panels import PANEL_NAMES

    key = str(name or "").strip().lower()
    if key not in PANEL_NAMES:
        return f"Unknown panel '{name}'. Valid panels: {', '.join(PANEL_NAMES)}."
    try:
        import streamlit as st

        active = st.session_state.setdefault("active_panels", [])
        if key not in active:
            active.append(key)
        return f"Panel '{key}' is now displayed in the console."
    except Exception:
        # Reached from a non-Streamlit surface (e.g. Telegram); nothing to render.
        return f"Panel '{key}' can only be displayed in the cockpit console."


def run_jarvis_chat(messages: list[dict]) -> str:
    """Cockpit entrypoint. Delegates to the shared diagnostic-only brain.

    The tool-calling loop, model resolution and system prompt now live in
    runtime.brain, shared with the Telegram operator. This wrapper keeps the old
    signature so streamlit_app.py did not have to change.
    """
    from runtime import brain

    return brain.ask(messages, surface=brain.COCKPIT)
