"""
strategies/funding_instrument_router.py — Spot vs perp instrument routing.

Funding is NOT a strategy. It is a modifier on holding quality.
This module answers: given a symbol, direction, and current funding rate,
should the trade use a perp or spot instrument?

Routing rules by market type:
  CARRY_MAJOR:     perp preferred when funding is favorable or neutral.
                   Spot preferred when funding is hostile.
  CLEAN_TREND_ALT: spot-first. Perp tolerated only if funding not hostile.
  EXPLOSIVE_CONVEX: spot preferred. Perp acceptable only for fast exits.
  REFLEXIVE_MEME:  blocked regardless of instrument.
  MEAN_REVERSION:  spot preferred. Perp risks adverse funding during hold.
  DO_NOT_TRADE:    blocked.

Funding thresholds (per 8h period, sign convention: positive = longs pay):
  HOSTILE:         rate > +0.02%/8h  → longs pay significantly
  NEUTRAL:         |rate| <= 0.02%/8h
  FAVORABLE:       rate < -0.01%/8h  → shorts pay (longs collect)
  CARRY_POSITIVE:  rate < -0.03%/8h  → strong carry incentive

Safe to wire now as a read-only helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from strategies.market_type_classifier import MarketType


class FundingRegime(str, Enum):
    HOSTILE = "hostile"  # longs pay; avoid perp longs
    NEUTRAL = "neutral"  # near zero; no strong carry signal
    FAVORABLE = "favorable"  # shorts pay; carry sweetens hold
    CARRY_POSITIVE = "carry_positive"  # strong carry; primary reason to hold perp


class InstrumentRoute(str, Enum):
    PERP_PREFERRED = "perp_preferred"  # use perp; funding helps or is neutral
    PERP_TOLERATED = "perp_tolerated"  # perp OK but not the primary reason
    SPOT_PREFERRED = "spot_preferred"  # use spot; funding hostile or unsuitable
    BLOCKED = "blocked"  # do not trade this symbol/direction


# Funding thresholds per 8-hour settlement period (as decimal, e.g. 0.0002 = 0.02%)
_HOSTILE_THRESHOLD = 0.0002  # longs pay > 0.02%/8h
_FAVORABLE_THRESHOLD = -0.0001  # shorts pay > 0.01%/8h (longs collect)
_CARRY_THRESHOLD = -0.0003  # strong carry: longs collect > 0.03%/8h


@dataclass(frozen=True)
class RoutingDecision:
    symbol: str
    direction: str
    market_type: MarketType
    funding_regime: FundingRegime
    route: InstrumentRoute
    reason: str
    is_pf_symbol: bool = False  # True if this is a Kraken PF_ perp (paper-only tonight)

    @property
    def live_eligible(self) -> bool:
        """True if this route can be executed live tonight (non-PF, not blocked)."""
        return self.route != InstrumentRoute.BLOCKED and not self.is_pf_symbol


def classify_funding(rate: Optional[float]) -> FundingRegime:
    """
    Classify a raw funding rate (decimal per 8h) into a FundingRegime.

    Args:
        rate: funding rate per 8h. Positive = longs pay. None = unknown.
    """
    if rate is None:
        return FundingRegime.NEUTRAL  # assume neutral when unknown

    if rate > _HOSTILE_THRESHOLD:
        return FundingRegime.HOSTILE
    elif rate < _CARRY_THRESHOLD:
        return FundingRegime.CARRY_POSITIVE
    elif rate < _FAVORABLE_THRESHOLD:
        return FundingRegime.FAVORABLE
    else:
        return FundingRegime.NEUTRAL


def route(
    symbol: str,
    direction: str,
    market_type: MarketType,
    funding_rate: Optional[float] = None,
) -> RoutingDecision:
    """
    Determine whether to use spot or perp for this trade.

    Args:
        symbol: ticker (e.g. "BTC", "PF_XBTUSD")
        direction: "LONG" or "SHORT"
        market_type: from market_type_classifier.classify()
        funding_rate: current 8h funding rate as decimal.
                      Positive = longs pay. None = treat as neutral.

    Returns:
        RoutingDecision with recommended instrument route.
    """
    is_pf = symbol.startswith("PF_")
    regime = classify_funding(funding_rate)
    dir_upper = direction.upper()

    # Blocked market types — no route
    if market_type in (MarketType.DO_NOT_TRADE, MarketType.REFLEXIVE_MEME):
        return RoutingDecision(
            symbol=symbol,
            direction=dir_upper,
            market_type=market_type,
            funding_regime=regime,
            route=InstrumentRoute.BLOCKED,
            reason=f"market_type={market_type.value} is blocked",
            is_pf_symbol=is_pf,
        )

    # Short routing — shorts are suppressed tonight per go-live audit
    if dir_upper == "SHORT":
        return RoutingDecision(
            symbol=symbol,
            direction=dir_upper,
            market_type=market_type,
            funding_regime=regime,
            route=InstrumentRoute.BLOCKED,
            reason="shorts suppressed: go-live audit AMBER (long net=+13.56 vs short net=-13.82)",
            is_pf_symbol=is_pf,
        )

    # Long routing by market type
    if market_type == MarketType.CARRY_MAJOR:
        if regime == FundingRegime.HOSTILE:
            r = InstrumentRoute.SPOT_PREFERRED
            reason = (
                f"carry_major but funding hostile ({_fmt_rate(funding_rate)}); "
                "perp long pays too much; use spot if available"
            )
        elif regime in (FundingRegime.FAVORABLE, FundingRegime.CARRY_POSITIVE):
            r = InstrumentRoute.PERP_PREFERRED
            reason = (
                f"carry_major with favorable funding ({_fmt_rate(funding_rate)}); "
                "perp preferred — collect carry while directional"
            )
        else:  # NEUTRAL
            r = InstrumentRoute.PERP_TOLERATED
            reason = (
                f"carry_major, neutral funding ({_fmt_rate(funding_rate)}); "
                "perp acceptable; watch for regime shift"
            )

    elif market_type == MarketType.CLEAN_TREND_ALT:
        if regime == FundingRegime.HOSTILE:
            r = InstrumentRoute.SPOT_PREFERRED
            reason = (
                f"clean_trend_alt with hostile funding ({_fmt_rate(funding_rate)}); "
                "funding cost erodes directional edge; spot-first"
            )
        elif regime == FundingRegime.CARRY_POSITIVE:
            r = InstrumentRoute.PERP_TOLERATED
            reason = (
                f"clean_trend_alt with carry-positive funding ({_fmt_rate(funding_rate)}); "
                "perp acceptable — carry sweetens hold; not primary reason"
            )
        else:
            r = InstrumentRoute.SPOT_PREFERRED
            reason = (
                f"clean_trend_alt; spot-first doctrine; "
                f"funding={_fmt_rate(funding_rate)} ({regime.value})"
            )

    elif market_type == MarketType.EXPLOSIVE_CONVEX:
        # Fast trades — funding matters less; but avoid hostile perps for convex
        if regime == FundingRegime.HOSTILE:
            r = InstrumentRoute.SPOT_PREFERRED
            reason = (
                f"explosive_convex with hostile funding ({_fmt_rate(funding_rate)}); "
                "spot reduces cost on fast trades"
            )
        else:
            r = InstrumentRoute.SPOT_PREFERRED
            reason = (
                f"explosive_convex; spot-first regardless of funding "
                f"({_fmt_rate(funding_rate)}); perp adds passive hold risk"
            )

    elif market_type == MarketType.MEAN_REVERSION:
        if regime == FundingRegime.HOSTILE:
            r = InstrumentRoute.SPOT_PREFERRED
            reason = (
                f"mean_reversion; hostile funding ({_fmt_rate(funding_rate)}) "
                "dangerous on multi-session MR hold; spot only"
            )
        else:
            r = InstrumentRoute.SPOT_PREFERRED
            reason = (
                f"mean_reversion; spot-first; funding={_fmt_rate(funding_rate)} "
                "({regime.value}); MR hold duration unpredictable"
            )

    else:
        r = InstrumentRoute.SPOT_PREFERRED
        reason = f"unknown market type {market_type.value}; default spot-first"

    return RoutingDecision(
        symbol=symbol,
        direction=dir_upper,
        market_type=market_type,
        funding_regime=regime,
        route=r,
        reason=reason,
        is_pf_symbol=is_pf,
    )


def _fmt_rate(rate: Optional[float]) -> str:
    if rate is None:
        return "unknown"
    return f"{rate * 100:+.4f}%/8h"


# ---------------------------------------------------------------------------
# Carry suitability quick-look table (data-grounded)
# Summarizes historical funding posture from scan_candidates + prior analysis.
# Note: scan_candidates funding_rate column is 0 for Projects DB fresh session;
# these are prior-session estimates and external data points.
# ---------------------------------------------------------------------------

CARRY_SUITABILITY = {
    # symbol: (carry_suitability, notes)
    "BTC": (
        "high",
        "historically neutral-to-favorable; dominant liquidity; best carry vehicle",
    ),
    "ETH": ("high", "often pays longs in bull phases; strong carry candidate"),
    "SOL": (
        "medium",
        "funding volatile; periods of hostile then favorable; monitor per session",
    ),
    "BNB": ("medium", "exchange token; funding depends on Binance ecosystem sentiment"),
    "XRP": (
        "low",
        "low vol but funding often hostile to longs; prefer spot for directional",
    ),
    "NEAR": (
        "low",
        "often pays short premium; spot-first; perp only if funding clearly favorable",
    ),
    "LINK": ("low", "often hostile funding for longs; spot-first"),
    "AVAX": ("low", "hostile funding history; spot-first for directional longs"),
    "MORPHO": ("medium", "DeFi token; funding erratic; spot-first until stable"),
    "TON": ("low", "perp funding often hostile for longs; spot preferred"),
    "ZEC": (
        "low",
        "privacy coin; lower perp liquidity; spot-first; PF_ZECUSD performing well",
    ),
    "TAO": (
        "very_low",
        "explosive vol; funding wildly variable; perp adds blowup risk",
    ),
    "ENA": (
        "very_low",
        "high vol + deep 90d drawdown; never carry; direction-only if any",
    ),
    "DOGE": ("low", "mean-reversion only; avoid long perp holds; funding can flip"),
    "XMR": ("none", "scanner-blocked; no perp carry doctrine; spot only if ever"),
}
