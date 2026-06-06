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
            forecast_yes_prob REAL,
            model_prob_gfs REAL DEFAULT NULL,
            model_prob_ecmwf REAL DEFAULT NULL,
            weather_mode TEXT DEFAULT NULL,
            forecast_hours_to_resolution REAL DEFAULT NULL
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
    # Negative realized pnl on a correctly resolved YES should still count as outcome=1.
    conn.execute(
        """
        INSERT INTO trades (ts, broker, symbol, action, pnl_usd, contract_side, forecast_yes_prob)
        VALUES (?, 'kalshi', 'KXRAINTEST-YES', 'BUY', 0.0, 'YES', 0.80)
        """,
        (resolved_at,),
    )
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
        VALUES (?, 'kalshi', 'KXRAINTEST-NO', 'BUY', 0.0, 'NO', 0.20)
        """,
        (resolved_at,),
    )
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
        VALUES (?, 'kalshi', 'KXRAINEMPTY', 'BUY', 0.0, 'YES', 0.90)
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


def test_weather_rbi_publishes_adaptive_model_weights(tmp_path, monkeypatch):
    import learning.weather_rbi as rbi

    db = str(tmp_path / "weather_rbi_weights.db")
    _seed_trade_db(db)

    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db)
    for contract_id, symbol, resolved_side, forecast_yes_prob, gfs_prob, ec_prob in [
        (1, "KXHIGHTEST-A", "YES", 0.78, 0.95, 0.78),
        (2, "KXHIGHTEST-B", "NO", 0.35, 0.75, 0.22),
        (3, "KXHIGHTEST-C", "NO", 0.28, 0.68, 0.18),
        (4, "KXHIGHTEST-D", "YES", 0.72, 0.90, 0.74),
    ]:
        resolved_at = (now - timedelta(days=contract_id - 1)).isoformat()
        conn.execute(
            "INSERT INTO forecast_contracts (id, local_symbol) VALUES (?, ?)",
            (contract_id, symbol),
        )
        conn.execute(
            "INSERT INTO forecast_resolutions (contract_id, resolved_side, resolved_at) VALUES (?, ?, ?)",
            (contract_id, resolved_side, resolved_at),
        )
        conn.execute(
            """
            INSERT INTO trades (
                ts, broker, symbol, action, pnl_usd, contract_side, forecast_yes_prob,
                model_prob_gfs, model_prob_ecmwf, weather_mode, forecast_hours_to_resolution
            )
            VALUES (?, 'kalshi', ?, 'BUY', 0.0, 'YES', ?, ?, ?, 'HIGH', 18.0)
            """,
            (resolved_at, symbol, forecast_yes_prob, gfs_prob, ec_prob),
        )
        conn.execute(
            """
            INSERT INTO trades (
                ts, broker, symbol, action, pnl_usd, contract_side, forecast_yes_prob,
                model_prob_gfs, model_prob_ecmwf, weather_mode, forecast_hours_to_resolution
            )
            VALUES (?, 'kalshi', ?, 'SELL', 1.0, 'YES', ?, ?, ?, 'HIGH', 18.0)
            """,
            (resolved_at, symbol, forecast_yes_prob, gfs_prob, ec_prob),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(rbi, "DB_PATH", db)
    monkeypatch.setattr(rbi, "init_forecast_db", lambda: None)

    rbi.run_weather_rbi(force=True)

    conn = sqlite3.connect(db)
    row = conn.execute(
        """
        SELECT segment, sample_size, gfs_weight, ecmwf_weight, shrinkage
        FROM weather_model_skill_state
        WHERE segment = 'HIGH'
        """
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "HIGH"
    assert row[1] == 4
    assert round(row[2] + row[3], 6) == 1.0
    assert row[3] > row[2]
    assert 0.0 < row[4] < 1.0
