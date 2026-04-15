"""
dashboard/data/health.py — System health, heartbeat, error rate, ML gate status.
"""

import re
from datetime import datetime, timedelta

from db import _q, _q1
from formatters import _ts_age_s


def get_health_status() -> dict:
    """Parse the last health_check event from system_events."""
    row = _q1("""
        SELECT ts, level, message FROM system_events
        WHERE source = 'health_check'
        ORDER BY rowid DESC LIMIT 1
    """)
    if not row:
        return {
            "status": "UNKNOWN",
            "score": 0,
            "total": 7,
            "ts": None,
            "message": "No health check data yet",
        }
    msg = row.get("message", "")
    ts = row.get("ts", "")
    m = re.search(r"(\d+)/(\d+)", msg)
    score = int(m.group(1)) if m else 0
    total = int(m.group(2)) if m else 6
    # Use bracket-enclosed match — "UNHEALTHY" contains "HEALTHY" as a substring
    # so a bare substring check would misread UNHEALTHY events as HEALTHY.
    if "[HEALTHY]" in msg.upper():
        status = "HEALTHY"
    elif "DEGRADED" in msg.upper():
        status = "DEGRADED"
    else:
        status = "UNHEALTHY"
    return {"status": status, "score": score, "total": total, "ts": ts, "message": msg}


def get_heartbeat_age() -> int:
    """Seconds since last heartbeat write."""
    row = _q1("""
        SELECT ts FROM system_events
        WHERE source = 'heartbeat'
        ORDER BY rowid DESC LIMIT 1
    """)
    if not row or not row.get("ts"):
        return 9999
    return _ts_age_s(row["ts"])


def get_error_rate_1h() -> int:
    """Count of ERROR events in last 60 minutes, excluding health_check source.
    health_check failures are surfaced live via get_health_check_failures() instead."""
    cutoff = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    r = _q1(
        "SELECT COUNT(*) AS n FROM system_events "
        "WHERE level='ERROR' AND source != 'health_check' AND ts >= ?",
        (cutoff,),
    )
    return r.get("n") or 0


def _fingerprint_msg(msg: str) -> str:
    """Strip numbers/timestamps to produce a stable group key."""
    s = re.sub(r"\d+(\.\d+)?[mhsdMH]?", "#", msg)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:100]


