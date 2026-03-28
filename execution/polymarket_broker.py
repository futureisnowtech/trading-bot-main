"""
execution/polymarket_broker.py — Polymarket CLOB broker.

Paper mode (POLYMARKET_PAPER=true):
  - Fetches real prices from CLOB REST API
  - All orders logged to SQLite trades.db (lane='lane3')
  - No crypto wallet / private key required

Live mode (POLYMARKET_PAPER=false):
  - Requires py-clob-client + POLYMARKET_PRIVATE_KEY, POLYMARKET_API_KEY/SECRET/PASSPHRASE
  - Orders submitted to the Polygon CLOB
  - Server-side matching, instant fills at market price

Adapted from Fully-Autonomous-Polymarket-AI-Trading-Bot/src/connectors/polymarket_clob.py
(sync; our broker interface; SQLite integration; paper/live toggle).
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from typing import Optional

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    PAPER_TRADING, POLYMARKET_PAPER, POLYMARKET_CHAIN_ID,
    POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE,
    POLYMARKET_PRIVATE_KEY, PM_MAX_POSITION_USD,
)
from data.polymarket_feed import (
    PredictionMarket, get_market, get_token_price, fetch_active_markets,
)
from execution.prediction_market_base import (
    BasePredictionMarketBroker, PredictionPosition, OrderResult, MarketSnapshot,
)
from logging_db.trade_logger import log_trade, log_event

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
_REQUEST_TIMEOUT = 20.0

# Fee: Polymarket charges 2% on winning side (deducted at resolution)
# We model this as 1% per trade round-trip for conservatism
POLYMARKET_FEE_PCT = 0.02


class PolymarketBroker(BasePredictionMarketBroker):
    """
    Synchronous Polymarket broker.

    Positions are stored in-memory (dict) and persisted to SQLite.
    Risk manager integration: register_position / close_position
    called on the shared RiskManager instance.
    """

    def __init__(self) -> None:
        self._paper = POLYMARKET_PAPER or PAPER_TRADING
        self._positions: dict[str, PredictionPosition] = {}
        self._clob_client = None   # lazy-loaded for live mode
        log_event('INFO', 'polymarket', f"PolymarketBroker init (paper={self._paper})")

    @property
    def platform_name(self) -> str:
        return "polymarket"

    @property
    def is_paper(self) -> bool:
        return self._paper

    # ── Market discovery ──────────────────────────────────────────────────────

    def get_markets(
        self,
        *,
        min_volume: float = 10_000,
        max_results: int = 50,
    ) -> list[MarketSnapshot]:
        """Fetch and filter active Polymarket markets."""
        try:
            markets = fetch_active_markets(min_volume=min_volume, limit=max_results)
            snapshots: list[MarketSnapshot] = []
            for m in markets:
                snapshots.append(MarketSnapshot(
                    market_id=m.id,
                    question=m.question,
                    platform="polymarket",
                    yes_price=m.best_price,
                    volume_usd=m.volume,
                    liquidity_usd=m.liquidity,
                    days_to_expiry=m.days_to_expiry,
                    spread=m.spread,
                    market_type=m.market_type,
                ))
            return snapshots
        except Exception as e:
            logger.error(f"[polymarket] get_markets failed: {e}")
            return []

    # ── Order placement ───────────────────────────────────────────────────────

    def place_order(
        self,
        market_id: str,
        side: str,
        size_usd: float,
        price: float,
    ) -> OrderResult:
        """Place YES or NO order. Paper: log to SQLite. Live: submit to CLOB."""
        side = side.upper()
        if side not in ("YES", "NO"):
            return OrderResult(success=False, error=f"Invalid side: {side}")
        if size_usd <= 0 or size_usd > PM_MAX_POSITION_USD:
            return OrderResult(success=False, error=f"size_usd {size_usd} out of range")
        if not (0.01 <= price <= 0.99):
            return OrderResult(success=False, error=f"price {price} must be 0.01-0.99")

        shares = size_usd / price  # shares purchased at this price
        fee_usd = size_usd * POLYMARKET_FEE_PCT
        order_id = f"PM_PAPER_{uuid.uuid4().hex[:10]}" if self._paper else ""

        if self._paper:
            order_id = self._paper_order(market_id, side, shares, price, size_usd, fee_usd)
        else:
            order_id, filled_price, error = self._live_order(market_id, side, shares, price)
            if error:
                return OrderResult(success=False, error=error)
            price = filled_price

        # Track position in-memory
        pos_id = order_id
        self._positions[pos_id] = PredictionPosition(
            position_id=pos_id,
            market_id=market_id,
            market_question=self._get_question(market_id),
            platform="polymarket",
            side=side,
            size_shares=shares,
            entry_price=price,
            current_price=price,
            stop_price=max(0.01, price * 0.5),   # exit if price halves
            target_price=min(0.99, price + (1.0 - price) * 0.60),  # exit at 60% of potential gain
            paper=self._paper,
        )

        logger.info(
            f"[polymarket] {'PAPER ' if self._paper else ''}ORDER {side} {market_id[:16]}… "
            f"${size_usd:.2f} @ {price:.3f} → {shares:.2f} shares | id={order_id}"
        )
        return OrderResult(
            success=True, order_id=order_id,
            filled_price=price, filled_shares=shares,
            paper=self._paper,
        )

    def _paper_order(
        self,
        market_id: str,
        side: str,
        shares: float,
        price: float,
        size_usd: float,
        fee_usd: float,
    ) -> str:
        """Log paper order to SQLite, return order_id."""
        order_id = f"PM_PAPER_{uuid.uuid4().hex[:10]}"
        log_trade(
            strategy=f"polymarket_{side.lower()}",
            broker="polymarket_paper",
            symbol=market_id[:24],
            action="BUY",
            order_type="MARKET",
            qty=shares,
            price=price,
            fee_usd=fee_usd,
            pnl_usd=0.0,
            paper=True,
            order_id=order_id,
            notes=f"lane=lane3|pm_side={side}|size_usd={size_usd:.2f}",
        )
        return order_id

    def _live_order(
        self,
        market_id: str,
        side: str,
        shares: float,
        price: float,
    ) -> tuple[str, float, str]:
        """Submit live order via py-clob-client. Returns (order_id, fill_price, error)."""
        try:
            client = self._ensure_clob_client()
            # Determine token_id for YES or NO side
            mkt = get_market(market_id)
            if mkt is None:
                return "", 0.0, f"Market {market_id} not found"
            tokens = {t.outcome.upper(): t.token_id for t in mkt.tokens}
            token_id = tokens.get(side, "")
            if not token_id:
                return "", 0.0, f"No {side} token in market {market_id}"

            from py_clob_client.clob_types import MarketOrderArgs, BUY  # type: ignore
            order_args = MarketOrderArgs(token_id=token_id, amount=shares)
            resp = client.create_and_post_order(order_args)
            order_id = resp.get("orderID", resp.get("id", uuid.uuid4().hex))
            fill_price = float(resp.get("price", price))
            return order_id, fill_price, ""
        except ImportError:
            return "", 0.0, "py-clob-client not installed (pip install py-clob-client)"
        except Exception as e:
            return "", 0.0, str(e)

    # ── Position management ───────────────────────────────────────────────────

    def get_positions(self) -> list[PredictionPosition]:
        return [p for p in self._positions.values() if not p.resolved]

    def close_position(self, position: PredictionPosition, reason: str = "") -> OrderResult:
        """Exit a position at current market price."""
        current = get_token_price(position.market_id) if not self._paper else position.current_price
        pnl = (current - position.entry_price) * position.size_shares
        fee_usd = abs(pnl) * POLYMARKET_FEE_PCT if pnl > 0 else 0.0
        net_pnl = pnl - fee_usd

        if self._paper:
            log_trade(
                strategy=f"polymarket_{position.side.lower()}",
                broker="polymarket_paper",
                symbol=position.market_id[:24],
                action="SELL",
                order_type="MARKET",
                qty=position.size_shares,
                price=current,
                fee_usd=fee_usd,
                pnl_usd=net_pnl,
                paper=True,
                order_id=position.position_id,
                notes=f"lane=lane3|reason={reason}|entry={position.entry_price:.4f}",
            )

        position.resolved = True
        position.current_price = current
        logger.info(
            f"[polymarket] CLOSE {position.side} {position.market_id[:16]}… "
            f"P&L=${net_pnl:+.2f} | reason={reason}"
        )
        return OrderResult(success=True, order_id=position.position_id,
                           filled_price=current, filled_shares=position.size_shares,
                           paper=self._paper)

    def check_resolution(self, position: PredictionPosition) -> PredictionPosition:
        """Check if market has resolved. Update position outcome if settled."""
        try:
            mkt = get_market(position.market_id)
            if mkt is None:
                return position
            if mkt.closed:
                # Determine outcome from token winner flags
                for t in mkt.tokens:
                    if t.outcome.upper() == position.side and t.winner is True:
                        position.outcome = True   # we won
                        position.resolved = True
                        position.current_price = 1.0
                    elif t.outcome.upper() == position.side and t.winner is False:
                        position.outcome = False  # we lost
                        position.resolved = True
                        position.current_price = 0.0
            else:
                # Update mark-to-market price
                for t in mkt.tokens:
                    if t.outcome.upper() == position.side:
                        position.current_price = t.price
        except Exception as e:
            logger.warning(f"[polymarket] check_resolution({position.market_id[:16]}…): {e}")
        return position

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _ensure_clob_client(self):
        if self._clob_client is not None:
            return self._clob_client
        try:
            from py_clob_client.client import ClobClient  # type: ignore
        except ImportError:
            raise RuntimeError("pip install py-clob-client")

        if not all([POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE, POLYMARKET_PRIVATE_KEY]):
            raise RuntimeError(
                "Live Polymarket requires POLYMARKET_API_KEY, POLYMARKET_API_SECRET, "
                "POLYMARKET_API_PASSPHRASE, POLYMARKET_PRIVATE_KEY in .env"
            )
        self._clob_client = ClobClient(
            host=CLOB_BASE,
            key=POLYMARKET_PRIVATE_KEY,
            chain_id=POLYMARKET_CHAIN_ID,
            creds={
                "apiKey": POLYMARKET_API_KEY,
                "secret": POLYMARKET_API_SECRET,
                "passphrase": POLYMARKET_API_PASSPHRASE,
            },
        )
        return self._clob_client

    def _get_question(self, market_id: str) -> str:
        try:
            mkt = get_market(market_id)
            return mkt.question if mkt else market_id
        except Exception:
            return market_id


# ── Singleton ─────────────────────────────────────────────────────────────────

_broker: Optional[PolymarketBroker] = None


def get_polymarket_broker() -> PolymarketBroker:
    global _broker
    if _broker is None:
        _broker = PolymarketBroker()
    return _broker
