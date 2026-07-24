"""
forecast/firewall.py — v20 Stateful Firewall (SPEC §5.4)

Four enforcement gates wired into runner.py BEFORE any entry evaluation:

    (a) Re-entry lockout       — SPEC §5.4a
        After any loss-realizing exit on ticker c, block fresh entries
        until after settlement.

    (b) Oscillation breaker    — SPEC §5.4b
        Track completed BUY->SELL round trips per ticker in a trailing
        6-hour window. Halt at >= 2 round trips. This rule would have
        capped the JUN-22 KXRAINNYC doom loop at cycle 2 (-$57 vs -$851).

    (c) Quote coherence invariant — SPEC §5.4c
        If entry-view ask and exit-view bid differ by > MAX_SPREAD_DOLLARS,
        halt the ticker and log quote_coherence_violation.

    (d) Daily kill switch       — SPEC §5.4d
        If day_loss > min(3% * bankroll, 5 * trailing_30d_mean_daily_edge):
        block entries until next UTC day. No automatic intraday resume.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import DB_PATH, KALSHI_MAX_SPREAD_DOLLARS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

_GLOBAL_TICKER: str = "__global__"
_OSCILLATION_WINDOW_HOURS: int = 6
_OSCILLATION_TRIP_LIMIT: int = 2   # halt when round_trips >= this; SPEC §5.4b

_DDL_FIREWALL_STATE: str = """
CREATE TABLE IF NOT EXISTS firewall_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL UNIQUE,
    lockout_until   TEXT,
    halted_reason   TEXT,
    entries_allowed INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT NOT NULL
);
"""

_DDL_FIREWALL_ROUND_TRIPS: str = """
CREATE TABLE IF NOT EXISTS firewall_round_trips (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker    TEXT NOT NULL,
    closed_at TEXT NOT NULL
);
"""

_DDL_FIREWALL_ROUND_TRIPS_IDX: str = (
    "CREATE INDEX IF NOT EXISTS idx_frt_ticker_ts "
    "ON firewall_round_trips(ticker, closed_at);"
)

_DDL_FIREWALL_DAY_PNL: str = """
CREATE TABLE IF NOT EXISTS firewall_day_pnl (
    day_utc    TEXT PRIMARY KEY,
    realized   REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL
);
"""


def ensure_firewall_tables(db_path: str | None = None) -> None:
    """
    Create all three firewall tables if they do not yet exist.
    Called from forecast/db.py::init_forecast_db(). SPEC §5.4
    """
    path = str(db_path or DB_PATH)
    with sqlite3.connect(path) as conn:
        conn.execute(_DDL_FIREWALL_STATE)
        conn.execute(_DDL_FIREWALL_ROUND_TRIPS)
        conn.execute(_DDL_FIREWALL_ROUND_TRIPS_IDX)
        conn.execute(_DDL_FIREWALL_DAY_PNL)
        conn.commit()


# ---------------------------------------------------------------------------
# Gate (a): Re-entry lockout — SPEC §5.4a
# ---------------------------------------------------------------------------

_LOSS_EXIT_TYPES: frozenset[str] = frozenset({
    "salvage_exit",
    "bust_exit",
    "manual_exit",
    "model_invalidation_exit",
    "firewall_forced_exit",
})


def record_exit_lockout(
    ticker: str,
    settlement_time: str | None,
    exit_type: str,
    *,
    db_path: str | None = None,
) -> None:
    """
    After any loss-realizing exit on ticker c, persist a lockout record
    so that fresh entries on c are blocked until after settlement.
    Only loss-realizing exit types trigger a lockout. SPEC §5.4a

    Args:
        ticker:          Kalshi contract ticker.
        settlement_time: ISO UTC timestamp of contract settlement, or None.
        exit_type:       Exit classification string from runner.py.
        db_path:         Override DB path for tests.
    """
    if exit_type not in _LOSS_EXIT_TYPES:
        return

    path = str(db_path or DB_PATH)
    now_utc = datetime.now(timezone.utc).isoformat()
    lockout = str(settlement_time or "")

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO firewall_state (ticker, lockout_until, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                lockout_until = excluded.lockout_until,
                updated_at    = excluded.updated_at
            """,
            (ticker, lockout, now_utc),
        )
        conn.commit()

    logger.info(
        "[Firewall §5.4a] Re-entry lockout set: ticker=%s exit=%s until=%s",
        ticker, exit_type, lockout or "open",
    )


