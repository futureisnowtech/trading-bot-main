import sqlite3
from datetime import datetime, timedelta, timezone

from forecast.db import init_forecast_db, upsert_contract, upsert_market
from forecast.resolution_sync import sync_forecast_resolutions


def _make_db(tmp_path):
    db = str(tmp_path / "resolution_sync.db")
    init_forecast_db(db_path=db)
    return db


def test_resolution_sync_inserts_high_truth(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    market_id = upsert_market("KXHIGHNY", "NY High", db_path=db)
    contract_id = upsert_contract(
        market_id=market_id,
        local_symbol="KXHIGHNY-04JUN26-T90",
        contract_name="Will the high temp in NY be >90° on Jun 4, 2026?",
        right="C",
        strike=90.0,
        resolution_at="20260604",
        last_trade_at="20260604",
        db_path=db,
    )

    monkeypatch.setattr(
        "forecast.resolution_sync.get_weather_data",
        lambda ticker: {"intraday": {"daily_max": 91.2, "daily_min": 70.0}},
    )

    summary = sync_forecast_resolutions(
        db_path=db,
        now=datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc),
    )

    assert summary["inserted"] == 1

    conn = sqlite3.connect(db)
    row = conn.execute(
        """
        SELECT contract_id, resolved_side, resolved_value, source
        FROM forecast_resolutions
        """
    ).fetchone()
    conn.close()

    assert row == (contract_id, "YES", 91.2, "metar_watermark")


def test_resolution_sync_skips_unsupported_rain_contracts(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    market_id = upsert_market("KXRAINNY", "NY Rain", db_path=db)
    upsert_contract(
        market_id=market_id,
        local_symbol="KXRAINNY-04JUN26-T1",
        contract_name="Will rainfall in NY be >1 inch on Jun 4, 2026?",
        right="C",
        strike=1.0,
        resolution_at="20260604",
        last_trade_at="20260604",
        db_path=db,
    )

    monkeypatch.setattr(
        "forecast.resolution_sync.get_weather_data",
        lambda ticker: {"intraday": {"daily_max": 80.0, "daily_min": 60.0}},
    )

    summary = sync_forecast_resolutions(
        db_path=db,
        now=datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc),
    )

    assert summary["inserted"] == 0
    assert summary["skipped_unsupported"] == 1


def test_resolution_sync_resolves_bracket_contracts(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    market_id = upsert_market("KXHIGHLAX", "LA High", db_path=db)
    contract_id = upsert_contract(
        market_id=market_id,
        local_symbol="KXHIGHLAX-04JUN26-B69.5",
        contract_name="Will the high temp in LA be 69-70° on Jun 4, 2026?",
        right="C",
        strike=69.5,
        resolution_at="20260604",
        last_trade_at="20260604",
        db_path=db,
    )

    monkeypatch.setattr(
        "forecast.resolution_sync.get_weather_data",
        lambda ticker: {"intraday": {"daily_max": 69.8, "daily_min": 55.0}},
    )

    summary = sync_forecast_resolutions(
        db_path=db,
        now=datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc),
    )

    assert summary["inserted"] == 1

    conn = sqlite3.connect(db)
    row = conn.execute(
        """
        SELECT contract_id, resolved_side, resolved_value
        FROM forecast_resolutions
        """
    ).fetchone()
    conn.close()

    assert row == (contract_id, "YES", 69.8)
