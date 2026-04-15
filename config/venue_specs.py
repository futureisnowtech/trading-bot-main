"""
config/venue_specs.py — Venue-specific execution parameters.

Import this instead of reaching into config.py for venue-specific values.
All values that are execution-infrastructure (fees, contract size, session times)
belong here. Strategy thresholds belong in config/alpha_specs.py.

Usage:
    from config.venue_specs import KRAKEN_TAKER_FEE, MES_POINT_VALUE, VENUE_FEES
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── MES Futures (IBKR) ────────────────────────────────────────────────────────
# These are derived from config.py env-backed constants so quarterly rolls
# only require updating .env / config.py, not this file.

try:
    from config import (
        MES_EXPIRY,
        FUTURES_NUM_CONTRACTS,
        FUTURES_DAILY_MAX_LOSS_PTS,
        FUTURES_ENABLED,
        IBKR_HOST,
        IBKR_PORT,
        IBKR_CLIENT_ID,
    )
except ImportError:
    MES_EXPIRY = os.getenv("MES_EXPIRY", "20260619")
    FUTURES_NUM_CONTRACTS = int(os.getenv("FUTURES_NUM_CONTRACTS", "2"))
    FUTURES_DAILY_MAX_LOSS_PTS = float(os.getenv("FUTURES_DAILY_MAX_LOSS_PTS", "10"))
    FUTURES_ENABLED = os.getenv("FUTURES_ENABLED", "false").lower() == "true"
    IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
    IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))
    IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))

MES_POINT_VALUE: float = 5.00  # dollars per point
MES_TICK_SIZE: float = 0.25  # minimum price increment
MES_TICK_VALUE: float = 1.25  # dollars per tick (= TICK_SIZE * POINT_VALUE)
MES_MARGIN_PER_CONTRACT: float = 1_320.0  # approximate IBKR overnight margin (USD)
MES_SESSION_OPEN: str = "09:30"  # ET — regular trading hours open
MES_SESSION_CLOSE: str = "16:00"  # ET — regular trading hours close
MES_EOD_CLOSE_TIME: str = "15:45"  # ET — forced close before settlement
MES_MAX_DAILY_LOSS_USD: float = (
    FUTURES_DAILY_MAX_LOSS_PTS * FUTURES_NUM_CONTRACTS * MES_POINT_VALUE
)

# ── Exchange fees ─────────────────────────────────────────────────────────────

KRAKEN_TAKER_FEE: float = 0.00065  # 0.065% — modeled in economics_gate.py
BINANCE_TAKER_FEE: float = 0.0004  # 0.04%
HYPERLIQUID_TAKER_FEE: float = 0.0005  # 0.05%

VENUE_FEES: dict[str, float] = {
    "kraken": KRAKEN_TAKER_FEE,
    "binance": BINANCE_TAKER_FEE,
    "hyperliquid": HYPERLIQUID_TAKER_FEE,
}

# Effective round-trip cost (entry + exit, same venue)
VENUE_ROUND_TRIP_FEES: dict[str, float] = {
    venue: fee * 2 for venue, fee in VENUE_FEES.items()
}


def get_taker_fee(venue: str) -> float:
    """Return taker fee for a venue. Defaults to Kraken if unknown."""
    return VENUE_FEES.get(str(venue).lower(), KRAKEN_TAKER_FEE)
