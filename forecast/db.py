"""
forecast/db.py — SQLite schema for the Kalshi forecast lane.

All tables live in the existing logs/trades.db (WAL mode).
Call init_forecast_db() once at startup (idempotent — uses CREATE TABLE IF NOT EXISTS).
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

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


def _parse_contract_dt(raw_value: str | None) -> datetime | None:
    """Parse Kalshi/IB-style contract timestamps into aware UTC datetimes."""
    value = str(raw_value or "").strip()
    if not value:
        return None

    try:
        if "T" in value and ("Z" in value or "+" in value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            fmt = "%Y%m%d %H:%M:%S" if " " in value else "%Y%m%d"
            parsed = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


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
    q_gfs         REAL,
    q_ecmwf       REAL,
    q_hrrr        REAL,
    q_hat         REAL,
    sigma_post    REAL,
    lambda_scaler REAL,
    fee_rate_applied REAL,
    basis_quality TEXT DEFAULT 'CONFIRMED',
    UNIQUE(contract_id)
);
"""

_DDL_FORECAST_POSITIONS = """
CREATE TABLE IF NOT EXISTS forecast_positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL UNIQUE,
    qty          INTEGER NOT NULL,
    entry_price  REAL    NOT NULL,
    side         TEXT    NOT NULL CHECK(side IN ('YES', 'NO')),
    category     TEXT    NOT NULL DEFAULT 'TEMP',
    active       INTEGER NOT NULL DEFAULT 1,
    opened_at    TEXT    NOT NULL,
    closed_at    TEXT,
    exit_type    TEXT,
    basis_quality TEXT DEFAULT 'CONFIRMED'
);
"""



_DDL_RECENT_VETOES = """
CREATE TABLE IF NOT EXISTS recent_vetoes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TEXT    NOT NULL,
    ticker             TEXT    NOT NULL,
    strategy_family    TEXT,
    side               TEXT,
    veto_reason        TEXT    NOT NULL,
    rank_score         REAL,
    ev                 REAL,
    position_contracts INTEGER,
    size_usd           REAL,
    details_json       TEXT    DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_recent_vetoes_ts
    ON recent_vetoes (ts DESC);
CREATE INDEX IF NOT EXISTS idx_recent_vetoes_reason
    ON recent_vetoes (veto_reason);
"""

_DDL_SYSTEM_COOLDOWNS = """
CREATE TABLE IF NOT EXISTS system_cooldowns (
    process_name     TEXT PRIMARY KEY,
    last_executed_ts INTEGER NOT NULL
);
"""

