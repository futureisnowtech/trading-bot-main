from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_history_range_uses_station_local_yesterday():
    from scripts.backfill_station_history import _history_date_range

    start, end = _history_date_range(
        "America/Los_Angeles",
        2,
        now_utc=datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc),
    )
    assert (start, end) == ("2026-08-22", "2026-08-23")


def test_archive_backfill_versions_provenance_and_preserves_other_sources(
    tmp_path,
    monkeypatch,
):
    import scripts.backfill_station_history as backfill
    from forecast.db import init_forecast_db

    db_path = tmp_path / "history.db"
    init_forecast_db(str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO noaa_daily_summaries
            (station, date, temp_max, temp_min, precipitation, source)
            VALUES ('KAAA', '2026-08-23', 99, 50, 0, 'noaa_observation')
            """
        )

    monkeypatch.setattr(
        backfill,
        "STATIONS",
        {"AAA": {"icao": "KAAA", "lat": 1.0, "lon": 2.0, "tz": "UTC"}},
    )
    monkeypatch.setattr(backfill.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        backfill.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            {
                "daily": {
                    "time": ["2026-08-23", "2026-08-24"],
                    "temperature_2m_max": [70, 71],
                    "temperature_2m_min": [50, 51],
                    "precipitation_sum": [0, 0.1],
                }
            }
        ),
    )

    assert backfill.backfill_station_history(days_back=2, db_path=str(db_path)) == 2
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT date, temp_max, source FROM noaa_daily_summaries ORDER BY date"
        ).fetchall()

    assert rows[0] == ("2026-08-23", 99.0, "noaa_observation")
    assert rows[1] == (
        "2026-08-24",
        71.0,
        backfill.HISTORY_SOURCE,
    )


def test_deploy_bootstraps_and_retries_full_history_daily():
    from pathlib import Path

    deploy = (Path(__file__).parents[2] / "deploy.sh").read_text(encoding="utf-8")
    assert "Bootstrapping 120 local-calendar days" in deploy
    assert deploy.count("backfill_station_history.py --days-back 120") >= 2
