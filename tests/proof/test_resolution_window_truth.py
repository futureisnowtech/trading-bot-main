from datetime import datetime, timedelta, timezone


def test_strategy_engine_hours_to_resolution_accepts_iso_z():
    from forecast.strategy_engine import _hours_to_resolution

    future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    hours = _hours_to_resolution(future)

    assert 11.5 <= hours <= 12.5


def test_strategy_engine_hours_to_resolution_accepts_yyyymmdd():
    from forecast.strategy_engine import _hours_to_resolution

    future = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y%m%d")
    hours = _hours_to_resolution(future)

    assert hours > 24
