"""
tests/proof/test_dashboard_truth_contract.py — Proof suite for dashboard data isolation (v16.14).

Invariants proved:
  DT-01  get_open_positions returns all positions (perp + spot) for current mode
  DT-02  get_perp_positions excludes spot_ strategy rows
  DT-03  get_spot_positions_dashboard returns only spot_ strategy rows
  DT-04  balance.py _unrealized_pnl excludes spot_ strategy rows
  DT-05  spot and perp position counts are independent
  DT-06  paper spot balance summary uses DB-backed spot rows
  DT-07  account equity includes spot unrealized P&L
"""

from __future__ import annotations

import os
import sys
import sqlite3
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DASHBOARD_ROOT = os.path.join(ROOT, "dashboard")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if DASHBOARD_ROOT not in sys.path:
    sys.path.insert(0, DASHBOARD_ROOT)


@pytest.fixture(autouse=True)
def _clear_dashboard_module_cache():
    yield
    for mod_name in ("data.positions", "data.account", "data.balance"):
        sys.modules.pop(mod_name, None)


def _make_db_with_mixed_positions(tmp_path, paper_int: int = 1) -> str:
    """Create DB with one perp position and one spot position."""
    db = str(tmp_path / "trades.db")
    with sqlite3.connect(db) as c:
        c.execute(
            """CREATE TABLE open_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, strategy TEXT, qty REAL, entry REAL,
                stop REAL DEFAULT 0, target REAL DEFAULT 0,
                direction TEXT DEFAULT 'LONG',
                paper INTEGER DEFAULT 1, ts_entry TEXT,
                leverage INTEGER DEFAULT 3
            )"""
        )
        # Perp position
        c.execute(
            "INSERT INTO open_positions (symbol, strategy, qty, entry, paper, direction) "
            "VALUES (?,?,?,?,?,?)",
            ("ETH", "v10_perp", 0.1, 2300.0, paper_int, "LONG"),
        )
        # Spot position
        c.execute(
            "INSERT INTO open_positions (symbol, strategy, qty, entry, paper, direction) "
            "VALUES (?,?,?,?,?,?)",
            ("BTC", "spot_btc", 0.001, 85000.0, paper_int, "LONG"),
        )
    return db


def _make_db_mock(db_path: str, paper_flag: int = 1):
    """Build a minimal db module mock for the given DB path."""
    db_mock = types.ModuleType("db")
    db_mock.DB_PATH = db_path
    db_mock.LOG_PATH = db_path + ".log"
    db_mock._runtime_paper_flag = lambda: paper_flag

    def _q(sql, params=()):
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    db_mock._q = _q
    db_mock._q1 = lambda *a, **kw: {}
    db_mock.get_effective_launch_date = lambda: "2026-01-01"
    return db_mock


# ── DT-01: get_open_positions returns all positions (both spot and perp) ──────


def test_dt01_get_open_positions_returns_all(tmp_path, monkeypatch):
    """get_open_positions() returns both perp and spot rows for the given mode."""
    db = _make_db_with_mixed_positions(tmp_path, paper_int=1)
    db_mock = _make_db_mock(db)

    monkeypatch.setitem(sys.modules, "db", db_mock)
    # Force fresh import of data.positions
    monkeypatch.delitem(sys.modules, "data.positions", raising=False)

    from data.positions import get_open_positions

    positions = get_open_positions()
    strategies = {p["strategy"] for p in positions}
    assert "v10_perp" in strategies, "get_open_positions must include perp positions"
    assert "spot_btc" in strategies, "get_open_positions must include spot positions"
    assert len(positions) == 2


# ── DT-02: get_perp_positions excludes spot_ strategy rows ───────────────────


def test_dt02_get_perp_positions_excludes_spot(tmp_path, monkeypatch):
    """get_perp_positions() must never return rows with strategy LIKE 'spot_%'."""
    db = _make_db_with_mixed_positions(tmp_path, paper_int=1)
    db_mock = _make_db_mock(db)

    monkeypatch.setitem(sys.modules, "db", db_mock)
    monkeypatch.delitem(sys.modules, "data.positions", raising=False)

    from data.positions import get_perp_positions

    positions = get_perp_positions()
    for p in positions:
        assert not p["strategy"].startswith("spot_"), (
            f"get_perp_positions must not include spot_ rows, found: {p['strategy']}"
        )
    assert len(positions) == 1
    assert positions[0]["symbol"] == "ETH"


# ── DT-03: get_spot_positions_dashboard returns only spot_ rows ───────────────


def test_dt03_get_spot_positions_returns_only_spot(tmp_path, monkeypatch):
    """get_spot_positions_dashboard() must return only strategy LIKE 'spot_%' rows."""
    db = _make_db_with_mixed_positions(tmp_path, paper_int=1)
    db_mock = _make_db_mock(db)

    monkeypatch.setitem(sys.modules, "db", db_mock)
    monkeypatch.delitem(sys.modules, "data.positions", raising=False)

    from data.positions import get_spot_positions_dashboard

    positions = get_spot_positions_dashboard()
    for p in positions:
        assert p["strategy"].startswith("spot_"), (
            f"get_spot_positions_dashboard must only return spot_ rows, found: {p['strategy']}"
        )
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTC"


