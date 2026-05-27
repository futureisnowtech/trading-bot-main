"""
runtime/execution_universe.py — Execution universe classification (v15.10).

Defines three tiers for every candidate symbol:
  core           — actual live-broker-supported Coinbase underlyings
  research_only  — scanner-visible but no live execution path
  suppressed     — statistically negative edge (config.SUPPRESSED_SYMBOLS)

The scanner can still run broad in research mode, but the live scheduler and
manual scan default to the actual tradable set. Only core symbols enter the
live execution path. research_only candidates
are journaled with decision='research_only_block' so the learning layer can
observe their outcomes without committing capital.

Single source of truth for symbol ↔ tier mapping.  v10_runner, manual_scan,
and any future execution path must import from here rather than implement
their own normalisation.
"""

from __future__ import annotations

_TIER_CORE = "core"
_TIER_RESEARCH = "research_only"
_TIER_SUPPRESSED = "suppressed"


def get_underlying(symbol: str) -> str:
    """
    Normalize any symbol format to its base asset.

    Examples:
      PF_ETHUSD  → ETH
      ETH        → ETH
      ETHUSDT    → ETH
      ETH-USDC   → ETH
      ETHFI      → ETHFI  (different asset — no match to ETH)
    """
    s = symbol.upper().strip()
    for pfx in ("PF_", "PI_"):
        if s.startswith(pfx):
            s = s[len(pfx) :]
            break
    if "-" in s:
        return s.split("-")[0]
    for q in ("USDT", "USDC", "BUSD", "USD"):
        if s.endswith(q) and len(s) > len(q) + 1:
            s = s[: -len(q)]
            break
    return s


def get_execution_tier(symbol: str) -> str:
    """
    Return the execution tier for *symbol*.

    Check order:
      1. suppressed  — exact symbol in config.SUPPRESSED_SYMBOLS
      2. core        — normalized underlying in config.CORE_EXECUTION_UNDERLYINGS
      3. research_only — everything else
    """
    try:
        from config import SUPPRESSED_SYMBOLS, CORE_EXECUTION_UNDERLYINGS
    except ImportError:
        return _TIER_RESEARCH

    if symbol.upper().strip() in {s.upper() for s in SUPPRESSED_SYMBOLS}:
        return _TIER_SUPPRESSED

    underlying = get_underlying(symbol)
    if underlying.upper() in {u.upper() for u in CORE_EXECUTION_UNDERLYINGS}:
        return _TIER_CORE

    return _TIER_RESEARCH


def is_core_execution_symbol(symbol: str) -> bool:
    """Return True iff symbol maps to a core execution underlying."""
    return get_execution_tier(symbol) == _TIER_CORE


def is_core_underlying(underlying: str) -> bool:
    """Return True iff *underlying* is in the configured core execution universe."""
    try:
        from config import CORE_EXECUTION_UNDERLYINGS
    except ImportError:
        return False
    return underlying.upper().strip() in {u.upper() for u in CORE_EXECUTION_UNDERLYINGS}


def get_execution_policy(symbol: str) -> dict:
    """
    Return a policy dict for *symbol*:
      {
          "tier":    "core" | "research_only" | "suppressed",
          "execute": bool,   # True = may enter live execution
          "reason":  str,    # human-readable tag for journaling / UI
      }
    """
    tier = get_execution_tier(symbol)
    underlying = get_underlying(symbol)
    if tier == _TIER_CORE:
        return {
            "symbol": symbol,
            "underlying": underlying,
            "tier": tier,
            "execute": True,
            "reason": "core_execution_underlying",
        }
    if tier == _TIER_SUPPRESSED:
        return {
            "symbol": symbol,
            "underlying": underlying,
            "tier": tier,
            "execute": False,
            "reason": "suppressed_symbol",
        }
    return {
        "symbol": symbol,
        "underlying": underlying,
        "tier": tier,
        "execute": False,
        "reason": "non_core_execution_universe",
    }
