"""
runtime/spot_execution_policy.py — practical maker-first / taker-fallback policy.
"""

from __future__ import annotations

from config import SPOT_MAKER_POLL_SECONDS, SPOT_MAKER_WAIT_SECONDS


def maker_poll_count() -> int:
    polls = max(1, int(SPOT_MAKER_WAIT_SECONDS // max(1, SPOT_MAKER_POLL_SECONDS)))
    return max(1, polls)


def limit_buy_price(best_bid: float, best_ask: float) -> float:
    if best_bid > 0:
        return best_bid
    return best_ask


def limit_sell_price(best_bid: float, best_ask: float) -> float:
    if best_ask > 0:
        return best_ask
    return best_bid