def check_reentry_lockout(
    ticker: str,
    *,
    db_path: str | None = None,
) -> tuple[bool, str]:
    """
    Check whether a re-entry lockout is active for ticker. SPEC §5.4a

    Returns:
        (allowed, veto_reason) — allowed=True means no lockout is active.
    """
    path = str(db_path or DB_PATH)
    now_utc = datetime.now(timezone.utc).isoformat()

    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT lockout_until FROM firewall_state WHERE ticker = ?",
                (ticker,),
            ).fetchone()
    except Exception as exc:
        logger.warning("[Firewall §5.4a] DB read error for %s: %s", ticker, exc)
        return True, ""

    if not row:
        return True, ""

    lockout_until = str(row[0] or "").strip()
    if lockout_until and now_utc < lockout_until:
        return False, f"firewall_reentry_lockout (locked_until={lockout_until})"
    return True, ""


# ---------------------------------------------------------------------------
# Gate (b): Oscillation breaker — SPEC §5.4b
# ---------------------------------------------------------------------------


def record_round_trip(ticker: str, *, db_path: str | None = None) -> None:
    """
    Record a completed BUY->SELL round trip for oscillation tracking.
    Called from runner.py after a position is fully closed. SPEC §5.4b

    Args:
        ticker:  Kalshi contract ticker.
        db_path: Override DB path for tests.
    """
    path = str(db_path or DB_PATH)
    now_utc = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO firewall_round_trips (ticker, closed_at) VALUES (?, ?)",
            (ticker, now_utc),
        )
        conn.commit()


def check_oscillation_breaker(
    ticker: str,
    *,
    db_path: str | None = None,
) -> tuple[bool, str]:
    """
    Count completed round trips in trailing 6h window for ticker.
    If count >= 2, halt ticker and return (False, reason). SPEC §5.4b

    Returns:
        (allowed, veto_reason) — allowed=True means oscillation limit not hit.
    """
    path = str(db_path or DB_PATH)
    window_start = (
        datetime.now(timezone.utc) - timedelta(hours=_OSCILLATION_WINDOW_HOURS)
    ).isoformat()

    try:
        with sqlite3.connect(path) as conn:
            count: int = conn.execute(
                """
                SELECT COUNT(*) FROM firewall_round_trips
                WHERE ticker = ? AND closed_at >= ?
                """,
                (ticker, window_start),
            ).fetchone()[0]
    except Exception as exc:
        logger.warning("[Firewall §5.4b] DB read error for %s: %s", ticker, exc)
        return True, ""

    if count >= _OSCILLATION_TRIP_LIMIT:
        reason = (
            f"firewall_oscillation_breaker "
            f"({count} round_trips in trailing {_OSCILLATION_WINDOW_HOURS}h)"
        )
        _write_halt_state(ticker, reason, db_path=db_path)
        logger.error("[Firewall §5.4b] TICKER HALTED: %s — %s", ticker, reason)
        return False, reason

    return True, ""


def _write_halt_state(ticker: str, reason: str, *, db_path: str | None = None) -> None:
    """Persist halt reason and entries_allowed=0 for a ticker. Internal helper."""
    path = str(db_path or DB_PATH)
    now_utc = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO firewall_state
                (ticker, halted_reason, entries_allowed, updated_at)
            VALUES (?, ?, 0, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                halted_reason   = excluded.halted_reason,
                entries_allowed = 0,
                updated_at      = excluded.updated_at
            """,
            (ticker, reason, now_utc),
        )
        conn.commit()


def is_ticker_halted(ticker: str, *, db_path: str | None = None) -> tuple[bool, str]:
    """
    Check whether ticker has been explicitly halted by oscillation
    breaker or coherence invariant. SPEC §5.4b / §5.4c

    Returns:
        (halted, reason) — halted=True means the ticker must not be entered.
    """
    path = str(db_path or DB_PATH)
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                """
                SELECT entries_allowed, halted_reason
                FROM   firewall_state WHERE ticker = ?
                """,
                (ticker,),
            ).fetchone()
        if row and int(row[0]) == 0 and row[1]:
            return True, str(row[1])
    except Exception as exc:
        logger.warning("[Firewall] halt check error for %s: %s", ticker, exc)
    return False, ""


# ---------------------------------------------------------------------------
# Gate (c): Quote coherence invariant — SPEC §5.4c
# ---------------------------------------------------------------------------


def check_quote_coherence(
    ticker: str,
    entry_ask: float,
    exit_bid: float,
    *,
    max_spread_dollars: float | None = None,
    db_path: str | None = None,
) -> tuple[bool, str]:
    """
    Validate that entry-view ask and exit-view bid for the same contract
    do not diverge by more than MAX_SPREAD_DOLLARS. SPEC §5.4c

    If the spread is too wide, halt the ticker and log coherence_violation.

    Args:
        ticker:             Kalshi contract ticker.
        entry_ask:          Ask price from cycle-start snapshot.
        exit_bid:           Bid price from exit/monitor path.
        max_spread_dollars: Override for tests; defaults to config value.
        db_path:            Override DB path for tests.

    Returns:
        (coherent, veto_reason) — coherent=True means quotes are consistent.
    """
    limit: float = (
        float(max_spread_dollars)
        if max_spread_dollars is not None
        else float(KALSHI_MAX_SPREAD_DOLLARS)
    )
    diff: float = abs(float(entry_ask) - float(exit_bid))

    if diff > limit:
        reason = (
            f"quote_coherence_violation "
            f"(|ask={entry_ask:.4f} - bid={exit_bid:.4f}|={diff:.4f} > {limit:.2f})"
        )
        _write_halt_state(ticker, reason, db_path=db_path)
        logger.error("[Firewall §5.4c] TICKER HALTED: %s — %s", ticker, reason)
        return False, reason

    return True, ""


# ---------------------------------------------------------------------------
# Gate (d): Daily kill switch — SPEC §5.4d
# ---------------------------------------------------------------------------


def record_realized_pnl(pnl_usd: float, *, db_path: str | None = None) -> None:
    """
    Accumulate intraday realized PnL in the UTC-day bucket.
    Called from runner.py after every position close. SPEC §5.4d

    Args:
        pnl_usd: Realized PnL in USD (negative = loss).
        db_path: Override DB path for tests.
    """
    path = str(db_path or DB_PATH)
    day_utc: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_utc: str = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO firewall_day_pnl (day_utc, realized, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(day_utc) DO UPDATE SET
                realized   = realized + excluded.realized,
                updated_at = excluded.updated_at
            """,
            (day_utc, float(pnl_usd), now_utc),
        )
        conn.commit()


