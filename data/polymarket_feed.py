"""
data/polymarket_feed.py — Polymarket Gamma (REST) market scanner.

Fetches and filters active prediction markets from Polymarket's public API.
No authentication required for market discovery — only order placement needs keys.

Adapted from Fully-Autonomous-Polymarket-AI-Trading-Bot/src/connectors/polymarket_gamma.py
(sync requests instead of async httpx; dataclasses instead of pydantic; stdlib logging).
"""
import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"
_REQUEST_TIMEOUT = 20.0

# ── Market classification keywords ──────────────────────────────────────────

_TYPE_KEYWORDS: dict[str, list[str]] = {
    "MACRO": [
        "cpi", "inflation", "unemployment", "gdp", "interest rate", "fed",
        "fomc", "ecb", "nonfarm", "payroll", "pce", "yield", "recession",
        "rate cut", "rate hike", "treasury", "bls", "jobs report",
    ],
    "ELECTION": [
        "election", "vote", "president", "governor", "senate", "congress",
        "primary", "nominee", "ballot", "electoral", "poll", "caucus",
        "republican", "democrat", "midterm",
    ],
    "CORPORATE": [
        "ipo", "merger", "acquisition", "sec", "earnings", "stock",
        "ceo", "board", "filing", "shares", "revenue", "fda approval",
        "antitrust", "layoffs", "bankruptcy",
    ],
    "CRYPTO": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain",
        "defi", "nft", "solana", "binance", "coinbase", "halving",
    ],
    "SPORTS": [
        "super bowl", "nfl", "nba", "mlb", "world cup", "olympics",
        "championship", "playoffs", "mvp", "bracket",
    ],
    "GEOPOLITICAL": [
        "war", "ceasefire", "sanctions", "military", "nato", "un",
        "treaty", "nuclear", "conflict", "diplomat",
    ],
}


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class PredictionToken:
    token_id: str = ""
    outcome: str = ""
    price: float = 0.0
    winner: Optional[bool] = None


@dataclass
class PredictionMarket:
    """A single Polymarket prediction market."""
    id: str = ""
    condition_id: str = ""
    question: str = ""
    description: str = ""
    category: str = ""
    market_type: str = "UNKNOWN"
    end_date: Optional[dt.datetime] = None
    created_at: Optional[dt.datetime] = None
    active: bool = True
    closed: bool = False
    volume: float = 0.0
    liquidity: float = 0.0
    tokens: list = field(default_factory=list)
    resolution_source: str = ""
    slug: str = ""

    @property
    def best_price(self) -> float:
        """Implied probability of YES outcome (0-1)."""
        yes = [t for t in self.tokens if t.outcome.lower() == "yes"]
        if yes:
            return yes[0].price
        return self.tokens[0].price if self.tokens else 0.0

    @property
    def days_to_expiry(self) -> float:
        if self.end_date is None:
            return 0.0
        now = dt.datetime.now(dt.timezone.utc)
        end = self.end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=dt.timezone.utc)
        return max(0.0, (end - now).total_seconds() / 86400.0)

    @property
    def spread(self) -> float:
        if len(self.tokens) < 2:
            return 1.0
        prices = sorted([t.price for t in self.tokens], reverse=True)
        return abs(1.0 - sum(prices))

    @property
    def has_clear_resolution(self) -> bool:
        return bool(self.resolution_source and len(self.resolution_source) > 5)

    def is_tradeable(
        self,
        min_volume: float = 10_000,
        min_liquidity: float = 1_000,
        min_days: float = 1.0,
        max_days: float = 90.0,
        max_spread: float = 0.05,
    ) -> bool:
        """Quick filter: is this market worth analysing?"""
        return (
            self.active
            and not self.closed
            and self.volume >= min_volume
            and self.liquidity >= min_liquidity
            and min_days <= self.days_to_expiry <= max_days
            and self.spread <= max_spread
            and 0.02 <= self.best_price <= 0.98   # avoid near-resolved markets
        )


# ── Classification ───────────────────────────────────────────────────────────

def classify_market_type(question: str, category: str = "", description: str = "") -> str:
    text = f"{question} {category} {description}".lower()
    scores: dict[str, int] = {}
    for mtype, keywords in _TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[mtype] = score
    if not scores:
        return "UNKNOWN"
    return max(scores, key=scores.get)


# ── Parsing ──────────────────────────────────────────────────────────────────

def _parse_json_list(val: Any) -> list:
    """Parse JSON-encoded string or return list as-is."""
    import json
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


