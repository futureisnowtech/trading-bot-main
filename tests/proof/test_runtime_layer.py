"""
tests/proof/test_runtime_layer.py — Runtime truth layer invariants.

Invariants proven:
  1.  test_runtime_tables_init            — init_runtime_tables() creates both tables without error
  2.  test_upsert_system_state            — upsert_system_state sets process_mode, readable via get_system_state()
  3.  test_upsert_lane_state              — upsert_lane_state for 'crypto' is readable via get_lane_state()
  4.  test_all_three_lanes_register       — upsert crypto/forecast/mes_archived → get_all_lane_states() returns 3 rows
  5.  test_system_heartbeat_updates_timestamp — write_system_heartbeat() sets process_alive=1 and updates timestamp
  6.  test_incident_table_init            — init_incident_table() creates incidents table without error
  7.  test_incident_ingest_deduplicates   — 10 identical source+message rows → 1 incident after ingest
  8.  test_incident_archived_lane_filtered — IBKRBroker errors ingested with FUTURES_LANE_ACTIVE=False → 0 open
  9.  test_position_reconciler_repairs_flags — open_pos scale_33_done=0 + partial-close trade → repaired to 1
  10. test_lane_economics_crypto          — get_lane_economics('crypto') taker_fee_pct=0.0003, round_trip=0.0006
  11. test_lane_economics_forecast_zero_commission — get_lane_economics('forecast') round_trip=0.0
  12. test_allocator_scaffold             — GlobalAllocator register + get_available_capital doesn't raise
  13. test_lane_registry_mes_disabled_by_default — mes_archived not in get_active_lane_ids() when FUTURES_LANE_ACTIVE=False
  14. test_is_trade_viable_crypto         — is_trade_viable('crypto', 0.01)=True; is_trade_viable('crypto', 0.0001)=False
  15. test_active_lanes_populated         — upsert_system_state(active_lanes='["crypto"]') → get_system_state() contains "crypto"
  16. test_mark_lane_heartbeat            — mark_lane_heartbeat("crypto") sets last_heartbeat_at to a recent ISO timestamp
  17. test_forecast_lane_state_update     — upsert_lane_state forecast active=1,connected=1,readiness=NO_UNDERLIERS is readable
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = ROOT / "dashboard"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_ROOT))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _insert_event_raw(db_path: Path, source: str, message: str,
                      level: str = "ERROR", minutes_ago: int = 0) -> None:
    """Insert a system_events row directly (UTC timestamp)."""
    ts = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?,?,?,?)",
            (ts, level, source, message),
        )


def _init_open_positions_table(db_path: Path) -> None:
    """Ensure open_positions table has scale_33_done / scale_66_done columns."""
    with sqlite3.connect(db_path) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS open_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy TEXT,
                qty REAL,
                entry REAL,
                stop REAL,
                target REAL,
                high_since_entry REAL,
                ts_entry TEXT,
                paper INTEGER DEFAULT 1,
                trailing_active INTEGER DEFAULT 0,
                scale_33_done INTEGER DEFAULT 0,
                scale_66_done INTEGER DEFAULT 0
            );
        """)


# ── Test 1: init_runtime_tables creates both tables ───────────────────────────

def test_runtime_tables_init(proof_runtime, monkeypatch):
    """init_runtime_tables() creates system_runtime_state and lane_runtime_state without error."""
    import runtime.runtime_state as rs

    monkeypatch.setattr(rs, "DB_PATH", str(proof_runtime.db_path), raising=False)

    rs.init_runtime_tables(db_path=str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as c:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "system_runtime_state" in tables
    assert "lane_runtime_state" in tables


# ── Test 2: upsert_system_state is readable via get_system_state ──────────────

def test_upsert_system_state(proof_runtime, monkeypatch):
    """upsert_system_state sets process_mode, get_system_state returns it."""
    import runtime.runtime_state as rs

    monkeypatch.setattr(rs, "DB_PATH", str(proof_runtime.db_path), raising=False)
    db = str(proof_runtime.db_path)

    rs.init_runtime_tables(db_path=db)
    rs.upsert_system_state(db_path=db, process_mode="paper")

    state = rs.get_system_state(db_path=db)
    assert state["process_mode"] == "paper"
    assert state["id"] == 1


# ── Test 3: upsert_lane_state for 'crypto' is readable ───────────────────────

def test_upsert_lane_state(proof_runtime, monkeypatch):
    """upsert_lane_state for 'crypto' is readable via get_lane_state()."""
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)

    rs.init_runtime_tables(db_path=db)
    rs.upsert_lane_state("crypto", db_path=db, enabled=1, health="OK", mode="paper")

    lane = rs.get_lane_state("crypto", db_path=db)
    assert lane["lane_id"] == "crypto"
    assert lane["enabled"] == 1
    assert lane["health"] == "OK"
    assert lane["mode"] == "paper"


