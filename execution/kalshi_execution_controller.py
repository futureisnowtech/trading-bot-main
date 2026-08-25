"""Execution control layer for Kalshi weather entries.

Strategy produces desired size. This layer converts that desire into an
executable order plan using live depth, buying power, and venue pacing.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import config
from config import (
    estimate_kalshi_fee_per_contract,
    get_kalshi_position_cap_usd,
    max_kalshi_contracts_for_budget,
)


@dataclass(frozen=True)
class TradeIntent:
    contract: dict
    result: Any
    bankroll: float
    buying_power_usd: float
    market_snapshot: Any | None = None
    max_capital_usd: float | None = None


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
        return max_kalshi_contracts_for_budget(price, buying_power_usd)

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
        side = str(getattr(result, "side", "YES") or "YES").upper()
        evaluated_ask = float(
            getattr(result, "ask_no" if side == "NO" else "ask_yes", 0.0) or 0.0
        )
        max_slippage = max(0.0, float(config.KALSHI_MAX_ENTRY_SLIPPAGE))
        if evaluated_ask > 0.0 and ask_price > evaluated_ask + max_slippage + 1e-9:
            return ExecutionPlan(
                intent=intent, ticker=ticker, right=right, side=side,
                order_type=order_type, limit_price=ask_price,
                requested_qty=requested_qty, visible_qty=visible_qty,
                affordable_qty=0, executable_qty=0, quote=quote,
                status="blocked",
                reason=(
                    f"live_slippage_veto ({evaluated_ask:.4f}->{ask_price:.4f} "
                    f"> {max_slippage:.4f})"
                ),
            )

        bid_key, _bid_size_key = self._bid_fields_for_right(right)
        bid_price = float(quote.get(bid_key) or 0.0)
        if bid_price <= 0.0 or ask_price - bid_price > float(config.KALSHI_MAX_SPREAD_DOLLARS):
            return ExecutionPlan(
                intent=intent, ticker=ticker, right=right, side=side,
                order_type=order_type, limit_price=ask_price,
                requested_qty=requested_qty, visible_qty=visible_qty,
                affordable_qty=0, executable_qty=0, quote=quote,
                status="blocked", reason="live_quote_coherence_veto",
            )

        held_probability = max(0.0, min(1.0, float(getattr(result, "confidence", 0.0) or 0.0)))
        entry_fee = estimate_kalshi_fee_per_contract(ask_price, rounded=True)
        exit_fee = estimate_kalshi_fee_per_contract(ask_price, rounded=True)
        live_net_ev = held_probability - ask_price - entry_fee - (0.48 * exit_fee)
        try:
            from forecast.strategy_engine import EV_THRESHOLD
            ev_floor = float(EV_THRESHOLD)
        except Exception:
            ev_floor = 0.12
        if live_net_ev < ev_floor:
            return ExecutionPlan(
                intent=intent, ticker=ticker, right=right, side=side,
                order_type=order_type, limit_price=ask_price,
                requested_qty=requested_qty, visible_qty=visible_qty,
                affordable_qty=0, executable_qty=0, quote=quote,
                status="blocked",
                reason=f"live_fee_adjusted_ev_veto ({live_net_ev:.4f} < {ev_floor:.4f})",
            )

        weather_mode = str(getattr(result, "weather_mode", "") or "").upper()
        yes_ask = float(quote.get("yes_ask") or 0.0)
        if weather_mode in {"HIGH", "LOW"} and yes_ask > 0.0:
            if not (
                float(config.KALSHI_DAILY_ASK_YES_BRACKET_MIN)
                <= yes_ask
                <= float(config.KALSHI_DAILY_ASK_YES_BRACKET_MAX)
            ):
                return ExecutionPlan(
                    intent=intent, ticker=ticker, right=right, side=side,
                    order_type=order_type, limit_price=ask_price,
                    requested_qty=requested_qty, visible_qty=visible_qty,
                    affordable_qty=0, executable_qty=0, quote=quote,
                    status="blocked", reason="live_price_bracket_veto",
                )
        if ask_price < float(config.KALSHI_MIN_ENTRY_PRICE):
            return ExecutionPlan(
                intent=intent, ticker=ticker, right=right, side=side,
                order_type=order_type, limit_price=ask_price,
                requested_qty=requested_qty, visible_qty=visible_qty,
                affordable_qty=0, executable_qty=0, quote=quote,
                status="blocked", reason="live_min_entry_price_veto",
            )

        default_capital_cap = min(
            get_kalshi_position_cap_usd(held_probability),
            max(0.0, float(intent.bankroll)) * float(config.KALSHI_KELLY_CAP),
        )
        capital_cap = min(
            max(0.0, float(intent.buying_power_usd)),
            default_capital_cap,
            max(0.0, float(intent.max_capital_usd))
            if intent.max_capital_usd is not None
            else default_capital_cap,
        )
        affordable_qty = self._max_affordable_qty(ask_price, capital_cap)
        executable_qty = min(requested_qty, visible_qty, affordable_qty)

        if ask_price <= 0:
            return ExecutionPlan(
                intent=intent,
                ticker=ticker,
                right=right,
                side=side,
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
                side=side,
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
                side=side,
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
            side=side,
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

    def _submission_gate_block(self, plan: ExecutionPlan) -> dict | None:
        """Recheck every mutable entry permission at a broker-write boundary."""
        # Lightweight mathematical test brokers intentionally omit the live
        # snapshot interface. Real Kalshi brokers always expose it.
        if not hasattr(self._broker, "position_snapshot_status"):
            return None
        if not self._broker.has_fresh_position_snapshot():
            return {
                "order_id": "ERR", "status": "position_snapshot_unavailable",
                "qty": 0, "filled_qty": 0,
                "execution_reason": str(self._broker.position_snapshot_status()),
            }
        try:
            from forecast.firewall import check_entry_firewall

            allowed, reason = check_entry_firewall(plan.ticker, plan.intent.bankroll)
            if not allowed:
                return {
                    "order_id": "ERR", "status": "firewall_blocked",
                    "qty": 0, "filled_qty": 0, "execution_reason": reason,
                }
        except Exception as exc:
            return {
                "order_id": "ERR", "status": "firewall_state_unavailable",
                "qty": 0, "filled_qty": 0, "execution_reason": str(exc),
            }

        try:
            from runtime.operator_truth import get_release_status

            release = get_release_status()
            if not bool(release.get("entries_allowed")):
                return {
                    "order_id": "ERR", "status": "release_gate_blocked",
                    "qty": 0, "filled_qty": 0,
                    "execution_reason": str(
                        release.get("current_release_verdict")
                        or "release_not_promoted"
                    ),
                }
        except Exception as exc:
            return {
                "order_id": "ERR", "status": "release_gate_unavailable",
                "qty": 0, "filled_qty": 0, "execution_reason": str(exc),
            }
        return None

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        forecast_yes_prob: float,
        model_prob_gfs: float | None = None,
        model_prob_ecmwf: float | None = None,
        weather_mode: str | None = None,
        forecast_hours_to_resolution: float | None = None,
    ) -> dict:
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

        gate_block = self._submission_gate_block(plan)
        if gate_block is not None:
            return gate_block

        # The candidate may have waited for evidence persistence, release checks,
        # or local pacing. Re-fetch depth and economics at the final boundary so
        # the POST cannot use a stale evaluated ask or stale visible quantity.
        refreshed_plan = self.plan_entry(plan.intent)
        if refreshed_plan.status != "ready" or refreshed_plan.executable_qty <= 0:
            return {
                "order_id": "ERR",
                "status": refreshed_plan.status,
                "qty": 0,
                "filled_qty": 0,
                "execution_reason": refreshed_plan.reason or "final_replan_blocked",
            }
        plan = refreshed_plan

        # Quote refresh is a network boundary. Permissions may change while it
        # is in flight, so a prior passing verdict cannot authorize the POST.
        gate_block = self._submission_gate_block(plan)
        if gate_block is not None:
            return gate_block

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
            strategy=f"forecast_{getattr(plan.intent.result, 'strategy_family', 'weather_physics')}",
            forecast_yes_prob=forecast_yes_prob,
            model_prob_gfs=model_prob_gfs,
            model_prob_ecmwf=model_prob_ecmwf,
            weather_mode=weather_mode,
            forecast_hours_to_resolution=forecast_hours_to_resolution,
        )
        self._next_order_at = time.time() + self._min_order_interval_sec

        status = str(result.get("status") or "")
        result["qty"] = float(result.get("filled_qty") or result.get("qty") or 0.0)

        result["requested_qty"] = plan.requested_qty
        result["visible_qty"] = plan.visible_qty
        result["affordable_qty"] = plan.affordable_qty
        result["depth_capped"] = plan.depth_capped
        result["execution_reason"] = plan.reason or "submitted"

        if status == "too_many_requests":
            self._rate_limited_until = time.time() + self._rate_limit_cooldown_sec
            return result

        if status == "fill_or_kill_insufficient_resting_volume":
            # Do not chase the same market with a second immediate POST. In live
            # Kalshi conditions that follow-up write is what most often converts
            # a depth slip into a hard 429 throttle.
            result["execution_reason"] = "depth_slipped_after_submission"
            self._next_order_at = time.time() + max(self._min_order_interval_sec, 1.0)
            return result

        return result