_DDL_NOAA_DAILY_SUMMARIES = """
CREATE TABLE IF NOT EXISTS noaa_daily_summaries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    station        TEXT NOT NULL,
    date           TEXT NOT NULL,
    temp_max       REAL,
    temp_min       REAL,
    precipitation  REAL,
    source         TEXT NOT NULL DEFAULT 'legacy_unknown',
    UNIQUE(station, date)
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
    """Create all forecast tables + v20 firewall tables (idempotent). Call once at startup."""
    with _conn(db_path) as c:
        # Execute each DDL block; the INDEX statements are separate from CREATE TABLE
        for ddl_block in [
            _DDL_FORECAST_MARKETS,
            _DDL_FORECAST_CONTRACTS,
            _DDL_FORECAST_QUOTES,
            _DDL_FORECAST_BARS,
            _DDL_FORECAST_RESOLUTIONS,
            _DDL_FORECAST_POSITIONS,
            _DDL_RECENT_VETOES,
            _DDL_SYSTEM_COOLDOWNS,
            _DDL_NOAA_DAILY_SUMMARIES,
        ]:
            for stmt in ddl_block.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    c.execute(stmt)
        _ensure_column(c, "forecast_contracts", "contract_name", "contract_name TEXT")
        _ensure_column(c, "forecast_positions", "basis_quality", "basis_quality TEXT DEFAULT 'CONFIRMED'")
        # The forecast that justified the entry, kept next to the entry itself.
        # forecast_resolutions has carried q_hat columns for a long time and they
        # are NULL on all 76k rows, because resolution_sync runs off weather
        # observations and never sees the model output. Recording it here is what
        # makes "were our entries actually calibrated?" answerable at all.
        _ensure_column(c, "forecast_positions", "q_hat", "q_hat REAL")
        _ensure_column(c, "forecast_positions", "ev_at_entry", "ev_at_entry REAL")
        _ensure_column(c, "forecast_resolutions", "q_gfs", "q_gfs REAL")
        _ensure_column(c, "forecast_resolutions", "q_ecmwf", "q_ecmwf REAL")
        _ensure_column(c, "forecast_resolutions", "q_hrrr", "q_hrrr REAL")
        _ensure_column(c, "forecast_resolutions", "q_hat", "q_hat REAL")
        _ensure_column(c, "forecast_resolutions", "sigma_post", "sigma_post REAL")
        _ensure_column(c, "forecast_resolutions", "lambda_scaler", "lambda_scaler REAL")
        _ensure_column(c, "forecast_resolutions", "fee_rate_applied", "fee_rate_applied REAL")
        _ensure_column(c, "forecast_resolutions", "basis_quality", "basis_quality TEXT DEFAULT 'CONFIRMED'")
        _ensure_column(
            c,
            "noaa_daily_summaries",
            "source",
            "source TEXT NOT NULL DEFAULT 'legacy_unknown'",
        )
        c.commit()
    from intelligence.schema import init_intelligence_db
    init_intelligence_db(db_path or DB_PATH)

    # v20 SPEC §5.4 — create stateful firewall tables in same DB
    try:
        from forecast.firewall import ensure_firewall_tables
        ensure_firewall_tables(db_path=db_path)
    except Exception as _fw_err:  # pragma: no cover
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[init_forecast_db] Firewall table init failed: %s", _fw_err
        )


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
    from forecast.weather_contracts import weather_mode_for_ticker

    category = weather_mode_for_ticker(ticker) or 'TEMP'
    now = datetime.now(timezone.utc).isoformat()
    normalized_qty = max(0, int(round(float(qty))))
    with _conn(db_path) as c:
        c.execute(
            """INSERT OR REPLACE INTO forecast_positions
               (ticker, qty, entry_price, side, category, active, opened_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (ticker, normalized_qty, entry_price, side, category, now),
        )
        c.commit()


def get_open_forecast_positions(db_path: str | None = None) -> list[dict]:
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT * FROM forecast_positions WHERE active=1"
        ).fetchall()
        return [dict(r) for r in rows]


def sync_open_forecast_position(
    ticker: str,
    qty: float,
    entry_price: float,
    side: str,
    basis_quality: str = "CONFIRMED",
    q_hat: float | None = None,
    ev_at_entry: float | None = None,
    db_path: str | None = None,
) -> None:
    from datetime import datetime, timezone
    from forecast.weather_contracts import weather_mode_for_ticker

    category = weather_mode_for_ticker(ticker) or 'TEMP'
    now = datetime.now(timezone.utc).isoformat()
    normalized_qty = max(0.0, float(qty))
    q_hat_val = float(q_hat) if q_hat is not None else None
    ev_val = float(ev_at_entry) if ev_at_entry is not None else None
    with _conn(db_path) as c:
        c.execute(
            """
            INSERT INTO forecast_positions
                (ticker, qty, entry_price, side, category, active, opened_at, closed_at, exit_type,
                 basis_quality, q_hat, ev_at_entry)
            VALUES (?, ?, ?, ?, ?, 1, ?, NULL, NULL, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                qty=excluded.qty,
                entry_price=excluded.entry_price,
                side=excluded.side,
                category=excluded.category,
                active=1,
                opened_at=CASE
                    WHEN forecast_positions.active=1 THEN forecast_positions.opened_at
                    ELSE excluded.opened_at
                END,
                closed_at=NULL,
                exit_type=NULL,
                basis_quality=excluded.basis_quality,
                -- Reconciliation adopts broker positions with no forecast attached;
                -- never let that blank out a q_hat the strategy already recorded.
                q_hat=COALESCE(excluded.q_hat, forecast_positions.q_hat),
                ev_at_entry=COALESCE(excluded.ev_at_entry, forecast_positions.ev_at_entry)
            """,
            (ticker, normalized_qty, entry_price, side, category, now, basis_quality,
             q_hat_val, ev_val),
        )
        c.commit()


