import sqlite3
from datetime import datetime, timedelta, timezone


def _seed_trade_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            broker TEXT,
            symbol TEXT,
            action TEXT,
            pnl_usd REAL DEFAULT 0,
            contract_side TEXT,
            forecast_yes_prob REAL
        );
        CREATE TABLE forecast_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            local_symbol TEXT
        );
        CREATE TABLE forecast_resolutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER,
            resolved_side TEXT,
            resolved_at TEXT
        );
        CREATE TABLE weather_calibration (
            ts TEXT PRIMARY KEY,
            brier_score REAL,
            win_rate REAL,
            ensemble_accuracy REAL,
            sample_size INTEGER,
            edge_decay REAL
        );
        """
    )
    conn.commit()
    conn.close()


def test_weather_rbi_uses_resolution_labels_and_no_inversion(tmp_path, monkeypatch):
    import learning.weather_rbi as rbi

    db = str(tmp_path / "weather_rbi.db")
    _seed_trade_db(db)

    now = datetime.now(timezone.utc)
    resolved_at = now.isoformat()

    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO forecast_contracts (id, local_symbol) VALUES (1, 'KXRAINTEST-YES')")
    conn.execute("INSERT INTO forecast_contracts (id, local_symbol) VALUES (2, 'KXRAINTEST-NO')")
    conn.execute(
        "INSERT INTO forecast_resolutions (contract_id, resolved_side, resolved_at) VALUES (1, 'YES', ?)",
        (resolved_at,),
    )
    conn.execute(
        "INSERT INTO forecast_resolutions (contract_id, resolved_side, resolved_at) VALUES (2, 'NO', ?)",
        (resolved_at,),
    )
    # Negative pnl on a correctly resolved YES should still count as outcome=1.
    conn.execute(
        """
        INSERT INTO trades (ts, broker, symbol, action, pnl_usd, contract_side, forecast_yes_prob)
        VALUES (?, 'kalshi', 'KXRAINTEST-YES', 'SELL', -2.0, 'YES', 0.80)
        """,
        (resolved_at,),
    )
    # NO contract should use inverse chosen probability: 1 - forecast_yes_prob = 0.80.
    conn.execute(
        """
        INSERT INTO trades (ts, broker, symbol, action, pnl_usd, contract_side, forecast_yes_prob)
        VALUES (?, 'kalshi', 'KXRAINTEST-NO', 'SELL', 1.0, 'NO', 0.20)
        """,
        (resolved_at,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(rbi, "DB_PATH", db)
    monkeypatch.setattr(rbi, "init_forecast_db", lambda: None)

    rbi.run_weather_rbi()

    conn = sqlite3.connect(db)
    row = conn.execute(
        """
        SELECT brier_score, win_rate, ensemble_accuracy, sample_size
        FROM weather_calibration
        ORDER BY ts DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row is not None
    assert round(row[0], 4) == 0.04
    assert round(row[1], 4) == 1.0
    assert round(row[2], 4) == 0.8
    assert row[3] == 2


def test_weather_rbi_skips_without_labeled_resolutions(tmp_path, monkeypatch):
    import learning.weather_rbi as rbi

    db = str(tmp_path / "weather_rbi_empty.db")
    _seed_trade_db(db)

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO forecast_contracts (id, local_symbol) VALUES (1, 'KXRAINEMPTY')")
    conn.execute(
        """
        INSERT INTO trades (ts, broker, symbol, action, pnl_usd, contract_side, forecast_yes_prob)
        VALUES (?, 'kalshi', 'KXRAINEMPTY', 'SELL', 5.0, 'YES', 0.90)
        """,
        (now,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(rbi, "DB_PATH", db)
    monkeypatch.setattr(rbi, "init_forecast_db", lambda: None)

    rbi.run_weather_rbi()

    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM weather_calibration").fetchone()[0]
    conn.close()

    assert count == 0
