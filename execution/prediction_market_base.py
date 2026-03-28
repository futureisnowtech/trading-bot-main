"""
execution/prediction_market_base.py — Abstract base class for prediction market brokers.

Standardises the interface so polymarket_broker.py and kalshi_broker.py are
interchangeable, and cross-market arbitrage detection works without market-specific
branching in the scan loop.

Pattern from: CloddsBot multi-market abstraction + hummingbot base connector.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class PredictionPosition:
    """An open prediction market position."""
    position_id: str          # broker-internal order/position ID
    market_id: str            # platform market identifier
    market_question: str      # human-readable question
    platform: str             # "polymarket" | "kalshi"
    side: str                 # "YES" | "NO"
    size_shares: float        # shares / contracts held
    entry_price: float        # 0-1 probability paid
    current_price: float      # latest market price
    stop_price: float         # exit if price drops below this
    target_price: float       # exit if price rises above this
    paper: bool = True
    resolved: bool = False
    outcome: Optional[bool] = None  # True=YES won, False=NO won, None=pending

    @property
    def notional_usd(self) -> float:
        """Current mark-to-market value in USD (each share pays $1 if correct)."""
        return self.current_price * self.size_shares

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.size_shares


@dataclass
class OrderResult:
    """Result of a place_order() call."""
    success: bool
    order_id: str = ""
    filled_price: float = 0.0
    filled_shares: float = 0.0
    error: str = ""
    paper: bool = True


@dataclass
class MarketSnapshot:
    """Lightweight market summary for the scanner."""
    market_id: str
    question: str
    platform: str
    yes_price: float           # 0-1 implied probability
    volume_usd: float
    liquidity_usd: float
    days_to_expiry: float
    spread: float
    market_type: str = "UNKNOWN"


# ── Abstract base ─────────────────────────────────────────────────────────────

class BasePredictionMarketBroker(abc.ABC):
    """
    Abstract interface for a prediction market broker.

    All concrete brokers (Polymarket, Kalshi) must implement these methods.
    The scan loop calls only methods defined here — no platform branching outside
    the broker file itself.
    """

    @property
    @abc.abstractmethod
    def platform_name(self) -> str:
        """Return e.g. 'polymarket' or 'kalshi'."""

    @property
    @abc.abstractmethod
    def is_paper(self) -> bool:
        """True if running in paper/demo mode."""

    @abc.abstractmethod
    def get_markets(
        self,
        *,
        min_volume: float = 10_000,
        max_results: int = 50,
    ) -> list[MarketSnapshot]:
        """
        Discover active markets that meet minimum volume/liquidity requirements.
        Returns [] on API failure (fail-open).
        """

    @abc.abstractmethod
    def place_order(
        self,
        market_id: str,
        side: str,           # "YES" | "NO"
        size_usd: float,     # dollar amount to risk
        price: float,        # limit price (0-1)
    ) -> OrderResult:
        """
        Place a YES or NO order.
        In paper mode: log to SQLite, return synthetic OrderResult.
        In live mode: submit to exchange API.
        """

    @abc.abstractmethod
    def get_positions(self) -> list[PredictionPosition]:
        """Return all open (unresolved) positions on this platform."""

    @abc.abstractmethod
    def close_position(self, position: PredictionPosition, reason: str = "") -> OrderResult:
        """
        Exit a position before resolution (sell back at market).
        In paper mode: mark as resolved, log P&L.
        """

    @abc.abstractmethod
    def check_resolution(self, position: PredictionPosition) -> PredictionPosition:
        """
        Check if a market has resolved and update the position.
        Returns the same position with `resolved` and `outcome` updated if settled.
        """

    # ── Optional helpers with default implementations ─────────────────────────

    def is_within_position_limit(self, max_positions: int) -> bool:
        """True if we can open another position."""
        return len(self.get_positions()) < max_positions

    def get_open_exposure_usd(self) -> float:
        """Total USD at risk across all open positions."""
        return sum(p.entry_price * p.size_shares for p in self.get_positions())