def test_dt03b_live_perp_positions_follow_exchange_truth(tmp_path, monkeypatch):
    """
    In live mode, stale DB perp rows must disappear when Coinbase reports no open
    CFM positions. This is the exact phantom-open-perp dashboard bug class.
    """
    db = _make_db_with_mixed_positions(tmp_path, paper_int=0)
    db_mock = _make_db_mock(db, paper_flag=0)

    class _Broker:
        def is_connected(self):
            return True

        def connect(self):
            return True

        def sync_live_positions(self):
            return {}

    broker_mod = types.ModuleType("execution.coinbase_broker")
    broker_mod.get_coinbase_broker = lambda: _Broker()

    monkeypatch.setitem(sys.modules, "db", db_mock)
    monkeypatch.setitem(sys.modules, "execution.coinbase_broker", broker_mod)
    monkeypatch.delitem(sys.modules, "data.positions", raising=False)

    from data.positions import get_perp_positions

    assert get_perp_positions() == []


def test_dt03c_live_open_positions_keep_spot_but_drop_stale_perp(
    tmp_path, monkeypatch
):
    """
    Live open-positions view must keep real spot rows while dropping stale perp DB
    rows when the exchange says futures are flat.
    """
    db = _make_db_with_mixed_positions(tmp_path, paper_int=0)
    db_mock = _make_db_mock(db, paper_flag=0)

    class _Broker:
        def is_connected(self):
            return True

        def connect(self):
            return True

        def sync_live_positions(self):
            return {}

    broker_mod = types.ModuleType("execution.coinbase_broker")
    broker_mod.get_coinbase_broker = lambda: _Broker()

    monkeypatch.setitem(sys.modules, "db", db_mock)
    monkeypatch.setitem(sys.modules, "execution.coinbase_broker", broker_mod)
    monkeypatch.delitem(sys.modules, "data.positions", raising=False)

    from data.positions import get_open_positions

    positions = get_open_positions()
    assert len(positions) == 1
    assert positions[0]["strategy"] == "spot_btc"


# ── DT-04: balance.py _unrealized_pnl excludes spot_ strategy rows ───────────


def test_dt04_unrealized_pnl_excludes_spot():
    """
    _unrealized_pnl() queries open_positions WHERE strategy NOT LIKE 'spot_%'.
    Verify the SQL in balance.py contains the exclusion clause.
    """
    balance_path = os.path.join(ROOT, "dashboard", "data", "balance.py")
    with open(balance_path) as f:
        source = f.read()

    assert "strategy NOT LIKE 'spot_%'" in source, (
        "balance.py _unrealized_pnl must exclude spot_ strategy rows "
        "via WHERE strategy NOT LIKE 'spot_%'"
    )


# ── DT-05: spot and perp position counts are independent ─────────────────────


def test_dt05_spot_perp_counts_are_independent(tmp_path, monkeypatch):
    """
    A spot position must not inflate the perp position count,
    and a perp position must not appear in spot counts.
    """
    db = _make_db_with_mixed_positions(tmp_path, paper_int=1)
    db_mock = _make_db_mock(db)

    monkeypatch.setitem(sys.modules, "db", db_mock)
    monkeypatch.delitem(sys.modules, "data.positions", raising=False)

    from data.positions import get_perp_positions, get_spot_positions_dashboard

    perp_count = len(get_perp_positions())
    spot_count = len(get_spot_positions_dashboard())

    assert perp_count == 1, f"Expected 1 perp position, got {perp_count}"
    assert spot_count == 1, f"Expected 1 spot position, got {spot_count}"
    # Total = sum of both, not double-counted
    assert perp_count + spot_count == 2


def test_dt06_paper_spot_balance_summary_uses_db_positions(tmp_path, monkeypatch):
    """
    Paper spot balance summary must not show all-zero placeholders when spot
    positions exist in open_positions.
    """
    db = _make_db_with_mixed_positions(tmp_path, paper_int=1)
    db_mock = _make_db_mock(db)

    monkeypatch.setitem(sys.modules, "db", db_mock)
    monkeypatch.delitem(sys.modules, "data.balance", raising=False)

    from data import balance as balance_mod

    monkeypatch.setattr(balance_mod, "_DB_PATH", db, raising=False)
    monkeypatch.setattr(balance_mod, "_balance_paper_mode", lambda: True, raising=False)

    summary = balance_mod.get_spot_balance_summary()
    assert summary["source"] == "paper_db"
    assert summary["btc_held_usd"] > 0.0
    assert summary["eth_held_usd"] == 0.0


def test_dt07_get_account_includes_spot_unrealized(tmp_path, monkeypatch):
    db = _make_db_with_mixed_positions(tmp_path, paper_int=1)
    db_mock = _make_db_mock(db)
    db_mock.LAUNCH_DATE = "2026-01-01"

    with sqlite3.connect(db) as c:
        c.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, pnl_usd REAL, fee_usd REAL, paper INTEGER, source TEXT
            )"""
        )

    monkeypatch.setitem(sys.modules, "db", db_mock)
    monkeypatch.delitem(sys.modules, "data.account", raising=False)
    monkeypatch.delitem(sys.modules, "data.positions", raising=False)

    from data import account as account_mod

    monkeypatch.setattr(account_mod, "_spot_unrealized_pnl", lambda: 12.5, raising=False)
    monkeypatch.setattr(account_mod, "get_perp_positions", lambda: [], raising=False)
    monkeypatch.setattr(account_mod, "get_live_prices", lambda symbols: {}, raising=False)

    equity, paper, base = account_mod.get_account()
    assert equity > base, "spot unrealized should lift headline account equity above base"