def _get_today_day_loss(db_path: str | None = None) -> float:
    """Return today's UTC realized loss as a positive float (0.0 if profitable)."""
    path = str(db_path or DB_PATH)
    day_utc: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT realized FROM firewall_day_pnl WHERE day_utc = ?",
                (day_utc,),
            ).fetchone()
        pnl = float(row[0]) if row and row[0] is not None else 0.0
        return max(0.0, -pnl)
    except Exception:
        return 0.0


def _get_trailing_30d_mean_daily_edge(db_path: str | None = None) -> float:
    """
    Compute mean daily PnL from all winning days in trailing 30 calendar days
    from the trades table. Returns 0.0 if no history. SPEC §5.4d
    """
    path = str(db_path or DB_PATH)
    cutoff_ts: float = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).timestamp()
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                """
                SELECT AVG(daily_pnl) FROM (
                    SELECT DATE(ts, 'unixepoch') AS d,
                           SUM(pnl_usd)         AS daily_pnl
                    FROM   trades
                    WHERE  ts >= ?
                    GROUP  BY d
                    HAVING SUM(pnl_usd) > 0
                )
                """,
                (cutoff_ts,),
            ).fetchone()
        val = float(row[0]) if row and row[0] is not None else 0.0
        return max(0.0, val)
    except Exception as exc:
        logger.warning("[Firewall §5.4d] trailing-edge query failed: %s", exc)
        return 0.0


def check_kill_switch(
    bankroll: float,
    *,
    db_path: str | None = None,
) -> tuple[bool, str]:
    """
    Evaluate the daily kill-switch threshold. SPEC §5.4d

    threshold = min(0.03 * bankroll, 5 * trailing_30d_mean_daily_edge)
    If trailing_30d_mean_daily_edge == 0: threshold = 3% of bankroll only.

    Returns:
        (entries_allowed, veto_reason)
    """
    bankroll_safe: float = max(float(bankroll), 1.0)
    day_loss: float = _get_today_day_loss(db_path=db_path)

    if day_loss <= 0.0:
        return True, ""

    trailing_edge: float = _get_trailing_30d_mean_daily_edge(db_path=db_path)
    bp_threshold: float = max(5.0, 0.03 * bankroll_safe)
    edge_threshold: float = 5.0 * trailing_edge if trailing_edge > 0.0 else float("inf")
    threshold: float = max(5.0, min(bp_threshold, edge_threshold))

    if day_loss > threshold:
        reason = (
            f"firewall_daily_kill_switch "
            f"(day_loss=${day_loss:.2f} > threshold=${threshold:.2f} "
            f"[3%_broll=${bp_threshold:.2f}, "
            f"5x_edge=${5.0 * trailing_edge:.2f}])"
        )
        logger.critical("[Firewall §5.4d] KILL SWITCH TRIGGERED: %s", reason)
        return False, reason

    return True, ""