def _classify_error(source: str, message: str) -> dict:
    """
    Return {category, fix_type, fix_prompt} for a given error.
    fix_type is either 'Claude Code' (deep code changes) or 'Codex' (verification/proof).
    """
    s, m = source.lower(), message.lower()

    # ── Health check sub-failures ──────────────────────────────────────────────
    if s == "health_check":
        if "stagnant" in m:
            return {
                "category": "Dead-Money Position",
                "fix_type": "Claude Code",
                "fix_prompt": (
                    "The dead-money exit (priority 7, scheduler/v10_runner.py ~line 1544) "
                    "fires when: held >24h AND price drift <0.5×ATR_at_entry AND no trailing AND no scale-out.\n"
                    "If a position is >24h old and barely moved, it should be closing automatically.\n"
                    "Check: tail -f logs/bot.log | grep DEAD_MONEY to watch for the close log.\n"
                    "If not firing: verify entry_ts and atr_at_entry are populated in the position dict "
                    "(perps_engine.load_positions_from_db, line ~443-445)."
                ),
            }
        if "heartbeat" in m or "liveness" in m:
            return {
                "category": "Scan Heartbeat Missing",
                "fix_type": "Claude Code",
                "fix_prompt": (
                    "The scan loop is not writing heartbeats on schedule. "
                    "1) Verify config.py: CRYPTO_SCAN_INTERVAL_SECONDS = 300 (not 15). "
                    "2) In scheduler/v10_runner.py, confirm scan_and_trade() calls "
                    "log_event('INFO', 'heartbeat', ...) on every successful cycle. "
                    "3) Check whether exit_monitor is blocking the scan loop — 18+ positions "
                    "each taking 15s of ML feature building = 4+ min per cycle."
                ),
            }
        if "modelstore" in m or "ml gate" in m or "ml tower" in m or "model" in m:
            return {
                "category": "ML Model Unavailable",
                "fix_type": "Codex",
                "fix_prompt": (
                    'python3 -c "from ml.model_store import ModelStore, MODELS_DIR; '
                    'import os; print(os.listdir(MODELS_DIR))"\n'
                    "If no .pkl files: ML is at neutral 50.0 until 30+ clean trades — expected.\n"
                    "If import fails: check ml/model_store.py for xgboost/lightgbm import errors.\n"
                    "python3 -m pytest tests/proof/ -k ml -v"
                ),
            }
        if "attribution" in m:
            return {
                "category": "Trade Attribution Gap",
                "fix_type": "Codex",
                "fix_prompt": (
                    "python3 -c \"import sqlite3; c=sqlite3.connect('logs/trades.db'); "
                    "print(c.execute('SELECT COUNT(*) FROM trade_attribution WHERE "
                    'created_at >= datetime(\\"now\\",\\"-24 hours\\")\').fetchone())"\n'
                    "If count is low: check learning/post_trade_analyzer.py — "
                    "it should write to trade_attribution on every close.\n"
                    "python3 -m pytest tests/proof/ -k attribution -v"
                ),
            }
        if "error rate" in m or "errors in last" in m:
            return {
                "category": "High Error Rate (10+/hr)",
                "fix_type": "Claude Code",
                "fix_prompt": (
                    "Query which sources are throwing errors:\n"
                    "python3 -c \"import sqlite3; c=sqlite3.connect('logs/trades.db'); "
                    "rows=c.execute('SELECT source, COUNT(*) n FROM system_events WHERE "
                    "level=\\'ERROR\\' AND source!=\\'health_check\\' AND "
                    "ts >= datetime(\\'now\\',\\'-1 hour\\') GROUP BY source ORDER BY n DESC').fetchall(); "
                    '[print(r) for r in rows]"\n'
                    "Then read the top error source file and fix the root cause."
                ),
            }
        if "halted" in m:
            return {
                "category": "Risk Manager Halted",
                "fix_type": "Claude Code",
                "fix_prompt": (
                    'python3 -c "from risk.risk_manager import get_risk_manager; '
                    "rm=get_risk_manager(); "
                    "print('halted:', rm.is_halted, 'reason:', getattr(rm, 'halt_reason', 'none'))\"\n"
                    "Kill-switch threshold is 75% of ACCOUNT_SIZE = $3,750 on a $5K account. "
                    "If falsely halted: read risk_manager.py.halt() and kill_switch.py.check_balance() "
                    "to find the bad condition. Fix the check, not the threshold."
                ),
            }

    # ── IBKR / TWS connection ─────────────────────────────────────────────────
    if s == "ibkr" or "ibkr" in m or "tws" in m or "7497" in m:
        return {
            "category": "IBKR / TWS Disconnected",
            "fix_type": "Claude Code",
            "fix_prompt": (
                "1. Confirm TWS is open and the API is enabled:\n"
                "   TWS → Edit → Global Configuration → API → Settings\n"
                "   ✓ Enable ActiveX and Socket Clients  ✓ Port: 7497  ✗ Read-Only API\n"
                "2. Test connection:\n"
                'python3 -c "from execution.ibkr_broker import get_ibkr_broker; '
                "b=get_ibkr_broker(); print('connected:', b.connect())\"\n"
                "3. If TWS shows 'Waiting for connection': click Trust on the API popup in TWS.\n"
                "4. Check FUTURES_ENABLED=true is in .env."
            ),
        }

    # ── Scanner / exchange API errors ─────────────────────────────────────────
    if "scanner" in s or s in ("kraken", "binance", "hyperliquid"):
        if any(
            kw in m
            for kw in (
                "timeout",
                "connection",
                "api",
                "request",
                "ssl",
                "http",
                "error",
            )
        ):
            return {
                "category": "Exchange API Error",
                "fix_type": "Claude Code",
                "fix_prompt": (
                    "scanner.py failed to fetch from an exchange endpoint. "
                    "Check: curl -s 'https://futures.kraken.com/derivatives/api/v3/tickers' | head -c 200\n"
                    "If exchange is down: scanner.py already handles partial failures "
                    "(skips failed sources, continues with others). "
                    "If persistent: look at scanner.py for the failing _fetch_* function "
                    "and consider increasing the request timeout constant."
                ),
            }

    # ── ML / learning pipeline ────────────────────────────────────────────────
    if any(
        kw in s
        for kw in ("ml", "learning", "model", "feature", "trainer", "walk_forward")
    ):
        return {
            "category": "ML / Learning Pipeline",
            "fix_type": "Codex",
            "fix_prompt": (
                "python3 -m pytest tests/proof/ -v -k ml\n"
                "python3 -c \"from ml.feature_builder import build_features; print('import ok')\"\n"
                "Check logs/bot.log for the full stack trace. Common causes:\n"
                "• Missing model pkl files (OK until 30+ clean trades)\n"
                "• xgboost/lightgbm version mismatch (pip install xgboost lightgbm)\n"
                "• Insufficient training data for walk-forward split"
            ),
        }

    # ── Risk / kill switch ────────────────────────────────────────────────────
    if any(kw in s for kw in ("risk", "kill_switch", "drawdown", "risk_manager")):
        return {
            "category": "Risk / Kill Switch",
            "fix_type": "Claude Code",
            "fix_prompt": (
                'python3 -c "from kill_switch import check_balance; print(check_balance())"\n'
                'python3 -c "from risk.risk_manager import get_risk_manager; '
                "rm=get_risk_manager(); print('halted:', rm.is_halted)\"\n"
                "Kill-switch fires at balance < $3,750 (75% of $5K ACCOUNT_SIZE). "
                "If halted: read kill_switch.py and risk_manager.py halt() call sites "
                "to understand what triggered it before unhalting."
            ),
        }

    # ── Execution / broker ────────────────────────────────────────────────────
    if any(kw in s for kw in ("broker", "execution", "perp", "perps_engine", "ibkr")):
        return {
            "category": "Execution / Broker",
            "fix_type": "Claude Code",
            "fix_prompt": (
                'python3 -c "from execution.binance_broker import BinanceBroker; '
                "b=BinanceBroker(); print('paper:', b.paper)\"\n"
                "Paper mode needs no API keys. Live mode needs BINANCE_API_KEY + SECRET in .env.\n"
                "For IBKR/MES: TWS must be running on port 7497 with API enabled.\n"
                "Check perps_engine.py for the specific failing method + traceback in logs/bot.log."
            ),
        }

    # ── Default / unknown ─────────────────────────────────────────────────────
    return {
        "category": "System Error",
        "fix_type": "Claude Code",
        "fix_prompt": (
            f"Error from source='{source}'. Find where it's logged:\n"
            f"grep -rn 'source=\"{source}\"\\|source=\\'{source}\\'' "
            "scheduler/ risk/ learning/ monitoring/ notifications/\n"
            "Then read that file and the full message in logs/bot.log to diagnose."
        ),
    }


