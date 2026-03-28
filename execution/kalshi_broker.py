"""
execution/kalshi_broker.py — Kalshi prediction market broker.

Kalshi is CFTC-regulated. Contracts settle at $1 (YES wins) or $0 (NO wins).
You buy YES at e.g. $0.60 → win $1.00 if correct, lose $0.60 if wrong.

Paper mode (KALSHI_PAPER=true):
  - Uses Kalshi demo environment (demo-api.kalshi.co)
  - Orders logged to SQLite only
  - No real account needed

Live mode (KALSHI_PAPER=false):
  - Uses production API (trading-api.kalshi.co)
  - Requires KALSHI_API_KEY + KALSHI_API_SECRET in .env

Built from: Kalshi REST API docs + Polymarket broker as structural template.
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
    PAPER_TRADING, KALSHI_PAPER, KALSHI_API_KEY, KALSHI_API_SECRET,
    PM_MAX_POSITION_USD,
)
from data.kalshi_feed import (
    KalshiMarket, KalshiClient, fetch_active_kalshi_markets,
    KALSHI_DEMO_BASE, KALSHI_LIVE_BASE,
)
from execution.prediction_market_base import (
    BasePredictionMarketBroker, PredictionPosition, OrderResult, MarketSnapshot,
)
from logging_db.trade_logger import log_trade, log_event

logger = logging.getLogger(__name__)

# Kalshi charges a 7 cent fee per contract on the losing side
# Model as ~1% round-trip for paper mode P&L accuracy
KALSHI_FEE_PCT = 0.01


class KalshiBroker(BasePredictionMarketBroker):
    """
    Synchronous Kalshi broker.
    Implements BasePredictionMarketBroker so it's interchangeable with PolymarketBroker.
    """

    def __init__(self) -> None:
        self._paper = KALSHI_PAPER or PAPER_TRADING
        self._client = KalshiClient(
            paper=self._paper,
            api_key=KALSHI_API_KEY,
            api_secret=KALSHI_API_SECRET,
        )
        self._positions: dict[str, PredictionPosition] = {}
        self._token: Optional[str] = None
        log_event('INFO', 'kalshi', f"KalshiBroker init (paper={self._paper})")

    @property
    def platform_name(self) -> str:
        return "kalshi"

    @property
    def is_paper(self) -> bool:
        return self._paper

    # ── Market discovery ──────────────────────────────────────────────────────

    def get_markets(
        self,
        *,
        min_volume: float = 500,
        max_results: int = 50,
    ) -> list[MarketSnapshot]:
        try:
            markets = fetch_active_kalshi_markets(
                paper=self._paper,
                min_volume=min_volume,
                api_key=KALSHI_API_KEY,
                api_secret=KALSHI_API_SECRET,
            )
            snapshots: list[MarketSnapshot] = []
            for m in markets[:max_results]:
                snapshots.append(MarketSnapshot(
                    market_id=m.ticker,
                    question=m.title,
                    platform="kalshi",
                    yes_price=m.yes_price,
                    volume_usd=m.volume,        # Kalshi volume is in contracts (~$1 each)
                    liquidity_usd=m.open_interest,
                    days_to_expiry=m.days_to_expiry,
                    spread=m.spread_cents / 100.0,
                    market_type=m.market_type,
                ))
            return snapshots
        except Exception as e:
            logger.error(f"[kalshi] get_markets failed: {e}")
            return []

    # ── Order placement ───────────────────────────────────────────────────────

    def place_order(
        self,
        market_id: str,
        side: str,
        size_usd: float,
        price: float,
    ) -> OrderResult:
        """Buy YES or NO contracts. Each contract costs price cents and pays $1 if correct."""
        side = side.upper()
        if side not in ("YES", "NO"):
            return OrderResult(success=False, error=f"Invalid side: {side}")
        if size_usd <= 0 or size_usd > PM_MAX_POSITION_USD:
            return OrderResult(success=False, error=f"size_usd {size_usd} out of range")
        if not (0.01 <= price <= 0.99):
            return OrderResult(success=False, error=f"price {price} must be 0.01-0.99")

        # Each share costs `price` dollars and pays $1 at resolution
        shares = size_usd / price
        fee_usd = size_usd * KALSHI_FEE_PCT
        order_id = f"KX_PAPER_{uuid.uuid4().hex[:10]}" if self._paper else ""

        if self._paper:
            order_id = self._paper_order(market_id, side, shares, price, size_usd, fee_usd)
        else:
            order_id, filled_price, error = self._live_order(market_id, side, shares, price)
            if error:
                return OrderResult(success=False, error=error)
            price = filled_price

        pos_id = order_id
        self._positions[pos_id] = PredictionPosition(
            position_id=pos_id,
            market_id=market_id,
            market_question=self._get_question(market_id),
            platform="kalshi",
            side=side,
            size_shares=shares,
            entry_price=price,
            current_price=price,
            stop_price=max(0.01, price * 0.4),
            target_price=min(0.99, price + (1.0 - price) * 0.60),
            paper=self._paper,
        )

        logger.info(
            f"[kalshi] {'PAPER ' if self._paper else ''}ORDER {side} {market_id} "
            f"${size_usd:.2f} @ {price:.3f} → {shares:.2f} contracts | id={order_id}"
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
        order_id = f"KX_PAPER_{uuid.uuid4().hex[:10]}"
        log_trade(
            strategy=f"kalshi_{side.lower()}",
            broker="kalshi_paper",
            symbol=market_id[:24],
            action="BUY",
            order_type="LIMIT",
            qty=shares,
            price=price,
            fee_usd=fee_usd,
            pnl_usd=0.0,
            paper=True,
            order_id=order_id,
            notes=f"lane=lane3|kx_side={side}|size_usd={size_usd:.2f}",
        )
        return order_id

    def _live_order(
        self,
        market_id: str,
        side: str,
        shares: float,
        price: float,
    ) -> tuple[str, float, str]:
        """Submit live order to Kalshi API."""
        try:
            if not self._token:
                if not self._client._login():
                    return "", 0.0, "Kalshi authentication failed"
                self._token = self._client._token

            base = KALSHI_DEMO_BASE if self._paper else KALSHI_LIVE_BASE
            payload = {
                "ticker": market_id,
                "client_order_id": uuid.uuid4().hex,
                "type": "limit",
                "action": "buy",
                "side": side.lower(),
                "count": int(shares),
                "yes_price": int(price * 100),  # Kalshi uses cents (1-99)
            }
            resp = requests.post(
                f"{base}/portfolio/orders",
                json=payload,
                headers={"Authorization": f"Bearer {self._token}",
                         "Content-Type": "application/json"},
                timeout=20.0,
            )
            resp.raise_for_status()
            data = resp.json()
            order = data.get("order", data)
            order_id = order.get("order_id", uuid.uuid4().hex)
            fill_price = float(order.get("yes_price", price * 100)) / 100.0
            return order_id, fill_price, ""
        except Exception as e:
            return "", 0.0, str(e)

    # ── Position management ───────────────────────────────────────────────────

    def get_positions(self) -> list[PredictionPosition]:
        return [p for p in self._positions.values() if not p.resolved]

    def close_position(self, position: PredictionPosition, reason: str = "") -> OrderResult:
        """Exit before resolution by selling back."""
        current = self._client.get_market_price(position.market_id)
        if current <= 0:
            current = position.current_price
        pnl = (current - position.entry_price) * position.size_shares
        fee_usd = abs(pnl) * KALSHI_FEE_PCT if pnl > 0 else 0.0
        net_pnl = pnl - fee_usd

        log_trade(
            strategy=f"kalshi_{position.side.lower()}",
            broker="kalshi_paper" if self._paper else "kalshi_live",
            symbol=position.market_id[:24],
            action="SELL",
            order_type="MARKET",
            qty=position.size_shares,
            price=current,
            fee_usd=fee_usd,
            pnl_usd=net_pnl,
            paper=self._paper,
            order_id=position.position_id,
            notes=f"lane=lane3|reason={reason}|entry={position.entry_price:.4f}",
        )
        position.resolved = True
        position.current_price = current
        logger.info(f"[kalshi] CLOSE {position.side} {position.market_id} P&L=${net_pnl:+.2f} | reason={reason}")
        return OrderResult(success=True, order_id=position.position_id,
                           filled_price=current, filled_shares=position.size_shares,
                           paper=self._paper)

    def check_resolution(self, position: PredictionPosition) -> PredictionPosition:
        """Check if market resolved. Log final P&L if so."""
        try:
            m = self._client.get_market(position.market_id)
            if m is None:
                return position
            if m.status in ("finalized", "closed") and m.result:
                won = (m.result.lower() == "yes" and position.side == "YES") or \
                      (m.result.lower() == "no"  and position.side == "NO")
                position.outcome = won
                position.resolved = True
                position.current_price = 1.0 if won else 0.0

                pnl = (position.current_price - position.entry_price) * position.size_shares
                fee_usd = KALSHI_FEE_PCT * position.size_shares if not won else 0.0
                net_pnl = pnl - fee_usd

                log_trade(
                    strategy=f"kalshi_{position.side.lower()}",
                    broker="kalshi_paper" if self._paper else "kalshi_live",
                    symbol=position.market_id[:24],
                    action="SELL",
                    order_type="SETTLEMENT",
                    qty=position.size_shares,
                    price=position.current_price,
                    fee_usd=fee_usd,
                    pnl_usd=net_pnl,
                    paper=self._paper,
                    order_id=position.position_id,
                    notes=f"lane=lane3|resolved=True|won={won}|result={m.result}",
                )
                result_str = "WON" if won else "LOST"
                logger.info(f"[kalshi] SETTLED {position.side} {position.market_id} → {result_str} P&L=${net_pnl:+.2f}")
            else:
                position.current_price = m.yes_price if position.side == "YES" else 1.0 - m.yes_price
        except Exception as e:
            logger.warning(f"[kalshi] check_resolution({position.market_id}): {e}")
        return position

    def _get_question(self, ticker: str) -> str:
        try:
            m = self._client.get_market(ticker)
            return m.title if m else ticker
        except Exception:
            return ticker


# ── Singleton ─────────────────────────────────────────────────────────────────

_broker: Optional[KalshiBroker] = None


def get_kalshi_broker() -> KalshiBroker:
    global _broker
    if _broker is None:
        _broker = KalshiBroker()
    return _broker
