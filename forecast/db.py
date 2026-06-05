"""
forecast/db.py — SQLite schema for the Kalshi forecast lane.

All tables live in the existing logs/trades.db (WAL mode).
Call init_forecast_db() once at startup (idempotent — uses CREATE TABLE IF NOT EXISTS).
"""

import sqlite3
import os
import sys

# Resolve DB path the same way truth_audit_lib does
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from config import DB_PATH as _CFG_DB_PATH
except Exception:
    _CFG_DB_PATH = os.path.join(_ROOT, "logs", "trades.db")

DB_PATH: str = _CFG_DB_PATH


def _conn(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ---------------------------------------------------------------------------
# DDL — 5 tables exactly as specified
# ---------------------------------------------------------------------------

_DDL_FORECAST_MARKETS = """
CREATE TABLE IF NOT EXISTS forecast_markets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    market_symbol    TEXT    NOT NULL UNIQUE,
    market_name      TEXT    NOT NULL,
    exchange         TEXT    NOT NULL DEFAULT 'KALSHI',
    category_path    TEXT,
    underlier_symbol TEXT,
    underlier_conid  INTEGER,
    dataset_ref      TEXT,
    active           INTEGER NOT NULL DEFAULT 1,
    first_seen_at    TEXT    NOT NULL,
    last_seen_at     TEXT    NOT NULL
);
"""

_DDL_FORECAST_CONTRACTS = """
CREATE TABLE IF NOT EXISTS forecast_contracts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       INTEGER NOT NULL REFERENCES forecast_markets(id),
    conid           INTEGER,
    local_symbol    TEXT    NOT NULL,
    contract_name   TEXT,
    right           TEXT    NOT NULL CHECK(right IN ('C', 'P')),
    strike          REAL    NOT NULL,
    currency        TEXT    NOT NULL DEFAULT 'USD',
    exchange        TEXT    NOT NULL DEFAULT 'KALSHI',
    last_trade_at   TEXT,
    resolution_at   TEXT,
    payout_at       TEXT,
    measured_period TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    first_seen_at   TEXT    NOT NULL,
    last_seen_at    TEXT    NOT NULL,
    UNIQUE(market_id, right, strike, last_trade_at)
);
"""

_DDL_FORECAST_QUOTES = """
CREATE TABLE IF NOT EXISTS forecast_quotes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES forecast_contracts(id),
    ts           TEXT    NOT NULL,
    bid          REAL,
    ask          REAL,
    bid_size     REAL,
    ask_size     REAL,
    mid          REAL,
    spread       REAL,
    implied_prob REAL,
    side         TEXT    CHECK(side IN ('YES', 'NO'))
);
CREATE INDEX IF NOT EXISTS idx_forecast_quotes_cid_ts
    ON forecast_quotes (contract_id, ts);
"""

_DDL_FORECAST_BARS = """
CREATE TABLE IF NOT EXISTS forecast_bars (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id         INTEGER NOT NULL REFERENCES forecast_contracts(id),
    interval            TEXT    NOT NULL CHECK(interval IN ('5m','30m','1h','4h','1d')),
    ts_open             TEXT    NOT NULL,
    ts_close            TEXT    NOT NULL,
    o                   REAL,
    h                   REAL,
    l                   REAL,
    c                   REAL,
    mid_mean            REAL,
    spread_mean         REAL,
    vol_proxy           REAL,
    derived_from_quotes INTEGER NOT NULL DEFAULT 1,
    UNIQUE(contract_id, interval, ts_open)
);
CREATE INDEX IF NOT EXISTS idx_forecast_bars_cid_int_ts
    ON forecast_bars (contract_id, interval, ts_open);
"""

_DDL_FORECAST_RESOLUTIONS = """
CREATE TABLE IF NOT EXISTS forecast_resolutions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   INTEGER NOT NULL REFERENCES forecast_contracts(id),
    resolved_side TEXT    CHECK(resolved_side IN ('YES', 'NO')),
    resolved_value REAL,
    resolved_at   TEXT,
    payout_at     TEXT,
    notes         TEXT,
    source        TEXT,
    UNIQUE(contract_id)
);
"""

_DDL_FORECAST_POSITIONS = """
CREATE TABLE IF NOT EXISTS forecast_positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL UNIQUE,
    qty          REAL    NOT NULL,
    entry_price  REAL    NOT NULL,
    side         TEXT    NOT NULL CHECK(side IN ('YES', 'NO')),
    active       INTEGER NOT NULL DEFAULT 1,
    opened_at    TEXT    NOT NULL,
    closed_at    TEXT,
    exit_type    TEXT
);
"""

# v19.4 Sovereign Balance: Tighten retention for 31-city scale
QUOTE_RETENTION_DAYS: int = 7
BAR_RETENTION_DAYS: int = 30


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    ddl_fragment: str,
) -> None:
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {str(row["name"]) for row in cols}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl_fragment}")


