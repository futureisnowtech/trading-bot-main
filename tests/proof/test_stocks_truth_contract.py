from __future__ import annotations

import os
import sqlite3
import sys
import types


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DASHBOARD_ROOT = os.path.join(ROOT, "dashboard")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if DASHBOARD_ROOT not in sys.path:
    sys.path.insert(0, DASHBOARD_ROOT)


def _make_stocks_db(tmp_path, paper_int: int = 0) -> str:
    db = str(tmp_path / "trades.db")
    with sqlite3.connect(db) as c:
        c.execute(
            """CREATE TABLE open_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, strategy TEXT, qty REAL, entry REAL,
                stop REAL DEFAULT 0, target REAL DEFAULT 0,
                paper INTEGER DEFAULT 0, ts_entry TEXT DEFAULT '',
                side TEXT DEFAULT 'LONG', order_id TEXT DEFAULT ''
            )"""
        )
        c.execute(
            """CREATE TABLE lane_runtime_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lane_id TEXT, health TEXT, active INTEGER, connected INTEGER,
                buying_power_usd REAL DEFAULT 0
            )"""
        )
        c.execute(
            """CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT '',
                broker TEXT DEFAULT '',
                action TEXT DEFAULT '',
                symbol TEXT DEFAULT ''
            )"""
        )
        c.execute(
            "INSERT INTO open_positions (symbol, strategy, qty, entry, stop, target, paper, ts_entry) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("AAPL", "stocks_aapl", 5, 200.0, 195.0, 210.0, paper_int, "2026-04-22T09:35:00"),
        )
        c.execute(
            "INSERT INTO lane_runtime_state (lane_id, health, active, connected, buying_power_usd) "
            "VALUES ('stocks','OK',1,1,25000.0)"
        )
    return db


def _make_db_mock(db_path: str, paper_flag: int = 0):
    db_mock = types.ModuleType("db")
    db_mock.DB_PATH = db_path
    db_mock.LOG_PATH = db_path + ".log"
    db_mock._runtime_paper_flag = lambda: paper_flag

    def _q(sql, params=()):
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    db_mock._q = _q
    db_mock._q1 = lambda sql, params=(): (_q(sql, params) or [{}])[0]
    return db_mock


def test_live_stock_positions_follow_broker_truth(tmp_path, monkeypatch):
    db = _make_stocks_db(tmp_path, paper_int=0)
    db_mock = _make_db_mock(db, paper_flag=0)

    class _StockBroker:
        def is_connected(self):
            return True

        def connect(self):
            return True

        def sync_live_positions(self):
            return {}

        def get_account_value(self):
            return 18000.0

    broker_mod = types.ModuleType("execution.ibkr_stock_broker")
    broker_mod.get_dashboard_stock_broker = lambda: _StockBroker()

    monkeypatch.setitem(sys.modules, "db", db_mock)
    monkeypatch.setitem(sys.modules, "execution.ibkr_stock_broker", broker_mod)
    monkeypatch.delitem(sys.modules, "data.stocks", raising=False)

    from data.stocks import get_stock_positions, get_stock_header

    assert get_stock_positions() == []
    hdr = get_stock_header()
    assert hdr["open_count"] == 0
    assert float(hdr["account_value"]) == 18000.0