def is_entries_allowed_today(*, db_path: str | None = None) -> tuple[bool, str]:
    """
    Read the persistent global entries_allowed flag. SPEC §5.4d
    Returns (allowed, reason). No auto-resume intraday.
    """
    path = str(db_path or DB_PATH)
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                """
                SELECT entries_allowed, halted_reason, updated_at
                FROM   firewall_state
                WHERE  ticker = ?
                """,
                (_GLOBAL_TICKER,),
            ).fetchone()
        if row:
            entries_allowed, halted_reason, updated_at = row
            if int(entries_allowed) == 0:
                # Self-healing daily reset: if the block was written on a different UTC day, reset it!
                try:
                    last_block_day = datetime.fromisoformat(updated_at).replace(tzinfo=timezone.utc).strftime("%Y-%m-%d")
                    current_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    if current_day != last_block_day:
                        logger.info("[Firewall §5.4d] Self-healing daily reset: Block written on %s (different UTC day from %s). Resetting.", last_block_day, current_day)
                        reset_daily_flag(db_path=db_path)
                        return True, ""
                except Exception as ex:
                    logger.warning("[Firewall §5.4d] Failed to parse updated_at in self-healing check: %s", ex)
                return False, str(halted_reason or "firewall_daily_kill_switch")
    except Exception as exc:
        logger.warning("[Firewall §5.4d] global flag read failed: %s", exc)
    return True, ""


def set_entries_blocked(reason: str, *, db_path: str | None = None) -> None:
    """Persist global entries_allowed=False until next UTC day. SPEC §5.4d"""
    path = str(db_path or DB_PATH)
    now_utc: str = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO firewall_state
                (ticker, entries_allowed, halted_reason, updated_at)
            VALUES (?, 0, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                entries_allowed = 0,
                halted_reason   = excluded.halted_reason,
                updated_at      = excluded.updated_at
            """,
            (_GLOBAL_TICKER, reason, now_utc),
        )
        conn.commit()
    logger.critical("[Firewall §5.4d] Global entries BLOCKED: %s", reason)


def reset_daily_flag(*, db_path: str | None = None) -> None:
    """
    Reset global entries_allowed=True at UTC day boundary.
    Called from execution_daemon.py at the start of each UTC day.
    SPEC §5.4d — only UTC day boundary may lift the kill switch.
    """
    path = str(db_path or DB_PATH)
    now_utc: str = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO firewall_state
                (ticker, entries_allowed, halted_reason, updated_at)
            VALUES (?, 1, NULL, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                entries_allowed = 1,
                halted_reason   = NULL,
                updated_at      = excluded.updated_at
            """,
            (_GLOBAL_TICKER, now_utc),
        )
        conn.commit()
    logger.info("[Firewall §5.4d] Daily entries_allowed flag reset at UTC day boundary.")


# ---------------------------------------------------------------------------
# Composite gate — single call site for runner.py
# ---------------------------------------------------------------------------


def check_entry_firewall(
    ticker: str,
    bankroll: float,
    *,
    db_path: str | None = None,
) -> tuple[bool, str]:
    """
    Run all four entry gates in sequence. Return on first veto. SPEC §5.4

    Gate evaluation order:
        (d) persisted global block  — fastest early-exit
        (d) live kill-switch eval   — in case threshold just crossed
        halt check                  — explicit ticker halts
        (a) re-entry lockout        — per-ticker settlement lockout
        (b) oscillation breaker     — per-ticker round-trip counter

    Args:
        ticker:   Kalshi contract ticker to evaluate.
        bankroll: Current account cash balance in USD.
        db_path:  Override DB path for tests.

    Returns:
        (allowed, veto_reason) — allowed=True means all four gates pass.
    """
    # Gate (d) — persisted global block first (fastest path)
    allowed, reason = is_entries_allowed_today(db_path=db_path)
    if not allowed:
        return False, reason

    # Gate (d) — live kill-switch evaluation
    allowed, reason = check_kill_switch(bankroll, db_path=db_path)
    if not allowed:
        set_entries_blocked(reason, db_path=db_path)
        return False, reason

    # Explicit halt check — set by previous oscillation / coherence violation
    halted, halt_reason = is_ticker_halted(ticker, db_path=db_path)
    if halted:
        return False, halt_reason

    # Gate (a) — re-entry lockout
    allowed, reason = check_reentry_lockout(ticker, db_path=db_path)
    if not allowed:
        return False, reason

    # Gate (b) — oscillation breaker
    allowed, reason = check_oscillation_breaker(ticker, db_path=db_path)
    if not allowed:
        return False, reason

    return True, ""