def init_forecast_db(db_path: str | None = None) -> None:
    """Create all 6 forecast tables (idempotent). Call once at startup."""
    with _conn(db_path) as c:
        # Execute each DDL block; the INDEX statements are separate from CREATE TABLE
        for ddl_block in [
            _DDL_FORECAST_MARKETS,
            _DDL_FORECAST_CONTRACTS,
            _DDL_FORECAST_QUOTES,
            _DDL_FORECAST_BARS,
            _DDL_FORECAST_RESOLUTIONS,
            _DDL_FORECAST_POSITIONS,
        ]:
            for stmt in ddl_block.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    c.execute(stmt)
        _ensure_column(c, "forecast_contracts", "contract_name", "contract_name TEXT")
        c.commit()


# ---------------------------------------------------------------------------
# Position helpers (v19.1.10 Sovereign Recon)
# ---------------------------------------------------------------------------


def insert_forecast_position(
    ticker: str,
    qty: float,
    entry_price: float,
    side: str,
    db_path: str | None = None,
) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as c:
        c.execute(
            """INSERT OR REPLACE INTO forecast_positions
               (ticker, qty, entry_price, side, active, opened_at)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (ticker, qty, entry_price, side, now),
        )
        c.commit()


def get_open_forecast_positions(db_path: str | None = None) -> list[dict]:
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT * FROM forecast_positions WHERE active=1"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_forecast_position_closed(
    ticker: str, exit_type: str = "resolved", db_path: str | None = None
) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as c:
        c.execute(
            """UPDATE forecast_positions
               SET active=0, closed_at=?, exit_type=?
               WHERE ticker=? AND active=1""",
            (now, exit_type, ticker),
        )
        c.commit()


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------


def upsert_market(
    market_symbol: str,
    market_name: str,
    exchange: str = "KALSHI",
    category_path: str = "",
    underlier_symbol: str = "",
    underlier_conid: int | None = None,
    dataset_ref: str = "",
    db_path: str | None = None,
) -> int:
    """Insert or update a market row. Returns the market id."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT id FROM forecast_markets WHERE market_symbol=?",
            (market_symbol,),
        ).fetchone()
        if row:
            c.execute(
                "UPDATE forecast_markets SET market_name=?, active=1, last_seen_at=? WHERE id=?",
                (market_name, now, row["id"]),
            )
            return row["id"]
        cur = c.execute(
            """INSERT INTO forecast_markets
               (market_symbol, market_name, exchange, category_path, underlier_symbol,
                underlier_conid, dataset_ref, active, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,1,?,?)""",
            (
                market_symbol,
                market_name,
                exchange,
                category_path,
                underlier_symbol,
                underlier_conid,
                dataset_ref,
                now,
                now,
            ),
        )
        c.commit()
        return cur.lastrowid


