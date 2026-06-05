"""Execution control layer for Kalshi weather entries.

Strategy produces desired size. This layer converts that desire into an
executable order plan using live depth, buying power, and venue pacing.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from config import KALSHI_FEE_PER_CONTRACT


@dataclass(frozen=True)
class TradeIntent:
    contract: dict
    result: Any
    bankroll: float
    buying_power_usd: float
    market_snapshot: Any | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    intent: TradeIntent
    ticker: str
    right: str
    side: str
    order_type: str
    limit_price: float
    requested_qty: int
    visible_qty: int
    affordable_qty: int
    executable_qty: int
    quote: dict
    status: str
    reason: str = ""
    depth_capped: bool = False


class KalshiExecutionController:
    """Turns strategy candidates into venue-realistic entry attempts."""

    def __init__(
        self,
        broker,
        *,
        min_order_interval_sec: float = 0.35,
        rate_limit_cooldown_sec: float = 15.0,
    ) -> None:
        self._broker = broker
        self._min_order_interval_sec = max(0.0, float(min_order_interval_sec))
        self._rate_limit_cooldown_sec = max(1.0, float(rate_limit_cooldown_sec))
        self._next_order_at = 0.0
        self._rate_limited_until = 0.0

    @staticmethod
    def _ask_fields_for_right(right: str) -> tuple[str, str]:
        if str(right).upper() == "P":
            return "no_ask", "no_ask_size"
        return "yes_ask", "yes_ask_size"

    @staticmethod
    def _bid_fields_for_right(right: str) -> tuple[str, str]:
        if str(right).upper() == "P":
            return "no_bid", "no_bid_size"
        return "yes_bid", "yes_bid_size"

    @staticmethod
    def _floor_qty(value: Any) -> int:
        try:
            return max(0, int(math.floor(float(value))))
        except (TypeError, ValueError):
            return 0

    def _visible_ask_depth(self, right: str, quote: dict) -> tuple[float, int]:
        ask_key, ask_size_key = self._ask_fields_for_right(right)
        ask = float(quote.get(ask_key) or 0.0)
        ask_size = quote.get(ask_size_key)
        if ask_size in (None, ""):
            ask_size = quote.get(ask_size_key.replace("_size", "_vol"))
        return ask, self._floor_qty(ask_size)

    def _max_affordable_qty(self, price: float, buying_power_usd: float) -> int:
        cash_per_contract = float(price) + float(KALSHI_FEE_PER_CONTRACT)
        if cash_per_contract <= 0:
            return 0
        return self._floor_qty(float(buying_power_usd) / cash_per_contract)

    def plan_entry(self, intent: TradeIntent) -> ExecutionPlan:
        contract = intent.contract
        result = intent.result
        ticker = str(contract.get("local_symbol") or "")
        right = str(contract.get("right") or "C").upper()
        requested_qty = max(0, int(getattr(result, "position_contracts", 0) or 0))
        order_type = "market" if bool(getattr(result, "is_taker_override", False)) else "limit"

        if requested_qty <= 0:
            return ExecutionPlan(
                intent=intent,
                ticker=ticker,
                right=right,
                side=str(getattr(result, "side", "YES") or "YES"),
                order_type=order_type,
                limit_price=0.0,
                requested_qty=requested_qty,
                visible_qty=0,
                affordable_qty=0,
                executable_qty=0,
                quote={},
                status="blocked",
                reason="sizing_zero",
            )

        quote = self._broker.get_quote(ticker) or {}
        ask_price, visible_qty = self._visible_ask_depth(right, quote)
        affordable_qty = self._max_affordable_qty(ask_price, intent.buying_power_usd)
        executable_qty = min(requested_qty, visible_qty, affordable_qty)

        if ask_price <= 0:
            return ExecutionPlan(
                intent=intent,
                ticker=ticker,
                right=right,
                side=str(getattr(result, "side", "YES") or "YES"),
                order_type=order_type,
                limit_price=0.0,
                requested_qty=requested_qty,
                visible_qty=visible_qty,
                affordable_qty=affordable_qty,
                executable_qty=0,
                quote=quote,
                status="blocked",
                reason="missing_live_ask",
            )

        if visible_qty <= 0:
            return ExecutionPlan(
                intent=intent,
                ticker=ticker,
                right=right,
                side=str(getattr(result, "side", "YES") or "YES"),
                order_type=order_type,
                limit_price=ask_price,
                requested_qty=requested_qty,
                visible_qty=visible_qty,
                affordable_qty=affordable_qty,
                executable_qty=0,
                quote=quote,
                status="blocked",
                reason="insufficient_resting_volume",
            )

        if affordable_qty <= 0:
            return ExecutionPlan(
                intent=intent,
                ticker=ticker,
                right=right,
                side=str(getattr(result, "side", "YES") or "YES"),
                order_type=order_type,
                limit_price=ask_price,
                requested_qty=requested_qty,
                visible_qty=visible_qty,
                affordable_qty=affordable_qty,
                executable_qty=0,
                quote=quote,
                status="blocked",
                reason="insufficient_buying_power",
            )

        return ExecutionPlan(
            intent=intent,
            ticker=ticker,
            right=right,
            side=str(getattr(result, "side", "YES") or "YES"),
            order_type=order_type,
            limit_price=ask_price,
            requested_qty=requested_qty,
            visible_qty=visible_qty,
            affordable_qty=affordable_qty,
            executable_qty=executable_qty,
            quote=quote,
            status="ready",
            reason=(
                f"depth_capped:{requested_qty}->{executable_qty}"
                if executable_qty < requested_qty
                else ""
            ),
            depth_capped=executable_qty < requested_qty,
        )

    def _respect_local_pacing(self) -> None:
        now = time.time()
        if now < self._next_order_at:
            time.sleep(self._next_order_at - now)

    def _retry_after_depth_loss(self, plan: ExecutionPlan, forecast_yes_prob: float) -> dict:
        refreshed_quote = self._broker.get_quote(plan.ticker) or {}
        ask_price, visible_qty = self._visible_ask_depth(plan.right, refreshed_quote)
        retry_qty = min(plan.executable_qty - 1, visible_qty)
        if retry_qty <= 0 or ask_price <= 0:
            return {
                "order_id": "ERR",
                "status": "fill_or_kill_insufficient_resting_volume",
                "qty": 0,
                "execution_reason": "depth_vanished_before_fill",
            }

        retry_result = self._broker.place_buy_order(
            contract_dict={
                "local_symbol": plan.ticker,
                "right": plan.right,
                "strike": plan.intent.contract.get("strike", 0.0),
                "last_trade_at": plan.intent.contract.get("last_trade_at", ""),
            },
            qty=retry_qty,
            limit_price=ask_price,
            type=plan.order_type,
            reason=f"{getattr(plan.intent.result, 'strategy_family', 'forecast')}_retry_depth",
            strategy=f"forecast_{getattr(plan.intent.result, 'strategy_family', 'weather_ensemble')}",
            forecast_yes_prob=forecast_yes_prob,
        )
        retry_result["qty"] = retry_result.get("qty") or retry_qty
        retry_result["requested_qty"] = plan.requested_qty
        retry_result["visible_qty"] = visible_qty
        retry_result["affordable_qty"] = plan.affordable_qty
        retry_result["depth_capped"] = True
        retry_result["execution_reason"] = "retried_smaller_after_depth_loss"
        retry_result["live_quote"] = refreshed_quote
        return retry_result

    def execute_plan(self, plan: ExecutionPlan, *, forecast_yes_prob: float) -> dict:
        now = time.time()
        if now < self._rate_limited_until:
            return {
                "order_id": "ERR",
                "status": "rate_limit_cooldown",
                "qty": 0,
                "execution_reason": "local_rate_limit_cooldown",
            }

        if plan.status != "ready" or plan.executable_qty <= 0:
            return {
                "order_id": "ERR",
                "status": plan.status,
                "qty": 0,
                "execution_reason": plan.reason or "not_executable",
            }

        self._respect_local_pacing()
        result = self._broker.place_buy_order(
            contract_dict={
                "local_symbol": plan.ticker,
                "right": plan.right,
                "strike": plan.intent.contract.get("strike", 0.0),
                "last_trade_at": plan.intent.contract.get("last_trade_at", ""),
            },
            qty=plan.executable_qty,
            limit_price=plan.limit_price,
            type=plan.order_type,
            reason=f"{getattr(plan.intent.result, 'strategy_family', 'forecast')}_ev={getattr(plan.intent.result, 'ev', 0.0):.4f}_depth={plan.visible_qty}",
            strategy=f"forecast_{getattr(plan.intent.result, 'strategy_family', 'weather_ensemble')}",
            forecast_yes_prob=forecast_yes_prob,
        )
        self._next_order_at = time.time() + self._min_order_interval_sec

        status = str(result.get("status") or "")
        result["qty"] = result.get("qty") or plan.executable_qty
        result["requested_qty"] = plan.requested_qty
        result["visible_qty"] = plan.visible_qty
        result["affordable_qty"] = plan.affordable_qty
        result["depth_capped"] = plan.depth_capped
        result["execution_reason"] = plan.reason or "submitted"

        if status == "too_many_requests":
            self._rate_limited_until = time.time() + self._rate_limit_cooldown_sec
            return result

        if status == "fill_or_kill_insufficient_resting_volume" and plan.executable_qty > 1:
            return self._retry_after_depth_loss(plan, forecast_yes_prob)

        return result
