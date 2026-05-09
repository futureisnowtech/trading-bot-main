"""
dashboard/data/execution.py — Execution quality, failure modes, recent events.
"""

from datetime import datetime, timedelta

import db as _db

_q = _db._q
_q1 = _db._q1= _db.clamp_metrics_cutoff = getattr(_db, "clamp_metrics_cutoff", lambda s: s)
get_current_strategy_start_date = getattr(
    _db,
    "get_current_strategy_start_date",
    lambda normalized=True: "2026-04-24 00:00:00" if normalized else "2026-04-24T00:00:00",
)
from formatters import _time_ago

# Normalized 7-day cutoff helper — avoids the ISO 'T'-separator false-positive bug
# where raw `ts >= ?` treats all same-day rows as in-window because ASCII('T') > ASCII(' ').
_TS_NORM = "datetime(replace(substr(ts,1,19),'T',' '))"


def _cutoff_7d() -> str:
    raw = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    return clamp_metrics_cutoff(raw)


def get_execution_stats() -> dict:
    """MAE/MFE efficiency, fee trap rate, hold duration from trade_attribution."""
    r = _q1(
        f"""
        SELECT
            AVG(ABS(COALESCE(mae_pct, 0)))  AS avg_mae,
            AVG(COALESCE(mfe_pct, 0))       AS avg_mfe,
            COUNT(*)                         AS total,
            SUM(CASE WHEN is_fee_trap=1 THEN 1 ELSE 0 END) AS fee_traps,
            AVG(CASE WHEN won=1 THEN hold_minutes END) AS avg_hold_win,
            AVG(CASE WHEN won=0 THEN hold_minutes END) AS avg_hold_loss,
            AVG(CASE WHEN won=1 AND mfe_pct > 0 THEN pnl_pct / mfe_pct END) AS exit_eff
        FROM trade_attribution
        WHERE paper = 0
          AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper','paper_v10')
          AND datetime(replace(substr(COALESCE(created_at, entry_ts, ''),1,19),'T',' ')) >= datetime(?)
    """,
        (get_current_strategy_start_date(normalized=True),),
    )
    total = r.get("total") or 0
    avg_mae = r.get("avg_mae") or 0.0
    avg_mfe = r.get("avg_mfe") or 0.0
    fee_traps = r.get("fee_traps") or 0
    entry_score = (
        max(0.0, 10.0 * (1.0 - min(avg_mae / 0.015, 1.0))) if avg_mae >= 0 else 5.0
    )
    exit_eff_raw = r.get("exit_eff") or 0.0
    exit_score = min(10.0, max(0.0, exit_eff_raw * 10.0))
    return {
        "total": total,
        "avg_mae_pct": avg_mae * 100,
        "avg_mfe_pct": avg_mfe * 100,
        "entry_score": entry_score,
        "exit_score": exit_score,
        "fee_trap_rate": fee_traps / total * 100 if total else 0.0,
        "fee_traps": fee_traps,
        "avg_hold_win_min": r.get("avg_hold_win") or 0.0,
        "avg_hold_loss_min": r.get("avg_hold_loss") or 0.0,
    }


def get_failure_counts() -> list:
    """Return categorized failure counts from trade_attribution + system_events.

    All time-window queries use normalized datetime comparison to avoid the
    ISO-timestamp 'T'-separator false-positive that inflated counts to 751+.
    Execution errors are collapsed into distinct incidents (GROUP BY fingerprint)
    rather than raw row counts.
    """
    cutoff_7d = _cutoff_7d()
    failures = []

    # ── Fee Trap ──────────────────────────────────────────────────────────────
    r = _q1(
        f"""SELECT COUNT(*) AS n, MAX(entry_ts) AS last FROM trade_attribution
           WHERE is_fee_trap=1
             AND paper=0
             AND {_TS_NORM.replace("ts", "entry_ts")} >= ?
             AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper','paper_v10')""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Fee Trap",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Fees consumed >50% of gross P&L move",
        }
    )

    # ── Quick Stop ────────────────────────────────────────────────────────────
    r = _q1(
        f"""SELECT COUNT(*) AS n, MAX(entry_ts) AS last FROM trade_attribution
           WHERE exit_type='stop_hit' AND COALESCE(hold_minutes,999) < 30
             AND paper=0
             AND {_TS_NORM.replace("ts", "entry_ts")} >= ?
             AND source NOT IN ('backtest','pre_v10_contaminated','bybit_paper','paper_v10')""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Quick Stop (<30m)",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Stop hit within 30 min of entry (stop hunt / bad timing)",
        }
    )

    # ── Execution Error — distinct incidents, excluding archived-lane noise ───
    # Collapse repeated identical errors into distinct incidents via GROUP BY.
    # Excludes IBKRBroker (archived MES lane) and health_check (shown separately).
    r = _q1(
        f"""SELECT COUNT(*) AS n, MAX(ts) AS last FROM (
               SELECT MAX(ts) AS ts
               FROM system_events
               WHERE level='ERROR'
                 AND source NOT IN ('IBKRBroker','health_check')
                 AND {_TS_NORM} >= ?
               GROUP BY source, substr(message,1,80)
           )""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Execution Error",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "CRIT" if (r.get("n") or 0) > 0 else "OK",
            "Description": "Distinct error types from broker/system (not MES/IBKR)",
        }
    )

    # ── Scan Dropout ──────────────────────────────────────────────────────────
    r = _q1(
        f"""SELECT COUNT(*) AS n, MAX(ts) AS last FROM system_events
           WHERE source='heartbeat' AND message LIKE '%candidates=0%'
             AND {_TS_NORM} >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Scan Dropout (0 cands)",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Scanner returned 0 candidates — possible connectivity issue",
        }
    )

    # ── Duplicate Close ───────────────────────────────────────────────────────
    r = _q1(
        f"""SELECT COUNT(*) AS n, MAX(ts) AS last FROM system_events
           WHERE message LIKE '%duplicate close%'
             AND {_TS_NORM} >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Duplicate Close",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Idempotency guard triggered — duplicate close attempt",
        }
    )

    # ── Economics Veto — parentheses fix prevents OR precedence bug ───────────
    r = _q1(
        f"""SELECT COUNT(*) AS n, MAX(ts) AS last FROM system_events
           WHERE (source='economics_gate' OR message LIKE '%ECONOMICS VETO%')
             AND {_TS_NORM} >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Economics Veto",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "INFO",
            "Description": "Pre-trade EV veto fired (expected; high rate = opportunity cost)",
        }
    )

    # ── Stagnant Position ─────────────────────────────────────────────────────
    r = _q1(
        f"""SELECT COUNT(*) AS n, MAX(ts) AS last FROM system_events
           WHERE message LIKE '%stagnant%'
             AND {_TS_NORM} >= ?""",
        (cutoff_7d,),
    )
    failures.append(
        {
            "Category": "Stagnant Position",
            "Count (7d)": r.get("n") or 0,
            "Last": _time_ago(r.get("last") or ""),
            "Severity": "WARN",
            "Description": "Position open >48h with no movement",
        }
    )

    return failures


def get_recent_events(limit=20):
    return _q(
        """SELECT ts, level, source, message FROM system_events
           WHERE source NOT IN ('IBKRBroker')
           ORDER BY rowid DESC LIMIT ?""",
        (limit,),
    )
