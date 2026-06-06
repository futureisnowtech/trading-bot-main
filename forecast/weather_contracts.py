from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Literal


Comparator = Literal["gt", "lt", "between"]
WeatherMode = Literal["HIGH", "LOW", "RAIN", "SNOW", "WIND", "TEMP"]


@dataclass(frozen=True)
class WeatherContractSemantics:
    ticker: str
    mode: WeatherMode
    comparator: Comparator
    source: str
    contract_name: str = ""
    display_low: float | None = None
    display_high: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    threshold: float | None = None
    ambiguous: bool = False


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").replace("*", " ")).strip()


def weather_mode_for_ticker(ticker: str) -> WeatherMode | None:
    symbol = (ticker or "").upper()
    if "HIGH" in symbol:
        return "HIGH"
    if "LOW" in symbol:
        return "LOW"
    if "RAIN" in symbol:
        return "RAIN"
    if "SNOW" in symbol:
        return "SNOW"
    if "WIND" in symbol:
        return "WIND"
    if symbol.startswith("KX"):
        return "TEMP"
    return None


def _contract_half_step(mode: WeatherMode) -> float:
    return 0.5 if mode in {"HIGH", "LOW", "TEMP"} else 0.0


def _display_range_to_bounds(low: float, high: float, mode: WeatherMode) -> tuple[float, float]:
    half_step = _contract_half_step(mode)
    return float(low) - half_step, float(high) + half_step


def _title_semantics(
    ticker: str,
    contract_name: str,
    mode: WeatherMode,
) -> WeatherContractSemantics | None:
    title = _clean_title(contract_name)
    if not title:
        return None

    m = re.search(
        r"\bbe\s+(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)\s*(?:°|degrees|deg|inches|inch|in|mph)?",
        title,
        flags=re.IGNORECASE,
    )
    if m:
        low = float(m.group(1))
        high = float(m.group(2))
        lo, hi = sorted((low, high))
        lower_bound, upper_bound = _display_range_to_bounds(lo, hi, mode)
        return WeatherContractSemantics(
            ticker=ticker,
            mode=mode,
            comparator="between",
            source="contract_name",
            contract_name=title,
            display_low=lo,
            display_high=hi,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        )

    gt_match = re.search(
        r"\bbe\s*(?:>|>=|at least|above|over)\s*(-?\d+(?:\.\d+)?)",
        title,
        flags=re.IGNORECASE,
    )
    if gt_match:
        display_threshold = float(gt_match.group(1))
        half_step = _contract_half_step(mode)
        return WeatherContractSemantics(
            ticker=ticker,
            mode=mode,
            comparator="gt",
            source="contract_name",
            contract_name=title,
            threshold=display_threshold + half_step,
            display_low=display_threshold,
        )

    lt_match = re.search(
        r"\bbe\s*(?:<|<=|at most|below|under)\s*(-?\d+(?:\.\d+)?)",
        title,
        flags=re.IGNORECASE,
    )
    if lt_match:
        display_threshold = float(lt_match.group(1))
        half_step = _contract_half_step(mode)
        return WeatherContractSemantics(
            ticker=ticker,
            mode=mode,
            comparator="lt",
            source="contract_name",
            contract_name=title,
            threshold=display_threshold - half_step,
            display_high=display_threshold,
        )

    return None


def resolve_weather_contract(
    ticker: str,
    contract_name: str = "",
    strike: float | None = None,
) -> WeatherContractSemantics | None:
    mode = weather_mode_for_ticker(ticker)
    if mode is None:
        return None

    titled = _title_semantics(ticker, contract_name, mode)
    if titled is not None:
        return titled

    symbol = (ticker or "").upper()
    if "-B" in symbol and strike is not None:
        low = math.floor(float(strike))
        high = math.ceil(float(strike))
        lower_bound, upper_bound = _display_range_to_bounds(low, high, mode)
        return WeatherContractSemantics(
            ticker=ticker,
            mode=mode,
            comparator="between",
            source="ticker_bin_fallback",
            contract_name=_clean_title(contract_name),
            display_low=float(low),
            display_high=float(high),
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        )

    if "-L" in symbol and strike is not None:
        return WeatherContractSemantics(
            ticker=ticker,
            mode=mode,
            comparator="lt",
            source="ticker_lt_fallback",
            contract_name=_clean_title(contract_name),
            threshold=float(strike),
        )

    if "-T" in symbol and strike is not None:
        # Temperature-chain edge buckets are not directionally safe from the ticker alone.
        ambiguous = mode in {"HIGH", "LOW", "TEMP"}
        return WeatherContractSemantics(
            ticker=ticker,
            mode=mode,
            comparator="gt",
            source="ticker_t_fallback",
            contract_name=_clean_title(contract_name),
            threshold=float(strike),
            ambiguous=ambiguous,
        )

    return None


def member_satisfies_contract(value: float, semantics: WeatherContractSemantics) -> bool:
    val = float(value)
    if semantics.comparator == "between":
        if semantics.lower_bound is None or semantics.upper_bound is None:
            return False
        return semantics.lower_bound <= val < semantics.upper_bound
    if semantics.comparator == "gt":
        if semantics.threshold is None:
            return False
        return val > semantics.threshold
    if semantics.threshold is None:
        return False
    return val < semantics.threshold


def probability_from_members(
    members: Iterable[float],
    semantics: WeatherContractSemantics,
) -> float | None:
    values = [float(v) for v in members]
    if not values:
        return None
    hits = sum(1 for value in values if member_satisfies_contract(value, semantics))
    return hits / len(values)


def yes_probability_from_weather_data(
    ticker: str,
    w_data: dict | None,
    contract_name: str = "",
    strike: float | None = None,
) -> float | None:
    if not w_data:
        return None

    semantics = resolve_weather_contract(
        ticker=ticker,
        contract_name=contract_name,
        strike=strike,
    )
    if semantics is None or semantics.ambiguous:
        return None

    if semantics.mode in {"RAIN", "SNOW"}:
        members = w_data.get("members_precip", [])
    elif semantics.mode == "WIND":
        members = w_data.get("members_wind", [])
    elif semantics.mode == "LOW":
        members = w_data.get("members_low", [])
    else:
        members = w_data.get("members_high", [])
    return probability_from_members(members, semantics)


def resolve_weather_observation(
    ticker: str,
    observed_high: float | None,
    observed_low: float | None,
    observed_precip: float | None = None,
    contract_name: str = "",
    strike: float | None = None,
) -> tuple[str, float, str] | None:
    semantics = resolve_weather_contract(
        ticker=ticker,
        contract_name=contract_name,
        strike=strike,
    )
    if semantics is None or semantics.ambiguous:
        return None

    if semantics.mode == "HIGH":
        if observed_high is None:
            return None
        observed_value = float(observed_high)
        label = "daily_max"
    elif semantics.mode == "LOW":
        if observed_low is None:
            return None
        observed_value = float(observed_low)
        label = "daily_min"
    elif semantics.mode in {"RAIN", "SNOW"}:
        if observed_precip is None:
            return None
        observed_value = float(observed_precip)
        label = "daily_precip"
    else:
        return None

    side = "YES" if member_satisfies_contract(observed_value, semantics) else "NO"
    if semantics.comparator == "between":
        notes = (
            f"{label}={observed_value:.2f} range="
            f"[{semantics.lower_bound:.2f}, {semantics.upper_bound:.2f})"
        )
    else:
        op = ">" if semantics.comparator == "gt" else "<"
        notes = f"{label}={observed_value:.2f} threshold={op}{semantics.threshold:.2f}"
    return side, observed_value, notes