# ── Test 4: all three lanes upserted → get_all_lane_states returns 3 rows ─────

def test_all_three_lanes_register(proof_runtime, monkeypatch):
    """After upserting crypto/forecast/mes_archived, get_all_lane_states() returns 3 rows."""
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)

    rs.init_runtime_tables(db_path=db)
    for lane_id in ("crypto", "forecast", "mes_archived"):
        rs.upsert_lane_state(lane_id, db_path=db, enabled=1)

    all_lanes = rs.get_all_lane_states(db_path=db)
    lane_ids = [r["lane_id"] for r in all_lanes]
    assert len(all_lanes) == 3
    assert "crypto" in lane_ids
    assert "forecast" in lane_ids
    assert "mes_archived" in lane_ids


# ── Test 5: write_system_heartbeat sets process_alive=1 ──────────────────────

def test_system_heartbeat_updates_timestamp(proof_runtime, monkeypatch):
    """write_system_heartbeat() sets process_alive=1 and updates last_global_heartbeat_at."""
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)

    rs.init_runtime_tables(db_path=db)
    rs.write_system_heartbeat(db_path=db)

    state = rs.get_system_state(db_path=db)
    assert state["process_alive"] == 1
    assert state["last_global_heartbeat_at"] is not None
    assert state["last_global_heartbeat_at"] != ""


# ── Test 6: init_incident_table creates incidents table ──────────────────────

def test_incident_table_init(proof_runtime, monkeypatch):
    """init_incident_table() creates incidents table without error."""
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)

    it.init_incident_table(db_path=db)

    with sqlite3.connect(proof_runtime.db_path) as c:
        tables = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "incidents" in tables


# ── Test 7: ingest_system_events deduplicates 10 identical rows to 1 incident ─

def test_incident_ingest_deduplicates(proof_runtime, monkeypatch):
    """Insert 10 identical source+message rows; ingest_system_events() produces 1 incident not 10."""
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)
    monkeypatch.setattr(it, "FUTURES_LANE_ACTIVE", True, raising=False)

    it.init_incident_table(db_path=db)

    # Insert 10 identical error events for the crypto lane
    for _ in range(10):
        _insert_event_raw(
            proof_runtime.db_path,
            source="v10_runner",
            message="Connection timeout on scanner endpoint",
            level="ERROR",
        )

    count = it.ingest_system_events(lookback_minutes=120, db_path=db)
    assert count == 1, f"Expected 1 incident group, got {count}"

    # Double-check incident row count
    with sqlite3.connect(proof_runtime.db_path) as c:
        n = c.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    assert n == 1


# ── Test 8: archived lane events filtered when FUTURES_LANE_ACTIVE=False ──────

def test_incident_archived_lane_filtered(proof_runtime, monkeypatch):
    """IBKRBroker error rows with FUTURES_LANE_ACTIVE=False → 0 open incidents."""
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)
    monkeypatch.setattr(it, "FUTURES_LANE_ACTIVE", False, raising=False)

    it.init_incident_table(db_path=db)

    # Insert IBKR broker errors (map to mes_archived lane)
    for _ in range(5):
        _insert_event_raw(
            proof_runtime.db_path,
            source="IBKRBroker",
            message="Connection refused to TWS port 7497",
            level="ERROR",
        )

    it.ingest_system_events(lookback_minutes=120, db_path=db)

    # With FUTURES_LANE_ACTIVE=False, archived lane events are skipped at ingest
    # So no incidents should be created for mes_archived
    open_incidents = it.get_open_incidents(exclude_archived=True, db_path=db)
    assert len(open_incidents) == 0, (
        f"Expected 0 open incidents (archived filtered), got {len(open_incidents)}: {open_incidents}"
    )


# ── Test 9: position_reconciler repairs scale_33_done from trade ledger ───────