def get_health_check_failures() -> list:
    """
    Parse the *current* health check state from the most recent health_check event.
    Always reflects live bot state — never stale historical DB records.
    Returns one classified dict per failing check, or [] when healthy.
    """
    health = get_health_status()
    if health["status"] == "HEALTHY":
        return []

    msg = health.get("message", "")
    ts = health.get("ts", "")
    fail_idx = msg.find("FAIL:")
    if fail_idx == -1:
        return []

    result = []
    for part in msg[fail_idx + 5 :].split(" | "):
        part = part.strip()
        if not part:
            continue
        # Format: "check_name: detail text"
        colon_idx = part.find(":")
        if colon_idx == -1:
            continue
        check_name = part[:colon_idx].strip()
        detail = part[colon_idx + 1 :].strip()
        cls = _classify_error("health_check", detail)
        result.append(
            {
                "source": check_name,
                "sample_msg": detail[:150],
                "count": 1,
                "ts_latest": ts,
                "live": True,  # flag: this is current state, not historical
                **cls,
            }
        )
    return result


def get_recent_errors_detail(hours: int = 1, limit: int = 15) -> list:
    """
    Fetch recent ERROR events from non-health_check sources, deduplicated by fingerprint.
    health_check failures are handled separately by get_health_check_failures() so they
    always reflect live state rather than 1-hour of potentially stale DB records.
    Returns list of dicts sorted by count desc:
      {source, count, sample_msg, ts_latest, category, fix_type, fix_prompt}
    """
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    rows = _q(
        "SELECT ts, source, message FROM system_events "
        "WHERE level='ERROR' AND source != 'health_check' AND ts >= ? ORDER BY id DESC LIMIT ?",
        (cutoff, limit * 6),
    )

    groups: dict = {}
    for row in rows:
        src = row.get("source", "unknown")
        msg = row.get("message", "")
        ts = row.get("ts", "")
        key = f"{src}::{_fingerprint_msg(msg)}"
        if key not in groups:
            groups[key] = {
                "source": src,
                "count": 0,
                "sample_msg": msg[:150],
                "ts_latest": ts,
            }
        groups[key]["count"] += 1

    result = []
    for g in sorted(groups.values(), key=lambda x: x["count"], reverse=True)[:limit]:
        classification = _classify_error(g["source"], g["sample_msg"])
        result.append({**g, **classification})

    return result


def get_restart_count_24h() -> int:
    """Number of bot start events in last 24h."""
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    r = _q1(
        "SELECT COUNT(*) AS n FROM system_events WHERE ts >= ? AND message LIKE '%Bot started%'",
        (cutoff,),
    )
    return r.get("n") or 0


def get_ml_status():
    r = _q1("SELECT COUNT(*) AS n FROM ml_feature_snapshots")
    return {"snapshots": r.get("n") or 0, "min_needed": 30}