def _fetch_confirmed_entry_price_from_fills(broker, ticker: str, side: str) -> float | None:
    """Recover a YES-denominated buy basis from current Kalshi V2 fill fields.

    ``forecast_positions.entry_price`` intentionally uses the same canonical
    YES-leg denomination as the broker's realized-P&L math.  V2 identifies the
    purchased outcome via ``outcome_side`` and reports decimal prices in
    ``yes_price_dollars`` / ``no_price_dollars`` with quantity in ``count_fp``.
    The legacy ``side`` / cent ``price`` / ``count`` shape remains a final
    compatibility fallback for old fixtures and archived responses.
    """
    import time
    if broker is None or not hasattr(broker, "_request"):
        return None

    backoffs = [0.5, 1.0, 2.0]
    for delay in backoffs:
        try:
            resp = broker._request("GET", "/trade-api/v2/portfolio/fills", params={"ticker": ticker, "limit": 100})
            fills = resp.get("fills") or []
            if fills:
                target_side = side.lower()
                matching_fills = [
                    f for f in fills
                    if str(f.get("outcome_side") or f.get("side") or "").lower()
                    == target_side
                    and str(f.get("action") or "buy").lower() == "buy"
                ]
                if matching_fills:
                    total_cost = 0.0
                    total_qty = 0.0
                    for f in matching_fills:
                        try:
                            yes_raw = f.get("yes_price_dollars")
                            no_raw = f.get("no_price_dollars")
                            if yes_raw not in (None, ""):
                                yes_price = float(yes_raw)
                            elif no_raw not in (None, ""):
                                yes_price = 1.0 - float(no_raw)
                            else:
                                legacy_price = float(f.get("price") or 0.0) / 100.0
                                yes_price = (
                                    1.0 - legacy_price
                                    if target_side == "no"
                                    else legacy_price
                                )
                            count = float(f.get("count_fp") or f.get("count") or 0.0)
                            total_cost += yes_price * count
                            total_qty += count
                        except (TypeError, ValueError):
                            continue
                    if total_qty > 0:
                        return total_cost / total_qty
            time.sleep(delay)
        except Exception:
            time.sleep(delay)
    return None


def reconcile_forecast_positions(
    broker_positions: list[dict],
    *,
    broker = None,
    db_path: str | None = None,
    close_missing_exit_type: str = "manual_exit",
) -> dict:
    """Mirror broker reality into the local forecast_positions cache."""
    open_db_positions = get_open_forecast_positions(db_path=db_path)
    broker_by_ticker = {
        str(pos.get("local_symbol") or ""): pos
        for pos in broker_positions
        if str(pos.get("local_symbol") or "") and float(pos.get("qty") or 0.0) > 0
    }
    db_tickers = {str(pos.get("ticker") or "") for pos in open_db_positions if pos.get("ticker")}

    closed = 0
    adopted = 0
    refreshed = 0

    for db_pos in open_db_positions:
        ticker = str(db_pos.get("ticker") or "")
        if ticker and ticker not in broker_by_ticker:
            mark_forecast_position_closed(
                ticker,
                exit_type=close_missing_exit_type,
                db_path=db_path,
            )
            closed += 1

    for ticker, broker_pos in broker_by_ticker.items():
        raw_price = float(
            broker_pos.get("entry_price")
            or broker_pos.get("entry")
            or 0.0
        )
        side = str(broker_pos.get("side") or "YES")

        basis_quality = "CONFIRMED"
        entry_price = raw_price

        if entry_price <= 0.0:
            resolved_price = _fetch_confirmed_entry_price_from_fills(broker, ticker, side)
            if resolved_price is not None and resolved_price > 0.0:
                entry_price = resolved_price
                basis_quality = "CONFIRMED"
            else:
                fallback_mid = float(broker_pos.get("mid") or 0.0)
                if fallback_mid > 0.0:
                    entry_price = fallback_mid
                    basis_quality = "ESTIMATED"
                else:
                    entry_price = 0.50
                    basis_quality = "ESTIMATED"

        sync_open_forecast_position(
            ticker=ticker,
            qty=float(broker_pos.get("qty") or 0.0),
            entry_price=entry_price,
            side=side,
            basis_quality=basis_quality,
            db_path=db_path,
        )
        if ticker in db_tickers:
            refreshed += 1
        else:
            adopted += 1

    return {
        "broker_positions": len(broker_by_ticker),
        "db_positions_before": len(open_db_positions),
        "adopted": adopted,
        "refreshed": refreshed,
        "closed": closed,
    }


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