def test_position_reconciler_repairs_flags(proof_runtime, monkeypatch):
    """open_position with scale_33_done=0 + partial-close trade → reconcile sets scale_33_done=1."""
    import config
    import runtime.position_reconciler as pr

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(pr, "DB_PATH", db, raising=False)
    monkeypatch.setattr(pr, "PAPER_TRADING", True, raising=False)
    monkeypatch.setattr(config, "PAPER_TRADING", True, raising=False)

    _init_open_positions_table(proof_runtime.db_path)

    entry_ts = "2026-04-15T10:00:00+00:00"

    # Insert an open position with scale_33_done=0
    with sqlite3.connect(proof_runtime.db_path) as c:
        c.execute(
            """
            INSERT INTO open_positions
                (symbol, strategy, qty, entry, stop, target, high_since_entry,
                 ts_entry, paper, trailing_active, scale_33_done, scale_66_done)
            VALUES ('BTCUSDT', 'crypto_perp', 1.0, 50000.0, 48000.0, 55000.0,
                    50500.0, ?, 1, 0, 0, 0)
            """,
            (entry_ts,),
        )

    # Insert a partial-close trade for the same symbol after entry_ts
    with sqlite3.connect(proof_runtime.db_path) as c:
        c.execute(
            """
            INSERT INTO trades
                (ts, strategy, broker, symbol, action, order_type, qty, price,
                 value_usd, fee_usd, pnl_usd, paper, order_id, notes, won, source, pnl_pct)
            VALUES
                ('2026-04-15T10:30:00+00:00', 'crypto_perp', 'coinbase_paper',
                 'BTCUSDT', 'SELL', 'MARKET', 0.33, 51000.0, 16830.0, 5.05,
                 330.0, 1, 'proof_partial_close', 'scale_out partial 33%', 1,
                 'clean_paper_v10', 0.02)
            """,
        )

    repairs = pr.reconcile_position_flags(db_path=db)

    assert len(repairs) == 1, f"Expected 1 repair, got {len(repairs)}: {repairs}"
    repair = repairs[0]
    assert repair["symbol"] == "BTCUSDT"
    assert "scale_33_done" in repair["flags_set"]

    # Verify DB was actually updated
    with sqlite3.connect(proof_runtime.db_path) as c:
        row = c.execute(
            "SELECT scale_33_done FROM open_positions WHERE symbol='BTCUSDT' AND paper=1"
        ).fetchone()
    assert row is not None
    assert row[0] == 1, f"scale_33_done not set to 1, got {row[0]}"


# ── Test 10: get_lane_economics crypto values ─────────────────────────────────

def test_lane_economics_crypto():
    """get_lane_economics('crypto') returns taker_fee_pct=0.0003 and round_trip=0.0006."""
    from runtime.economics import get_lane_economics

    econ = get_lane_economics("crypto")
    assert econ.lane_id == "crypto"
    assert econ.taker_fee_pct == pytest.approx(0.0003)
    assert econ.round_trip_cost_pct == pytest.approx(0.0006)
    assert econ.maker_fee_pct == pytest.approx(0.0)


# ── Test 11: get_lane_economics forecast zero commission ──────────────────────

def test_lane_economics_forecast_zero_commission():
    """get_lane_economics('forecast') returns round_trip_cost_pct=0.0."""
    from runtime.economics import get_lane_economics

    econ = get_lane_economics("forecast")
    assert econ.lane_id == "forecast"
    assert econ.taker_fee_pct == pytest.approx(0.0)
    assert econ.round_trip_cost_pct == pytest.approx(0.0)
    assert econ.maker_fee_pct == pytest.approx(0.0)


# ── Test 12: GlobalAllocator register + get_available_capital ────────────────

def test_allocator_scaffold():
    """GlobalAllocator register_lane_budget + get_available_capital doesn't raise."""
    from runtime.allocator import GlobalAllocator

    allocator = GlobalAllocator()

    # Register a lane budget without raising
    allocator.register_lane_budget(
        lane_id="crypto",
        max_deployed_usd=4500.0,
        max_concurrent_positions=5,
    )

    available = allocator.get_available_capital("crypto")
    assert available == pytest.approx(4500.0)

    # Update deployed and verify available decreases
    allocator.update_lane_deployed("crypto", deployed_usd=1000.0, positions=1)
    available_after = allocator.get_available_capital("crypto")
    assert available_after == pytest.approx(3500.0)

    # Unknown lane returns 0.0 — no raise
    unknown = allocator.get_available_capital("nonexistent_lane")
    assert unknown == pytest.approx(0.0)


# ── Test 13: LaneRegistry mes_archived disabled by default ───────────────────