def upsert_contract(
    market_id: int,
    local_symbol: str,
    right: str,
    strike: float,
    contract_name: str = "",
    currency: str = "USD",
    exchange: str = "KALSHI",
    last_trade_at: str = "",
    resolution_at: str = "",
    payout_at: str = "",
    measured_period: str = "",
    conid: int | None = None,
    db_path: str | None = None,
) -> int:
    """Insert or update a contract row. Returns the contract id."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as c:
        row = c.execute(
            """SELECT id FROM forecast_contracts
               WHERE market_id=? AND right=? AND strike=? AND last_trade_at=?""",
            (market_id, right, strike, last_trade_at),
        ).fetchone()
        if row:
            c.execute(
                """UPDATE forecast_contracts
                   SET active=1,
                       last_seen_at=?,
                       conid=?,
                       contract_name=COALESCE(NULLIF(?, ''), contract_name)
                   WHERE id=?""",
                (now, conid, contract_name, row["id"]),
            )
            return row["id"]
        cur = c.execute(
            """INSERT INTO forecast_contracts
               (market_id, conid, local_symbol, contract_name, right, strike, currency, exchange,
                last_trade_at, resolution_at, payout_at, measured_period, active,
                first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (
                market_id,
                conid,
                local_symbol,
                contract_name,
                right,
                strike,
                currency,
                exchange,
                last_trade_at,
                resolution_at,
                payout_at,
                measured_period,
                now,
                now,
            ),
        )
        c.commit()
        return cur.lastrowid


