import sqlite3

import config
import dashboard.jarvis_brain as jarvis_brain


def test_canonical_trade_data_boundary_is_july_23():
    assert config.TRADE_DATA_START_DATE == "2026-07-23"
    assert config.POST_PAPER_START_DATE == config.TRADE_DATA_START_DATE
    assert config.TRADE_SESSION_START == config.TRADE_DATA_START_DATE


def test_jarvis_recent_trades_excludes_older_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "trades.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE trades (
                ts TEXT, symbol TEXT, action TEXT, qty REAL, price REAL,
                pnl_usd REAL, notes TEXT, contract_side TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO trades VALUES (?, ?, 'BUY', 1, 0.5, 0, '', 'YES')",
            [
                ("2026-07-22T23:59:59+00:00", "OLD"),
                ("2026-07-23T00:00:00+00:00", "QUALITY"),
            ],
        )

    monkeypatch.setattr(jarvis_brain, "_get_db_path", lambda: str(db_path))
    result = jarvis_brain.get_recent_trades(limit=10)

    assert "QUALITY" in result
    assert "OLD" not in result
