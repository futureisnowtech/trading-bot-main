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


_HOURLY_WEATHER_TICKER_RE = re.compile(r"-\d{2}[A-Z]{3}\d{4}(?:-|$)")
_HOURLY_TITLE_RE = re.compile(r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b")
_SHORT_CADENCE_TEMP_PREFIXES = ("KXLOWT", "KXHIGHT")
LIVE_ENTRY_SCOPE = "ALL_WEATHER_LANES"


def has_hourly_weather_timestamp(ticker: str) -> bool:
    return bool(_HOURLY_WEATHER_TICKER_RE.search((ticker or "").upper()))


def is_hourly_weather_contract(
    ticker: str,
    *,
    contract_name: str = "",
) -> bool:
    if weather_mode_for_ticker(ticker) is None:
        return False
    if has_hourly_weather_timestamp(ticker):
        return True
    title = _clean_title(contract_name).lower()
    return "hourly" in title or bool(_HOURLY_TITLE_RE.search(title))


def is_short_cadence_weather_contract(
    ticker: str,
    *,
    contract_name: str = "",
) -> bool:
    symbol = (ticker or "").upper()
    if not symbol or weather_mode_for_ticker(symbol) is None:
        return False
    if has_hourly_weather_timestamp(symbol):
        return True
    if symbol.startswith(_SHORT_CADENCE_TEMP_PREFIXES):
        return True
    title = _clean_title(contract_name).lower()
    return "hourly" in title or bool(_HOURLY_TITLE_RE.search(title))


def live_entry_scope() -> str:
    return LIVE_ENTRY_SCOPE


def is_live_entry_weather_contract(
    ticker: str,
    *,
    contract_name: str = "",
) -> bool:
    # Fresh entries are allowed across all active weather lanes.
    return weather_mode_for_ticker(ticker) is not None


def weather_mode_for_ticker(ticker: str) -> WeatherMode | None:
    symbol = (ticker or "").upper()
    if has_hourly_weather_timestamp(symbol) and (
        "TEMP" in symbol or "HIGH" in symbol or "LOW" in symbol
    ):
        return "TEMP"
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
    if "TEMP" in symbol:
        return "TEMP"
    return None


def weather_trade_bucket(
    ticker: str,
    *,
    contract_name: str = "",
) -> str:
    mode = weather_mode_for_ticker(ticker)
    if mode == "HIGH":
        return "Daily High"
    if mode == "LOW":
        return "Daily Low"
    if mode == "RAIN":
        return "Rain"
    if mode == "TEMP":
        return "Hourly Temp"
    if mode == "SNOW":
        return "Snow"
    if mode == "WIND":
        return "Wind"

    title = _clean_title(contract_name).lower()
    if "hourly" in title or _HOURLY_TITLE_RE.search(title):
        return "Hourly Temp"
    return "Other Weather"


def _contract_half_step(mode: WeatherMode) -> float:
    return 0.5 if mode in {"HIGH", "LOW"} else 0.0


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
        r"(?:be\s+)?(-?\d+(?:\.\d+)?)\s*(?:°|degrees|deg)?\s*(?:to|and|[-–])\s*(-?\d+(?:\.\d+)?)\s*(?:°|degrees|deg|inches|inch|in|mph)?",
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
        r"(?:\bbe\s*(?:>|>=|at least|above|over)\s*(-?\d+(?:\.\d+)?))|(?:(-?\d+(?:\.\d+)?)\s*(?:°|degrees|deg)?\s*(?:or higher|or more|and above))",
        title,
        flags=re.IGNORECASE,
    )
    if gt_match:
        display_threshold = float(gt_match.group(1) or gt_match.group(2))
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
        r"(?:\bbe\s*(?:<|<=|at most|below|under)\s*(-?\d+(?:\.\d+)?))|(?:(-?\d+(?:\.\d+)?)\s*(?:°|degrees|deg)?\s*(?:or lower|or less|and below))",
        title,
        flags=re.IGNORECASE,
    )
    if lt_match:
        display_threshold = float(lt_match.group(1) or lt_match.group(2))
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
        ambiguous = mode in {"HIGH", "LOW"}
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
        limit = semantics.threshold if semantics.threshold is not None else semantics.display_low
        if limit is None:
            return False
        return val >= limit
    limit = semantics.threshold if semantics.threshold is not None else semantics.display_high
    if limit is None:
        return False
    return val <= limit


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
    elif semantics.mode == "TEMP":
        members = w_data.get("members_temp", [])
    else:
        members = w_data.get("members_high", [])
    return probability_from_members(members, semantics)


def resolve_weather_observation(
    ticker: str,
    observed_high: float | None,
    observed_low: float | None,
    observed_precip: float | None = None,
    observed_temp: float | None = None,
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
    elif semantics.mode == "TEMP":
        if observed_temp is None:
            return None
        observed_value = float(observed_temp)
        label = "hourly_temp"
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