def _parse_dt(raw: Any) -> Optional[dt.datetime]:
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def parse_market(raw: dict) -> PredictionMarket:
    """Convert raw Gamma JSON into a PredictionMarket."""
    tokens: list[PredictionToken] = []

    raw_tokens = raw.get("tokens", [])
    if isinstance(raw_tokens, list) and raw_tokens and isinstance(raw_tokens[0], dict):
        for tok in raw_tokens:
            tokens.append(PredictionToken(
                token_id=str(tok.get("token_id", tok.get("id", ""))),
                outcome=tok.get("outcome", tok.get("value", "")),
                price=float(tok.get("price", 0)),
                winner=tok.get("winner"),
            ))
    else:
        outcomes = _parse_json_list(raw.get("outcomes", []))
        prices   = _parse_json_list(raw.get("outcomePrices", []))
        clob_ids = _parse_json_list(raw.get("clobTokenIds", []))
        for i, outcome in enumerate(outcomes):
            price    = float(prices[i])   if i < len(prices)   else 0.0
            token_id = str(clob_ids[i])   if i < len(clob_ids) else ""
            tokens.append(PredictionToken(token_id=token_id, outcome=str(outcome), price=price))

    question    = raw.get("question", raw.get("title", ""))
    category    = raw.get("category", raw.get("tag", ""))
    description = raw.get("description", "")

    return PredictionMarket(
        id=str(raw.get("id", raw.get("condition_id", ""))),
        condition_id=str(raw.get("condition_id", raw.get("conditionId", ""))),
        question=question,
        description=description,
        category=category,
        market_type=classify_market_type(question, category, description),
        end_date=_parse_dt(raw.get("end_date_iso") or raw.get("end_date") or raw.get("endDate")),
        created_at=_parse_dt(
            raw.get("startDate") or raw.get("acceptingOrdersTimestamp") or raw.get("createdAt")
        ),
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        volume=float(raw.get("volume", raw.get("volumeNum", 0))),
        liquidity=float(raw.get("liquidity", raw.get("liquidityNum", 0))),
        tokens=tokens,
        resolution_source=raw.get("resolution_source", raw.get("resolutionSource", "")),
        slug=raw.get("slug", ""),
    )


# ── REST client ──────────────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None, retries: int = 3) -> Any:
    """Synchronous GET against Gamma API with retry."""
    url = GAMMA_BASE.rstrip("/") + path
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT,
                                headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            logger.warning(f"[polymarket_feed] GET {path} attempt {attempt+1} failed: {e}. Retry in {wait}s")
            time.sleep(wait)


def list_markets(
    *,
    limit: int = 50,
    offset: int = 0,
    active: bool = True,
    closed: bool = False,
    order: str = "volume",
    ascending: bool = False,
) -> list[PredictionMarket]:
    """Fetch a page of Polymarket markets."""
    params = {
        "limit": limit,
        "offset": offset,
        "active": str(active).lower(),
        "closed": str(closed).lower(),
        "order": order,
        "ascending": str(ascending).lower(),
    }
    data = _get("/markets", params=params)
    raw_list = data if isinstance(data, list) else data.get("data", data.get("markets", []))
    return [parse_market(r) for r in raw_list]


def fetch_active_markets(
    *,
    min_volume: float = 10_000,
    limit: int = 100,
) -> list[PredictionMarket]:
    """
    Fetch active markets sorted by volume + newest, then filter to tradeable ones.
    Fail-open: returns [] on API error.
    """
    try:
        by_volume = list_markets(limit=limit, active=True, closed=False, order="volume")
        by_newest = list_markets(limit=limit, active=True, closed=False, order="startDate")
        seen: set[str] = set()
        merged: list[PredictionMarket] = []
        for m in by_newest + by_volume:
            if m.id not in seen:
                seen.add(m.id)
                merged.append(m)
        if min_volume > 0:
            merged = [m for m in merged if m.volume >= min_volume]
        logger.info(f"[polymarket_feed] Fetched {len(merged)} markets (min_vol=${min_volume:,.0f})")
        return merged
    except Exception as e:
        logger.error(f"[polymarket_feed] fetch_active_markets failed: {e}")
        return []


def get_market(market_id: str) -> Optional[PredictionMarket]:
    """Fetch a single market by condition ID or slug. Returns None on error."""
    try:
        data = _get(f"/markets/{market_id}")
        return parse_market(data)
    except Exception as e:
        logger.error(f"[polymarket_feed] get_market({market_id}) failed: {e}")
        return None


def get_token_price(token_id: str) -> float:
    """Get the current price (0-1) for a token from the CLOB. Returns 0 on error."""
    try:
        url = CLOB_BASE.rstrip("/") + "/price"
        resp = requests.get(url, params={"token_id": token_id},
                            timeout=_REQUEST_TIMEOUT, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("price", data.get("mid", 0)))
    except Exception as e:
        logger.warning(f"[polymarket_feed] get_token_price({token_id[:16]}…) failed: {e}")
        return 0.0


def get_market_edge(market: PredictionMarket, our_probability: float) -> float:
    """
    Calculate our edge vs the market-implied probability.
    Positive = we think YES is more likely than market implies.
    Edge > 0.03 (3%) is the entry threshold (PM_MIN_EDGE_PCT in config).
    """
    market_prob = market.best_price
    if market_prob <= 0:
        return 0.0
    return our_probability - market_prob
