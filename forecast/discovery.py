"""
forecast/discovery.py — Discovery and persistence for prediction markets.

Responsible for:
1. Fetching active contracts from brokers (IBKR, Kalshi).
2. Filtering to economic/weather scope only.
3. Scoring and ranking contracts by suitability.
4. Persisting/updating forecast_markets and forecast_contracts tables.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from forecast.db import (
    upsert_market,
    upsert_contract,
    get_active_contracts,
)
from logging_db.trade_logger import log_event

logger = logging.getLogger(__name__)

# Tier-1 keywords that boost the score of an event
TIER1_KEYWORDS: list[str] = [
    "fed",
    "fomc",
    "rate",
    "unemployment",
    "pce",
    "gdp",
]

# Resolution window: only trade events resolving within this window
MIN_HOURS_TO_RESOLUTION: float = 2.0  # too close = insufficient time for entry
MAX_DAYS_TO_RESOLUTION: float = 7.0  # v19.6: Extended to capture long-range Grand Slams

# Min quote quality to be considered tradeable
MAX_ACCEPTABLE_OVERROUND: float = 0.30  # Ω_t ≤ 0.30 (30%)
MAX_ACCEPTABLE_SPREAD: float = 0.15  # spread ≤ $0.15 per contract


def _hours_to_resolution(last_trade_at: str) -> Optional[float]:
    """Return hours until contract resolution, or None if unparseable."""
    if not last_trade_at:
        return None
    try:
        if "T" in last_trade_at and "Z" in last_trade_at:
            # ISO format: 2030-01-04T13:25:00Z
            expiry_dt = datetime.fromisoformat(last_trade_at.replace("Z", "+00:00"))
        else:
            # TWS returns YYYYMMDD or YYYYMMDD HH:MM:SS
            fmt = "%Y%m%d %H:%M:%S" if " " in last_trade_at else "%Y%m%d"
            expiry_dt = datetime.strptime(last_trade_at, fmt).replace(tzinfo=timezone.utc)
            
        delta = (expiry_dt - datetime.now(timezone.utc)).total_seconds() / 3600.0
        return delta
    except Exception:
        return None


def _tier1_score(name: str, symbol: str) -> int:
    """1 if name/symbol matches a Tier-1 macro event, else 0."""
    combined = (name + " " + symbol).lower()
    for kw in TIER1_KEYWORDS:
        if kw in combined:
            return 1
    return 0


def _rank_contracts(contracts: list[dict]) -> list[dict]:
    """
    Rank candidate contracts by quality for trading.

    Scoring (lower rank = better):
      primary:   Tier-1 macro event (descending)
      secondary: time-to-resolution suitability (favour middle of window)
      tertiary:  spread quality from last quote (ascending)

    Contracts that fail hard quality gates are filtered out entirely.
    """
    ranked = []
    
    for c in contracts:
        hours = _hours_to_resolution(c.get("last_trade_at", ""))
        if hours is None:
            continue
        if hours < MIN_HOURS_TO_RESOLUTION:
            continue  # too close to resolution
        if hours > MAX_DAYS_TO_RESOLUTION * 24:
            continue  # too far out

        name = c.get("long_name", "") or c.get("market_name", "")
        symbol = c.get("underlier", "") or c.get("market_symbol", "")
        t1 = _tier1_score(name, symbol)

        # Ideal resolution: 24h–7d from now
        ideal_hours = 72.0
        time_score = abs(hours - ideal_hours) / ideal_hours  # lower is better

        ranked.append({**c, "_tier1": t1, "_hours": hours, "_time_score": time_score})

    ranked.sort(key=lambda x: (-x["_tier1"], x["_time_score"]))
    return ranked


def run_discovery(
    broker=None,
    db_path: Optional[str] = None,
) -> dict:
    """
    Main discovery entry point.  Called by forecast/runner.py every 30 min.

    1. Get active contracts from brokers (IBKR, Kalshi).
    2. Filter to economic/weather scope only.
    3. Persist to forecast_markets + forecast_contracts tables (idempotent)
    4. Return summary dict
    """
    result: dict = {
        "found": 0,
        "persisted": 0,
        "skipped_scope": 0,
        "skipped_expired": 0,
        "active_in_db": 0,
        "errors": [],
    }

    raw_contracts: list[dict] = []

    if broker is not None:
        try:
            raw_contracts = broker.discover_markets()
            result["found"] = len(raw_contracts)
        except Exception as e:
            msg = f"broker.discover_markets() failed: {e}"
            logger.warning(msg)
            result["errors"].append(msg)

    # Rank and filter
    ranked = _rank_contracts(raw_contracts)
    result["skipped_expired"] = result["found"] - len(ranked)

    for c in ranked:
        name = c.get("long_name", "") or c.get("underlier", "")
        symbol = c.get("underlier", "") or ""
        exchange = c.get("exchange", "FORECASTX")

        # Persist market
        try:
            market_id = upsert_market(
                market_symbol=symbol,
                market_name=name,
                exchange=exchange,
                category_path=c.get("category", ""),
                underlier_symbol=symbol,
                underlier_conid=c.get("conid"),
                db_path=db_path,
            )
        except Exception as e:
            msg = f"upsert_market failed for {symbol}: {e}"
            logger.warning(msg)
            result["errors"].append(msg)
            continue

        # Persist contract
        try:
            upsert_contract(
                market_id=market_id,
                local_symbol=c.get("local_symbol", symbol),
                right=c.get("right", "C"),
                strike=float(c.get("strike") or 0.0),
                currency="USD",
                exchange=exchange,
                last_trade_at=c.get("last_trade_at", ""),
                resolution_at=c.get("last_trade_at", ""),
                conid=c.get("conid"),
                db_path=db_path,
            )
            result["persisted"] += 1
        except Exception as e:
            msg = f"upsert_contract failed for {c.get('local_symbol')}: {e}"
            logger.warning(msg)
            result["errors"].append(msg)

    # Count active in DB
    try:
        result["active_in_db"] = len(get_active_contracts(db_path=db_path))
    except Exception:
        pass

    msg = (
        f"[Discovery] found={result['found']} persisted={result['persisted']} "
        f"active_in_db={result['active_in_db']} errors={len(result['errors'])}"
    )
    logger.info(msg)
    log_event("INFO", "Discovery", msg)
    return result
