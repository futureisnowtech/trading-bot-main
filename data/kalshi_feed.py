"""
data/kalshi_feed.py — Kalshi market scanner.

Kalshi is a CFTC-regulated prediction market exchange. Unlike Polymarket (crypto),
Kalshi uses USD directly and is fully legal in the US for retail traders.

API docs: https://trading-api.readme.io/reference/
Demo environment: demo-api.kalshi.co
Live environment: trading-api.kalshi.co

Built from scratch using Kalshi REST API spec + Polymarket feed as structural template.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Any

import requests

logger = logging.getLogger(__name__)

KALSHI_DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"
KALSHI_LIVE_BASE = "https://trading-api.kalshi.co/trade-api/v2"
_REQUEST_TIMEOUT = 20.0

# Market category classification
_KALSHI_CATEGORIES = {
    "ECONOMIC": ["cpi", "pce", "fed", "inflation", "unemployment", "jobs", "gdp", "fomc"],
    "POLITICAL": ["election", "president", "congress", "senate", "vote", "bill", "supreme"],
    "WEATHER": ["hurricane", "temperature", "noaa", "storm", "rainfall"],
    "FINANCIAL": ["sp500", "nasdaq", "dow", "market", "earnings", "stock", "rate"],
    "CRYPTO": ["bitcoin", "btc", "ethereum", "eth", "crypto"],
    "SPORTS": ["nfl", "nba", "mlb", "nhl", "championship", "super bowl"],
    "GEOPOLITICAL": ["war", "ceasefire", "treaty", "sanction", "military"],
}


@dataclass
class KalshiMarket:
    """A single Kalshi prediction market (event + series)."""
    ticker: str               # e.g. KXINFL-25APR or INXD-25JUN21-B5500
    event_ticker: str         # parent event ticker
    title: str
    subtitle: str = ""
    category: str = ""
    market_type: str = "UNKNOWN"
    yes_bid: float = 0.0       # 0-100 cents
    yes_ask: float = 0.0
    last_price: float = 0.0    # in cents (0-99)
    volume: float = 0.0        # in contracts
    open_interest: float = 0.0
    close_time: Optional[dt.datetime] = None
    status: str = "open"       # open | closed | finalized
    result: Optional[str] = None  # "yes" | "no" | None

    @property
    def yes_price(self) -> float:
        """Mid price normalised to 0-1 probability."""
        mid = (self.yes_bid + self.yes_ask) / 2 if (self.yes_bid and self.yes_ask) else self.last_price
        return min(0.99, max(0.01, mid / 100.0))

    @property
    def spread_cents(self) -> float:
        return abs(self.yes_ask - self.yes_bid)

    @property
    def days_to_expiry(self) -> float:
        if self.close_time is None:
            return 0.0
        now = dt.datetime.now(dt.timezone.utc)
        ct = self.close_time
        if ct.tzinfo is None:
            ct = ct.replace(tzinfo=dt.timezone.utc)
        return max(0.0, (ct - now).total_seconds() / 86400.0)

    def is_tradeable(
        self,
        min_volume: float = 500,
        min_days: float = 1.0,
        max_days: float = 90.0,
        max_spread_cents: float = 6.0,
    ) -> bool:
        return (
            self.status == "open"
            and self.volume >= min_volume
            and min_days <= self.days_to_expiry <= max_days
            and self.spread_cents <= max_spread_cents
            and 0.03 <= self.yes_price <= 0.97
        )


def _classify(title: str, subtitle: str = "", category: str = "") -> str:
    text = f"{title} {subtitle} {category}".lower()
    for mtype, keywords in _KALSHI_CATEGORIES.items():
        if any(kw in text for kw in keywords):
            return mtype
    return "UNKNOWN"


def _parse_dt(raw: Any) -> Optional[dt.datetime]:
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def parse_kalshi_market(raw: dict) -> KalshiMarket:
    """Parse a raw Kalshi market API response."""
    yes_bid = float(raw.get("yes_bid", raw.get("yes_bid_price", 0)) or 0)
    yes_ask = float(raw.get("yes_ask", raw.get("yes_ask_price", 0)) or 0)
    last    = float(raw.get("last_price", raw.get("previous_price", 0)) or 0)
    title   = raw.get("title", raw.get("question", ""))
    cat     = raw.get("category", raw.get("series_ticker", ""))

    return KalshiMarket(
        ticker=raw.get("ticker", raw.get("id", "")),
        event_ticker=raw.get("event_ticker", raw.get("series_ticker", "")),
        title=title,
        subtitle=raw.get("subtitle", ""),
        category=cat,
        market_type=_classify(title, raw.get("subtitle", ""), cat),
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        last_price=last,
        volume=float(raw.get("volume", raw.get("volume_24h", 0)) or 0),
        open_interest=float(raw.get("open_interest", 0) or 0),
        close_time=_parse_dt(raw.get("close_time", raw.get("expiration_time"))),
        status=raw.get("status", "open"),
        result=raw.get("result"),
    )


class KalshiClient:
    """Synchronous REST client for the Kalshi trading API."""

    def __init__(self, paper: bool = True, api_key: str = "", api_secret: str = ""):
        self._base = KALSHI_DEMO_BASE if paper else KALSHI_LIVE_BASE
        self._paper = paper
        self._api_key = api_key
        self._api_secret = api_secret
        self._token: Optional[str] = None

    def _headers(self) -> dict:
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _login(self) -> bool:
        """Authenticate with Kalshi API. Returns True on success."""
        if not (self._api_key and self._api_secret):
            return False
        try:
            resp = requests.post(
                f"{self._base}/login",
                json={"email": self._api_key, "password": self._api_secret},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            self._token = resp.json().get("token", "")
            return bool(self._token)
        except Exception as e:
            logger.error(f"[kalshi_feed] Login failed: {e}")
            return False

    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> Any:
        url = self._base.rstrip("/") + path
        for attempt in range(retries):
            try:
                resp = requests.get(url, params=params,
                                    headers=self._headers(), timeout=_REQUEST_TIMEOUT)
                if resp.status_code == 401 and attempt == 0:
                    self._login()
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(f"[kalshi_feed] GET {path} attempt {attempt+1} failed: {e}. Retry in {wait}s")
                time.sleep(wait)

    def get_markets(
        self,
        status: str = "open",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> list[KalshiMarket]:
        """Fetch active Kalshi markets."""
        params: dict = {"status": status, "limit": str(limit)}
        if cursor:
            params["cursor"] = cursor
        try:
            data = self._get("/markets", params=params)
            raw_markets = data.get("markets", [])
            return [parse_kalshi_market(m) for m in raw_markets]
        except Exception as e:
            logger.error(f"[kalshi_feed] get_markets failed: {e}")
            return []

    def get_market(self, ticker: str) -> Optional[KalshiMarket]:
        """Fetch a single Kalshi market by ticker."""
        try:
            data = self._get(f"/markets/{ticker}")
            return parse_kalshi_market(data.get("market", data))
        except Exception as e:
            logger.error(f"[kalshi_feed] get_market({ticker}) failed: {e}")
            return None

    def get_market_price(self, ticker: str) -> float:
        """Get current YES mid price (0-1) for a market."""
        m = self.get_market(ticker)
        return m.yes_price if m else 0.0


def fetch_active_kalshi_markets(
    paper: bool = True,
    min_volume: float = 500,
    api_key: str = "",
    api_secret: str = "",
) -> list[KalshiMarket]:
    """
    Fetch and filter active Kalshi markets.
    No auth required for market discovery in demo mode.
    Fail-open: returns [] on error.
    """
    try:
        client = KalshiClient(paper=paper, api_key=api_key, api_secret=api_secret)
        markets = client.get_markets(status="open", limit=200)
        tradeable = [m for m in markets if m.is_tradeable(min_volume=min_volume)]
        # Sort by volume descending
        tradeable.sort(key=lambda m: m.volume, reverse=True)
        logger.info(f"[kalshi_feed] {len(tradeable)} tradeable markets (min_vol={min_volume})")
        return tradeable
    except Exception as e:
        logger.error(f"[kalshi_feed] fetch_active_kalshi_markets failed: {e}")
        return []