def test_lane_registry_mes_disabled_by_default(monkeypatch):
    """LaneRegistry has mes_archived not in get_active_lane_ids() when FUTURES_LANE_ACTIVE=False."""
    import config

    monkeypatch.setattr(config, "FUTURES_LANE_ACTIVE", False, raising=False)
    monkeypatch.setattr(config, "PAPER_TRADING", True, raising=False)
    monkeypatch.setattr(config, "FORECAST_LANE_ACTIVE", False, raising=False)
    monkeypatch.setattr(config, "COINBASE_CDP_KEY_NAME", "", raising=False)

    # Re-import with patched config values
    import importlib
    import runtime.lane_registry as lr_mod
    importlib.reload(lr_mod)

    registry = lr_mod.LaneRegistry()

    active_ids = registry.get_active_lane_ids()
    all_ids = registry.get_lane_ids()

    # mes_archived should be registered but not active
    assert "mes_archived" in all_ids, "mes_archived should always be registered"
    assert "mes_archived" not in active_ids, (
        f"mes_archived should not be active when FUTURES_LANE_ACTIVE=False, got: {active_ids}"
    )
    # crypto should be active (PAPER_TRADING=True)
    assert "crypto" in active_ids, "crypto should be active in paper mode"


# ── Test 14: is_trade_viable crypto ──────────────────────────────────────────

def test_is_trade_viable_crypto():
    """is_trade_viable('crypto', 0.01)=True; is_trade_viable('crypto', 0.0001)=False."""
    from runtime.economics import is_trade_viable

    # 1% edge is well above the 0.8% minimum
    assert is_trade_viable("crypto", 0.01) is True

    # 0.01% edge is below the 0.8% minimum
    assert is_trade_viable("crypto", 0.0001) is False

    # Boundary: exactly at minimum should pass
    from runtime.economics import get_lane_economics
    econ = get_lane_economics("crypto")
    assert is_trade_viable("crypto", econ.min_viable_edge_pct) is True

    # Just below minimum should fail
    assert is_trade_viable("crypto", econ.min_viable_edge_pct - 0.0001) is False


# ── Test 15: active_lanes populated via upsert_system_state ──────────────────

def test_active_lanes_populated(proof_runtime, monkeypatch):
    """upsert_system_state(active_lanes='["crypto"]') → get_system_state() contains 'crypto'."""
    import json
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)

    rs.init_runtime_tables(db_path=db)
    rs.upsert_system_state(db_path=db, active_lanes=json.dumps(["crypto"]))

    state = rs.get_system_state(db_path=db)
    active_lanes_raw = state.get("active_lanes", "[]")
    active_lanes = json.loads(active_lanes_raw)
    assert "crypto" in active_lanes, (
        f"Expected 'crypto' in active_lanes, got: {active_lanes}"
    )


# ── Test 16: mark_lane_heartbeat sets a recent timestamp ─────────────────────

def test_mark_lane_heartbeat(proof_runtime, monkeypatch):
    """mark_lane_heartbeat('crypto') sets last_heartbeat_at to a recent ISO timestamp (within 5s)."""
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)

    rs.init_runtime_tables(db_path=db)
    rs.upsert_lane_state("crypto", db_path=db, enabled=1)

    before = datetime.now(timezone.utc)
    rs.mark_lane_heartbeat("crypto", db_path=db)
    after = datetime.now(timezone.utc)

    lane = rs.get_lane_state("crypto", db_path=db)
    hb_raw = lane.get("last_heartbeat_at")
    assert hb_raw is not None and hb_raw != "", "last_heartbeat_at should not be blank"

    hb_ts = datetime.fromisoformat(hb_raw)
    if not hb_ts.tzinfo:
        hb_ts = hb_ts.replace(tzinfo=timezone.utc)
    age_secs = (after - hb_ts).total_seconds()
    assert age_secs <= 5, f"Heartbeat timestamp too old: {age_secs:.2f}s"
    assert hb_ts >= before, "Heartbeat timestamp should be >= time before call"


# ── Test 17: forecast lane state update (active/connected/readiness) ──────────

def test_forecast_lane_state_update(proof_runtime, monkeypatch):
    """upsert_lane_state('forecast', active=1, connected=1, readiness_state='NO_UNDERLIERS') is readable."""
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)

    rs.init_runtime_tables(db_path=db)
    rs.upsert_lane_state(
        "forecast",
        db_path=db,
        active=1,
        connected=1,
        readiness_state="NO_UNDERLIERS",
    )

    lane = rs.get_lane_state("forecast", db_path=db)
    assert lane["lane_id"] == "forecast"
    assert lane["active"] == 1
    assert lane["connected"] == 1
    assert lane["readiness_state"] == "NO_UNDERLIERS"
