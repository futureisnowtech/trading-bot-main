"""
forecast/discovery.py — Discovery and persistence for prediction markets.

Responsible for:
1. Fetching active contracts from Kalshi.
2. Filtering to weather scope only.
3. Scoring and ranking contracts by suitability.
4. Persisting/updating forecast_markets and forecast_contracts tables.
5. Preserving market stubs even when contract details are incomplete.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from forecast.db import (
    deactivate_contracts_not_in_symbols,
    deactivate_markets_not_in_symbols,
    init_forecast_db,
    get_active_contracts,
    upsert_contract,
    upsert_market,
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
    "rain",
    "snow",
    "wind",
    "temperature",
]

# Resolution window: discovery must stay aligned with the live strategy horizon.
MIN_HOURS_TO_RESOLUTION: float = 2.0  # too close = insufficient time for entry
MAX_DAYS_TO_RESOLUTION: float = 2.0  # 48h max to match live strategy gating

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
    
    from forecast.weather_contracts import (
        is_hourly_weather_contract,
        is_live_entry_weather_contract,
        weather_mode_for_ticker,
    )

    for c in contracts:
        hours = _hours_to_resolution(c.get("last_trade_at", ""))
        if hours is None:
            continue
        
        # v19.10.1: Lane-aware time-to-expiry gate.
        # Hourly Temp contracts must be discovered up to 20 mins (0.33h) before resolution.
        symbol = c.get("underlier", "") or c.get("market_symbol", "") or c.get("local_symbol", "")
        mode = weather_mode_for_ticker(symbol)
        min_hours = (
            0.33
            if is_hourly_weather_contract(
                symbol,
                contract_name=c.get("contract_name", "") or c.get("market_name", ""),
            )
            else MIN_HOURS_TO_RESOLUTION
        )
        
        if hours < min_hours:
            continue  # too close to resolution
        if hours > MAX_DAYS_TO_RESOLUTION * 24:
            continue  # too far out

        name = c.get("contract_name", "") or c.get("long_name", "") or c.get("market_name", "")
        symbol = c.get("underlier", "") or c.get("market_symbol", "")
        t1 = _tier1_score(name, symbol)

        # Ideal resolution: roughly mid-window for the short-horizon weather lane.
        ideal_hours = 24.0
        time_score = abs(hours - ideal_hours) / ideal_hours  # lower is better

        ranked.append({**c, "_tier1": t1, "_hours": hours, "_time_score": time_score})

    ranked.sort(key=lambda x: (-x["_tier1"], x["_time_score"]))
    return ranked


def run_discovery(
    broker=None,
    db_path: Optional[str] = None,
) -> dict:
    """
    Main discovery entry point. Called by forecast/runner.py every 30 min.

    1. Get active contracts from Kalshi.
    2. Filter to weather scope only.
    3. Persist to forecast_markets + forecast_contracts tables (idempotent)
    4. Return summary dict
    """
    result: dict = {
        "found": 0,
        "persisted": 0,
        "stubs_persisted": 0,
        "skipped_scope": 0,
        "skipped_expired": 0,
        "deactivated_contracts": 0,
        "deactivated_markets": 0,
        "active_in_db": 0,
        "cleanup_skipped": False,
        "errors": [],
    }

    raw_contracts: list[dict] = []
    seen_market_symbols: set[str] = set()
    seen_contract_symbols: set[str] = set()
    init_forecast_db(db_path=db_path)

    if broker is not None:
        try:
            raw_contracts = broker.discover_markets()
            result["found"] = len(raw_contracts)
        except Exception as e:
            msg = f"broker.discover_markets() failed: {e}"
            logger.warning(msg)
            result["errors"].append(msg)

    stub_only: list[dict] = []
    tradable_candidates: list[dict] = []
    for contract in raw_contracts:
        if contract.get("stub_only"):
            stub_only.append(contract)
            continue
        if not contract.get("last_trade_at"):
            stub_only.append(contract)
            continue
        tradable_candidates.append(contract)

    for c in stub_only:
        event_name = c.get("event_title", "") or c.get("market_name", "") or c.get("underlier", "")
        symbol = c.get("underlier", "") or c.get("market_symbol", "") or c.get("local_symbol", "")
        exchange = c.get("exchange", "KALSHI")
        if not symbol:
            result["skipped_scope"] += 1
            continue
        seen_market_symbols.add(symbol)
        try:
            upsert_market(
                market_symbol=symbol,
                market_name=event_name or symbol,
                exchange=exchange,
                category_path=c.get("category", ""),
                underlier_symbol=symbol,
                underlier_conid=c.get("und_conid") or c.get("conid"),
                db_path=db_path,
            )
            result["stubs_persisted"] += 1
        except Exception as e:
            msg = f"upsert_market failed for stub {symbol}: {e}"
            logger.warning(msg)
            result["errors"].append(msg)

    # Rank and filter
    ranked = _rank_contracts(tradable_candidates)
    result["skipped_expired"] = len(tradable_candidates) - len(ranked)

    for c in ranked:
        event_name = c.get("event_title", "") or c.get("underlier", "")
        contract_name = c.get("contract_name", "") or c.get("long_name", "") or c.get("underlier", "")
        symbol = c.get("underlier", "") or ""
        exchange = c.get("exchange", "KALSHI")
        local_symbol = c.get("local_symbol", symbol)
        if symbol:
            seen_market_symbols.add(symbol)
        if local_symbol:
            seen_contract_symbols.add(local_symbol)

        # Persist market
        try:
            market_id = upsert_market(
                market_symbol=symbol,
                market_name=event_name or symbol,
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
                local_symbol=local_symbol,
                contract_name=contract_name,
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

    # Active-universe cleanup: preserve history, but stop evaluating rows that are
    # no longer present in the latest weather discovery pass.
    if raw_contracts and seen_market_symbols:
        try:
            result["deactivated_markets"] = deactivate_markets_not_in_symbols(
                sorted(seen_market_symbols),
                db_path=db_path,
            )
            result["deactivated_contracts"] = deactivate_contracts_not_in_symbols(
                sorted(seen_contract_symbols),
                db_path=db_path,
                deactivate_all_if_empty=True,
            )
        except Exception as e:
            msg = f"discovery cleanup failed: {e}"
            logger.warning(msg)
            result["errors"].append(msg)
    else:
        result["cleanup_skipped"] = True

    # Count active in DB
    try:
        result["active_in_db"] = len(get_active_contracts(db_path=db_path))
    except Exception:
        pass

    msg = (
        f"[Discovery] found={result['found']} persisted={result['persisted']} "
        f"stubs={result['stubs_persisted']} "
        f"deactivated_contracts={result['deactivated_contracts']} "
        f"deactivated_markets={result['deactivated_markets']} "
        f"active_in_db={result['active_in_db']} errors={len(result['errors'])}"
    )
    logger.info(msg)
    log_event("INFO", "Discovery", msg)
    return result
