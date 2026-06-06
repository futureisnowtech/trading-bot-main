"""
forecast/resolution_sync.py — Conservative resolution ingestion for weather.

This module only writes forecast_resolutions when ground truth is explicit in
the live weather shadow state. Unsupported contracts fail closed.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import pytz

from config import DB_PATH
from forecast.db import init_forecast_db, insert_resolution
from forecast.weather_contracts import resolve_weather_contract, resolve_weather_observation

logger = logging.getLogger(__name__)


def _weather_stations() -> dict:
    from data.kalshi_weather_monitor import STATIONS

    return STATIONS


def get_weather_data(ticker: str):
    from data.kalshi_weather_monitor import get_weather_data as _get_weather_data

    return _get_weather_data(ticker)


def get_contract_observed_weather_data(
    ticker: str,
    *,
    contract_name: str = "",
    strike: float | None = None,
    resolution_at: str = "",
    last_trade_at: str = "",
):
    from data.kalshi_weather_monitor import (
        get_contract_observed_weather_data as _get_contract_observed_weather_data,
    )

    return _get_contract_observed_weather_data(
        ticker,
        contract_name=contract_name,
        strike=strike,
        resolution_at=resolution_at,
        last_trade_at=last_trade_at,
    )


def _station_for_ticker(ticker: str) -> dict | None:
    symbol = (ticker or "").upper()
    for station in _weather_stations().values():
        if any(symbol.startswith(series) for series in station.get("series", [])):
            return station
    return None


def _parse_resolution_deadline(ticker: str, value: str) -> datetime | None:
    if not value:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        if "T" in text:
            deadline = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            return deadline.astimezone(timezone.utc)

        if " " in text:
            deadline = datetime.strptime(text, "%Y%m%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            return deadline

        station = _station_for_ticker(ticker)
        if station is None:
            return None

        local_tz = pytz.timezone(station.get("tz", "UTC"))
        local_eod = local_tz.localize(datetime.strptime(text, "%Y%m%d")).replace(
            hour=23, minute=59, second=59
        )
        return local_eod.astimezone(timezone.utc)
    except Exception:
        return None


def determine_weather_resolution(
    ticker: str,
    observed_high: float | None,
    observed_low: float | None,
    observed_precip: float | None = None,
    observed_temp: float | None = None,
    contract_name: str = "",
    strike: float | None = None,
) -> tuple[str, float, str] | None:
    """Return (resolved_side, resolved_value, notes) for supported contracts."""
    return resolve_weather_observation(
        ticker=ticker,
        observed_high=observed_high,
        observed_low=observed_low,
        observed_precip=observed_precip,
        observed_temp=observed_temp,
        contract_name=contract_name,
        strike=strike,
    )


def sync_forecast_resolutions(
    db_path: str = DB_PATH,
    now: datetime | None = None,
) -> dict:
    """
    Persist weather contract resolutions when contract-date observed truth is present.
    """
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    summary = {
        "checked": 0,
        "inserted": 0,
        "skipped_not_due": 0,
        "skipped_unsupported": 0,
        "skipped_no_ground_truth": 0,
    }

    init_forecast_db(db_path=db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT c.id,
                   c.local_symbol,
                   c.contract_name,
                   c.strike,
                   c.resolution_at,
                   c.last_trade_at,
                   COALESCE(c.resolution_at, c.last_trade_at) AS resolution_key
            FROM forecast_contracts c
            LEFT JOIN forecast_resolutions r ON r.contract_id = c.id
            WHERE r.id IS NULL
              AND COALESCE(c.resolution_at, c.last_trade_at, '') != ''
            """
        ).fetchall()

    for row in rows:
        summary["checked"] += 1
        ticker = str(row["local_symbol"] or "")
        deadline = _parse_resolution_deadline(ticker, row["resolution_key"])
        if deadline is None or now_utc < deadline:
            summary["skipped_not_due"] += 1
            continue

        observed = get_contract_observed_weather_data(
            ticker,
            contract_name=str(row["contract_name"] or ""),
            strike=float(row["strike"]) if row["strike"] is not None else None,
            resolution_at=str(row["resolution_at"] or ""),
            last_trade_at=str(row["last_trade_at"] or ""),
        )
        if not observed:
            summary["skipped_no_ground_truth"] += 1
            continue
        if all(
            observed.get(key) is None
            for key in ("observed_high", "observed_low", "observed_precip", "observed_temp")
        ):
            summary["skipped_no_ground_truth"] += 1
            continue
        semantics = resolve_weather_contract(
            ticker=ticker,
            contract_name=str(row["contract_name"] or ""),
            strike=float(row["strike"]) if row["strike"] is not None else None,
        )
        if semantics is not None and not semantics.ambiguous:
            if semantics.mode == "HIGH" and observed.get("observed_high") is None:
                summary["skipped_no_ground_truth"] += 1
                continue
            if semantics.mode == "LOW" and observed.get("observed_low") is None:
                summary["skipped_no_ground_truth"] += 1
                continue
            if semantics.mode in {"RAIN", "SNOW"} and observed.get("observed_precip") is None:
                summary["skipped_no_ground_truth"] += 1
                continue
            if semantics.mode == "TEMP" and observed.get("observed_temp") is None:
                summary["skipped_no_ground_truth"] += 1
                continue

        resolution = determine_weather_resolution(
            ticker=ticker,
            observed_high=observed.get("observed_high"),
            observed_low=observed.get("observed_low"),
            observed_precip=observed.get("observed_precip"),
            observed_temp=observed.get("observed_temp"),
            contract_name=str(row["contract_name"] or ""),
            strike=float(row["strike"]) if row["strike"] is not None else None,
        )
        if resolution is None:
            summary["skipped_unsupported"] += 1
            continue

        resolved_side, resolved_value, notes = resolution
        insert_resolution(
            contract_id=int(row["id"]),
            resolved_side=resolved_side,
            resolved_value=resolved_value,
            resolved_at=now_utc.isoformat(),
            notes=notes,
            source=str(observed.get("source") or "kalshi"),
            db_path=db_path,
        )
        summary["inserted"] += 1
        logger.info(
            "[ResolutionSync] %s resolved %s (%s)",
            ticker,
            resolved_side,
            notes,
        )

    return summary
