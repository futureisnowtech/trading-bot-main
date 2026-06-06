"""Canonical market snapshots for the Kalshi forecast lane.

The persistence schema stores separate YES/NO contract rows for a single Kalshi
market. Runtime evaluation should operate on the market as one object, then
route the chosen side to the appropriate contract row only at execution time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from forecast.weather_contracts import weather_mode_for_ticker


@dataclass(frozen=True)
class MarketSnapshot:
    market_id: int
    ticker: str
    contract_name: str
    strike: float
    last_trade_at: str
    resolution_at: str
    yes_contract: dict
    no_contract: dict
    yes_quote: dict
    no_quote: dict
    bars_5m: list[dict]
    bars_30m: list[dict]
    bars_1h: list[dict]
    bars_4h: list[dict]

    @property
    def family(self) -> str:
        return self.ticker.split("-")[0] if self.ticker else ""

    @property
    def pair_key(self) -> tuple[int, float, str, str]:
        return (
            int(self.market_id or 0),
            float(self.strike or 0.0),
            str(self.last_trade_at or ""),
            str(self.ticker or ""),
        )


def snapshot_pair_key(contract: dict) -> tuple[int, float, str, str]:
    return (
        int(contract.get("market_id") or contract.get("id") or 0),
        float(contract.get("strike") or 0.0),
        str(contract.get("last_trade_at") or ""),
        str(contract.get("local_symbol") or ""),
    )


def _snapshot_requires_bars(ticker: str) -> bool:
    return weather_mode_for_ticker(str(ticker or "")) is None


def build_market_snapshots(
    active_contracts: Iterable[dict],
    *,
    get_bars_fn: Callable[[int, str], list[dict]],
    get_quotes_fn: Callable[[int, float, str], dict],
) -> list[MarketSnapshot]:
    grouped: dict[tuple[int, float, str, str], dict[str, dict]] = {}

    for contract in active_contracts or []:
        key = snapshot_pair_key(contract)
        slot = grouped.setdefault(key, {})
        right = str(contract.get("right") or "").upper()
        if right == "C":
            slot["yes"] = contract
        elif right == "P":
            slot["no"] = contract

    snapshots: list[MarketSnapshot] = []
    for key, slot in grouped.items():
        yes_contract = slot.get("yes")
        no_contract = slot.get("no")
        if not yes_contract or not no_contract:
            continue

        market_id, strike, last_trade_at, ticker = key
        try:
            pair = get_quotes_fn(market_id, strike, last_trade_at) or {}
        except Exception:
            continue

        yes_quote = pair.get("yes_quote") or {}
        no_quote = pair.get("no_quote") or {}

        bars_5m: list[dict] = []
        bars_30m: list[dict] = []
        bars_1h: list[dict] = []
        bars_4h: list[dict] = []
        yes_id = yes_contract.get("id") or yes_contract.get("contract_id")
        if yes_id and _snapshot_requires_bars(ticker):
            try:
                bars_5m = get_bars_fn(int(yes_id), "5m")
                bars_30m = get_bars_fn(int(yes_id), "30m")
                bars_1h = get_bars_fn(int(yes_id), "1h")
                bars_4h = get_bars_fn(int(yes_id), "4h")
            except Exception:
                bars_5m, bars_30m, bars_1h, bars_4h = [], [], [], []

        snapshots.append(
            MarketSnapshot(
                market_id=market_id,
                ticker=ticker,
                contract_name=str(
                    yes_contract.get("contract_name")
                    or no_contract.get("contract_name")
                    or ticker
                ),
                strike=strike,
                last_trade_at=last_trade_at,
                resolution_at=str(
                    yes_contract.get("resolution_at")
                    or no_contract.get("resolution_at")
                    or ""
                ),
                yes_contract=yes_contract,
                no_contract=no_contract,
                yes_quote=yes_quote,
                no_quote=no_quote,
                bars_5m=bars_5m,
                bars_30m=bars_30m,
                bars_1h=bars_1h,
                bars_4h=bars_4h,
            )
        )

    snapshots.sort(key=lambda item: (item.resolution_at or item.last_trade_at, item.ticker))
    return snapshots
