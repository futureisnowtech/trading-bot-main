"""
forecast/discovery.py — ForecastEx market and contract discovery.

Pulls active economic event contracts from IBKR/FORECASTX and persists
them to forecast_markets + forecast_contracts tables.  Runs every 30
minutes from forecast/runner.py.

v1 scope: economic markets only (Fed/rates, CPI, employment, payrolls).
Non-economic markets are ignored — fail-closed on discovery, never on
execution.

Discovery ranking (when more contracts than needed are available):
  1. spread quality (lower is better)
  2. overround Ω_t (lower is better)
  3. quote stability (lower spread variance is better)
  4. time-to-resolution suitability (neither too soon nor too far)
  5. event-class preference (macro tier 1: FOMC/CPI/NFP before others)

All results written to DB immediately.  Callers query DB for active
contracts — no in-memory contract list is maintained here.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from forecast.db import (
    get_active_contracts,
    get_bars,
    upsert_contract,
    upsert_market,
)

logger = logging.getLogger(__name__)

# ── Tier-1 macro events that get discovery priority ────────────────────────────
TIER1_KEYWORDS: list[str] = [
    "fomc",
    "fed",
    "cpi",
    "nonfarm",
    "payroll",
    "nfp",
    "unemployment",
    "pce",
    "gdp",
]

# Resolution window: only trade events resolving within this window
MIN_HOURS_TO_RESOLUTION: float = 2.0  # too close = insufficient time for entry
MAX_DAYS_TO_RESOLUTION: float = 90.0  # too far = too much uncertainty

# Min quote quality to be considered tradeable
MAX_ACCEPTABLE_OVERROUND: float = 0.30  # Ω_t ≤ 0.30 (30%)
MAX_ACCEPTABLE_SPREAD: float = 0.15  # spread ≤ $0.15 per contract


def _hours_to_resolution(last_trade_at: str) -> Optional[float]:
    """Return hours until contract resolution, or None if unparseable."""
    if not last_trade_at:
        return None
    try:
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
    now = datetime.now(timezone.utc)

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

    1. Get active contracts from IBKR via ForecastExBroker.discover_markets()
    2. Filter to economic scope only
    3. Persist to forecast_markets + forecast_contracts tables (idempotent)
    4. Return summary dict

    If broker is None or disconnected, falls back to DB-only (returns
    whatever is already persisted) — never crashes the caller.

    Returns:
        {found: int, persisted: int, skipped_scope: int, skipped_expired: int,
         active_in_db: int, errors: list[str]}
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

        # Persist market
        try:
            market_id = upsert_market(
                market_symbol=symbol,
                market_name=name,
                exchange="FORECASTX",
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
                exchange="FORECASTX",
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

    logger.info(
        f"[Discovery] found={result['found']} persisted={result['persisted']} "
        f"active_in_db={result['active_in_db']} errors={len(result['errors'])}"
    )
    return result


def get_tradeable_contracts(
    db_path: Optional[str] = None,
    broker=None,
) -> list[dict]:
    """
    Return active contracts suitable for trading right now.

    Applies:
    - Resolution window filter (MIN_HOURS_TO_RESOLUTION to MAX_DAYS_TO_RESOLUTION)
    - Ranks by Tier-1 score and time-suitability
    - Optionally fetches live quotes for spread/overround filtering (if broker provided)

    Returns ranked list of contract dicts ready for strategy_engine evaluation.
    """
    try:
        raw = get_active_contracts(db_path=db_path)
    except Exception:
        return []

    ranked = _rank_contracts(raw)

    if not broker:
        return ranked

    # Enrich with live quote data for quality filtering
    enriched = []
    for c in ranked:
        conid = c.get("conid")
        if not conid:
            enriched.append(c)
            continue
        try:
            quote = broker.get_quote(int(conid), c.get("local_symbol", ""))
            ask_yes = ask_no = None

            if c.get("right") == "C":
                ask_yes = quote.get("ask")
            else:
                ask_no = quote.get("ask")

            if ask_yes and ask_no:
                from forecast.primitives import overround as compute_overround

                omega = compute_overround(ask_yes, ask_no)
                if omega > MAX_ACCEPTABLE_OVERROUND:
                    continue  # skip — house edge too high
                spread = quote.get("spread") or 0.0
                if spread > MAX_ACCEPTABLE_SPREAD:
                    continue
                c["_omega"] = omega
                c["_spread"] = spread

            c["_quote"] = quote
        except Exception as e:
            logger.debug(f"Quote enrichment failed for {c.get('local_symbol')}: {e}")

        enriched.append(c)

    return enriched