def insert_quote(
    contract_id: int,
    ts: str,
    bid: float | None,
    ask: float | None,
    bid_size: float | None,
    ask_size: float | None,
    mid: float | None,
    spread: float | None,
    implied_prob: float | None,
    side: str,
    db_path: str | None = None,
) -> None:
    with _conn(db_path) as c:
        c.execute(
            """INSERT INTO forecast_quotes
               (contract_id, ts, bid, ask, bid_size, ask_size, mid, spread, implied_prob, side)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                contract_id,
                ts,
                bid,
                ask,
                bid_size,
                ask_size,
                mid,
                spread,
                implied_prob,
                side,
            ),
        )
        c.commit()


def upsert_bar(
    contract_id: int,
    interval: str,
    ts_open: str,
    ts_close: str,
    o: float,
    h: float,
    l: float,
    c_: float,
    mid_mean: float,
    spread_mean: float,
    vol_proxy: float,
    db_path: str | None = None,
) -> None:
    with _conn(db_path) as c:
        c.execute(
            """INSERT OR REPLACE INTO forecast_bars
               (contract_id, interval, ts_open, ts_close, o, h, l, c,
                mid_mean, spread_mean, vol_proxy, derived_from_quotes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
            (
                contract_id,
                interval,
                ts_open,
                ts_close,
                o,
                h,
                l,
                c_,
                mid_mean,
                spread_mean,
                vol_proxy,
            ),
        )
        c.commit()


def insert_resolution(
    contract_id: int,
    resolved_side: str,
    resolved_value: float,
    resolved_at: str,
    payout_at: str = "",
    notes: str = "",
    source: str = "kalshi",
    db_path: str | None = None,
) -> None:
    with _conn(db_path) as c:
        c.execute(
            """INSERT OR IGNORE INTO forecast_resolutions
               (contract_id, resolved_side, resolved_value, resolved_at,
                payout_at, notes, source)
               VALUES (?,?,?,?,?,?,?)""",
            (
                contract_id,
                resolved_side,
                resolved_value,
                resolved_at,
                payout_at,
                notes,
                source,
            ),
        )
        c.commit()


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def get_active_contracts(db_path: str | None = None) -> list[dict]:
    """Return all active contracts with their market info joined."""
    with _conn(db_path) as c:
        rows = c.execute(
            """SELECT fc.id, fc.market_id, fc.conid, fc.local_symbol, fc.contract_name,
                      fc.right, fc.strike, fc.last_trade_at, fc.resolution_at,
                      fm.market_symbol, fm.market_name, fm.category_path
               FROM forecast_contracts fc
               JOIN forecast_markets fm ON fm.id = fc.market_id
               WHERE fc.active=1 AND fm.active=1
               ORDER BY fc.resolution_at ASC""",
        ).fetchall()
        return [dict(r) for r in rows]


def get_contract_metadata(local_symbol: str, db_path: str | None = None) -> dict | None:
    """Return the most recent stored contract row for a ticker."""
    with _conn(db_path) as c:
        row = c.execute(
            """SELECT fc.id, fc.market_id, fc.conid, fc.local_symbol, fc.contract_name,
                      fc.right, fc.strike, fc.last_trade_at, fc.resolution_at,
                      fm.market_symbol, fm.market_name, fm.category_path
               FROM forecast_contracts fc
               JOIN forecast_markets fm ON fm.id = fc.market_id
               WHERE fc.local_symbol = ?
               ORDER BY fc.active DESC, fc.last_seen_at DESC, fc.id DESC
               LIMIT 1""",
            (local_symbol,),
        ).fetchone()
        return dict(row) if row else None


def get_recent_quotes(
    contract_id: int,
    limit: int = 300,
    db_path: str | None = None,
) -> list[dict]:
    with _conn(db_path) as c:
        rows = c.execute(
            """SELECT * FROM forecast_quotes
               WHERE contract_id=?
               ORDER BY ts DESC LIMIT ?""",
            (contract_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_recent_quotes_for_bar(
    contract_id: int,
    lookback_seconds: int,
    db_path: str | None = None,
) -> list[dict]:
    """Return quotes within a rolling lookback anchored to the latest quote."""
    from datetime import datetime, timedelta, timezone

    rows = get_recent_quotes(contract_id, limit=5000, db_path=db_path)
    if not rows:
        return []

    latest_ts = rows[-1]["ts"]
    try:
        latest_dt = datetime.fromisoformat(str(latest_ts).replace("Z", "+00:00"))
        if latest_dt.tzinfo is None:
            latest_dt = latest_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return rows

    cutoff = latest_dt - timedelta(seconds=lookback_seconds)
    filtered = []
    for row in rows:
        try:
            row_dt = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
            if row_dt.tzinfo is None:
                row_dt = row_dt.replace(tzinfo=timezone.utc)
            if row_dt >= cutoff:
                filtered.append(row)
        except Exception:
            continue
    return filtered


def get_last_bar_ts(contract_id: int, interval: str, db_path: str | None = None) -> str | None:
    """Return the ts_open of the most recent bar for a contract/interval."""
    with _conn(db_path) as c:
        row = c.execute(
            "SELECT ts_open FROM forecast_bars WHERE contract_id=? AND interval=? ORDER BY ts_open DESC LIMIT 1",
            (contract_id, interval),
        ).fetchone()
        return row[0] if row else None


def get_bars(
    contract_id: int,
    interval: str,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    with _conn(db_path) as c:
        rows = c.execute(
            """SELECT * FROM forecast_bars
               WHERE contract_id=? AND interval=?
               ORDER BY ts_open DESC LIMIT ?""",
            (contract_id, interval, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


def prune_old_quotes(db_path: str | None = None) -> int:
    """Delete quotes older than QUOTE_RETENTION_DAYS. Returns rows deleted."""
    from datetime import datetime, timedelta, timezone

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=QUOTE_RETENTION_DAYS)
    ).isoformat()
    with _conn(db_path) as c:
        cur = c.execute("DELETE FROM forecast_quotes WHERE ts < ?", (cutoff,))
        c.commit()
        return cur.rowcount


def prune_old_bars(db_path: str | None = None) -> int:
    """Delete bars older than BAR_RETENTION_DAYS. Returns rows deleted."""
    from datetime import datetime, timedelta, timezone

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=BAR_RETENTION_DAYS)
    ).isoformat()
    with _conn(db_path) as c:
        cur = c.execute("DELETE FROM forecast_bars WHERE ts_open < ?", (cutoff,))
        c.commit()
        return cur.rowcount