def record_recent_veto(
    *,
    ticker: str,
    veto_reason: str,
    strategy_family: str = "",
    side: str = "",
    rank_score: float | None = None,
    ev: float | None = None,
    position_contracts: int | None = None,
    size_usd: float | None = None,
    details: dict | None = None,
    db_path: str | None = None,
    max_rows: int = 1000,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(details or {}, separators=(",", ":"))
    with _conn(db_path) as c:
        c.execute(
            """
            INSERT INTO recent_vetoes
                (ts, ticker, strategy_family, side, veto_reason, rank_score, ev,
                 position_contracts, size_usd, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                ticker,
                strategy_family,
                side,
                veto_reason,
                rank_score,
                ev,
                position_contracts,
                size_usd,
                payload,
            ),
        )
        c.execute(
            """
            DELETE FROM recent_vetoes
             WHERE id NOT IN (
                 SELECT id
                 FROM recent_vetoes
                 ORDER BY ts DESC, id DESC
                 LIMIT ?
             )
            """,
            (max(1, int(max_rows)),),
        )
        c.commit()


def get_system_cooldown_ts(
    process_name: str,
    *,
    db_path: str | None = None,
) -> int | None:
    with _conn(db_path) as c:
        row = c.execute(
            """
            SELECT last_executed_ts
            FROM system_cooldowns
            WHERE process_name=?
            """,
            (process_name,),
        ).fetchone()
    if not row:
        return None
    try:
        return int(row["last_executed_ts"])
    except Exception:
        return None


def set_system_cooldown_ts(
    process_name: str,
    last_executed_ts: int,
    *,
    db_path: str | None = None,
) -> None:
    with _conn(db_path) as c:
        c.execute(
            """
            INSERT INTO system_cooldowns (process_name, last_executed_ts)
            VALUES (?, ?)
            ON CONFLICT(process_name) DO UPDATE SET
                last_executed_ts=excluded.last_executed_ts
            """,
            (process_name, int(last_executed_ts)),
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


def deactivate_markets_not_in_symbols(
    market_symbols: list[str],
    db_path: str | None = None,
) -> int:
    """
    Mark markets inactive when they disappear from the latest discovery pass.
    Historical rows are preserved; only the active flag changes.
    """
    cleaned = sorted({str(symbol).strip() for symbol in market_symbols if str(symbol).strip()})
    with _conn(db_path) as c:
        if not cleaned:
            return 0
        placeholders = ",".join("?" for _ in cleaned)
        cur = c.execute(
            f"""
            UPDATE forecast_markets
               SET active=0
             WHERE active=1
               AND market_symbol NOT IN ({placeholders})
            """,
            cleaned,
        )
        c.commit()
        return cur.rowcount


def deactivate_contracts_not_in_symbols(
    local_symbols: list[str],
    db_path: str | None = None,
    *,
    deactivate_all_if_empty: bool = False,
) -> int:
    """
    Mark contracts inactive when they disappear from discovery.

    When ``deactivate_all_if_empty`` is True, an empty symbol list is treated as
    "no currently tradable contracts were discovered" rather than "skip cleanup".
    """
    cleaned = sorted({str(symbol).strip() for symbol in local_symbols if str(symbol).strip()})
    with _conn(db_path) as c:
        if not cleaned and not deactivate_all_if_empty:
            return 0
        if not cleaned:
            cur = c.execute(
                """
                UPDATE forecast_contracts
                   SET active=0
                 WHERE active=1
                """
            )
            c.commit()
            return cur.rowcount

        placeholders = ",".join("?" for _ in cleaned)
        cur = c.execute(
            f"""
            UPDATE forecast_contracts
               SET active=0
             WHERE active=1
               AND local_symbol NOT IN ({placeholders})
            """,
            cleaned,
        )
        c.commit()
        return cur.rowcount


def deactivate_expired_contracts(
    db_path: str | None = None,
    *,
    as_of: datetime | None = None,
) -> int:
    """
    Retire contracts that are already resolved or past their close/resolution time.
    """
    now_utc = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expired_ids: list[int] = []

    with _conn(db_path) as c:
        rows = c.execute(
            """
            SELECT id, resolution_at, last_trade_at
            FROM forecast_contracts
            WHERE active=1
            """
        ).fetchall()

        for row in rows:
            expiry_dt = _parse_contract_dt(row["resolution_at"]) or _parse_contract_dt(
                row["last_trade_at"]
            )
            if expiry_dt and expiry_dt <= now_utc:
                expired_ids.append(int(row["id"]))

        updated = 0
        if expired_ids:
            c.executemany(
                "UPDATE forecast_contracts SET active=0 WHERE id=?",
                [(contract_id,) for contract_id in expired_ids],
            )
            updated += len(expired_ids)

        resolved_cur = c.execute(
            """
            UPDATE forecast_contracts
               SET active=0
             WHERE active=1
               AND id IN (SELECT contract_id FROM forecast_resolutions)
            """
        )
        updated += max(0, int(resolved_cur.rowcount or 0))
        c.commit()
        return updated


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
    low: float,
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
                low,
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
    q_gfs: float | None = None,
    q_ecmwf: float | None = None,
    q_hrrr: float | None = None,
    q_hat: float | None = None,
    sigma_post: float | None = None,
    lambda_scaler: float | None = None,
    fee_rate_applied: float | None = None,
    basis_quality: str = "CONFIRMED",
    db_path: str | None = None,
) -> None:
    with _conn(db_path) as c:
        c.execute(
            """INSERT OR IGNORE INTO forecast_resolutions
               (contract_id, resolved_side, resolved_value, resolved_at,
                payout_at, notes, source, q_gfs, q_ecmwf, q_hrrr, q_hat,
                sigma_post, lambda_scaler, fee_rate_applied, basis_quality)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                contract_id,
                resolved_side,
                resolved_value,
                resolved_at,
                payout_at,
                notes,
                source,
                q_gfs,
                q_ecmwf,
                q_hrrr,
                q_hat,
                sigma_post,
                lambda_scaler,
                fee_rate_applied,
                basis_quality,
            ),
        )
        c.commit()


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def get_live_brier_score(min_n: int = 20, db_path: str | None = None) -> dict:
    """Real Brier score of entry q_hat against realized settlement outcomes.

    Joins forecast_resolutions.q_hat (the strategy's fair-probability estimate
    at entry, forwarded by resolution_sync.py) against resolved_side. Returns
    n=0/score=None below min_n -- there is no synthetic fallback here, unlike
    historical placeholder writers that bypassed official settlement truth.
    """
    with _conn(db_path) as c:
        rows = c.execute(
            """SELECT q_hat, resolved_side FROM forecast_resolutions
               WHERE q_hat IS NOT NULL AND resolved_side IN ('YES', 'NO')"""
        ).fetchall()
    n = len(rows)
    if n < min_n:
        return {"score": None, "n": n, "min_n": min_n}
    sq_err = sum((r["q_hat"] - (1.0 if r["resolved_side"] == "YES" else 0.0)) ** 2 for r in rows)
    return {"score": sq_err / n, "n": n, "min_n": min_n}


def get_active_contracts(db_path: str | None = None) -> list[dict]:
    """Return all active contracts with their market info joined."""
    with _conn(db_path) as c:
        rows = c.execute(
            """SELECT fc.id, fc.market_id, fc.conid, fc.local_symbol, fc.contract_name,
                      fc.right, fc.strike, fc.last_trade_at, fc.resolution_at,
                      fm.market_symbol, fm.market_name, fm.category_path
               FROM forecast_contracts fc
               JOIN forecast_markets fm ON fm.id = fc.market_id
               LEFT JOIN forecast_resolutions fr ON fr.contract_id = fc.id
               WHERE fc.active=1 AND fm.active=1 AND fr.contract_id IS NULL
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
