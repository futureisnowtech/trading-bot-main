"""
strategies/prediction_arb.py — Cross-platform prediction market arbitrage detector.

Scans for the same real-world event priced differently on Polymarket vs Kalshi.
Example: "Fed cuts rates in June" at 0.62 on Polymarket vs 0.71 on Kalshi = 9-point arb.

Matching strategy: normalize market titles (lowercase, strip punctuation, first 40 chars)
and compare. Markets with normalised title similarity above MATCH_MIN_SIMILARITY are
treated as the same underlying event.

Called by: scheduler/lane3_scanner.py (if LANE3_ENABLED)
Only yields results when both POLYMARKET_ENABLED and KALSHI_ENABLED are true in config.

Usage:
    from strategies.prediction_arb import scan_arbitrage
    opportunities = scan_arbitrage()          # list[dict], may be empty
    opportunities = scan_arbitrage(min_edge_pct=5.0)   # stricter threshold
"""
from __future__ import annotations

import re
import sys
import os
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Optional feed imports (fail-open) ────────────────────────────────────────

try:
    from data.polymarket_feed import get_active_markets as _get_poly_markets
    _POLY_AVAILABLE = True
except Exception:
    _POLY_AVAILABLE = False

try:
    from data.kalshi_feed import get_active_markets as _get_kalshi_markets
    _KALSHI_AVAILABLE = True
except Exception:
    _KALSHI_AVAILABLE = False

# ── Config (optional — degrade gracefully if config unavailable) ──────────────

try:
    from config import POLYMARKET_ENABLED, KALSHI_ENABLED
except Exception:
    POLYMARKET_ENABLED = False
    KALSHI_ENABLED = False

# ── Constants ─────────────────────────────────────────────────────────────────

# Number of normalised title characters used for matching.
# 40 chars covers "will the fed cut rates in june" reliably
# without being tripped by trailing date/phrasing differences.
_MATCH_CHARS = 40

_PUNCT_RE = re.compile(r"[^\w\s]")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, take first _MATCH_CHARS."""
    t = title.lower()
    t = _PUNCT_RE.sub(' ', t)
    t = ' '.join(t.split())          # collapse multiple spaces
    return t[:_MATCH_CHARS]


def _extract_price(market: dict) -> Optional[float]:
    """
    Pull the YES probability from a market dict.
    Both feeds return different field names — handle both.
    """
    for key in ('yes_price', 'price', 'best_ask', 'probability', 'last_price'):
        val = market.get(key)
        if val is not None:
            try:
                f = float(val)
                if 0.0 < f < 1.0:
                    return f
                # Some feeds return 0-100 scale
                if 1.0 <= f <= 100.0:
                    return f / 100.0
            except (TypeError, ValueError):
                continue
    return None


def _extract_title(market: dict) -> str:
    """Pull human-readable question from a market dict."""
    for key in ('question', 'title', 'name', 'market_title'):
        val = market.get(key, '')
        if val:
            return str(val)
    return ''


# ── Core scanner ─────────────────────────────────────────────────────────────

def scan_arbitrage(min_edge_pct: float = 3.0) -> list[dict]:
    """
    Scan Polymarket and Kalshi for the same event priced differently.

    Parameters
    ----------
    min_edge_pct : float
        Minimum price difference (in percentage points) to flag as an opportunity.
        Default 3.0 (= 3 percentage points, i.e. 0.03 probability).

    Returns
    -------
    list[dict]
        Each dict contains:
          market_a    (str)  — Polymarket question text
          platform_a  (str)  — always "polymarket"
          price_a     (float) — Polymarket YES probability (0-1)
          market_b    (str)  — Kalshi question text
          platform_b  (str)  — always "kalshi"
          price_b     (float) — Kalshi YES probability (0-1)
          edge_pct    (float) — abs(price_a - price_b) * 100
          description (str)  — human-readable summary
        Empty list if no arb found, feeds unavailable, or platforms not enabled.
    """
    if not (POLYMARKET_ENABLED and _POLY_AVAILABLE
            and KALSHI_ENABLED and _KALSHI_AVAILABLE):
        return []

    try:
        poly_markets = _get_poly_markets() or []
    except Exception as e:
        print(f"[prediction_arb] Polymarket feed error: {e}")
        poly_markets = []

    try:
        kalshi_markets = _get_kalshi_markets() or []
    except Exception as e:
        print(f"[prediction_arb] Kalshi feed error: {e}")
        kalshi_markets = []

    if not poly_markets or not kalshi_markets:
        return []

    # Build normalised-title lookup for Kalshi
    kalshi_index: dict[str, dict] = {}
    for m in kalshi_markets:
        title = _extract_title(m)
        if not title:
            continue
        key = _normalize(title)
        if key:
            kalshi_index[key] = m

    min_edge = min_edge_pct / 100.0
    opportunities: list[dict] = []

    for poly_m in poly_markets:
        poly_title = _extract_title(poly_m)
        if not poly_title:
            continue
        poly_key = _normalize(poly_title)
        if not poly_key:
            continue

        # Exact normalised-prefix match
        kalshi_m = kalshi_index.get(poly_key)
        if kalshi_m is None:
            continue

        price_poly = _extract_price(poly_m)
        price_kalshi = _extract_price(kalshi_m)
        if price_poly is None or price_kalshi is None:
            continue

        edge = abs(price_poly - price_kalshi)
        if edge < min_edge:
            continue

        kalshi_title = _extract_title(kalshi_m)
        edge_pct = round(edge * 100, 2)

        if price_poly > price_kalshi:
            desc = (f"Polymarket prices YES at {price_poly:.1%} vs "
                    f"Kalshi at {price_kalshi:.1%} (+{edge_pct:.1f}pp)")
        else:
            desc = (f"Kalshi prices YES at {price_kalshi:.1%} vs "
                    f"Polymarket at {price_poly:.1%} (+{edge_pct:.1f}pp)")

        opportunities.append({
            'market_a':   poly_title,
            'platform_a': 'polymarket',
            'price_a':    round(price_poly, 4),
            'market_b':   kalshi_title,
            'platform_b': 'kalshi',
            'price_b':    round(price_kalshi, 4),
            'edge_pct':   edge_pct,
            'description': desc,
        })

    # Sort by largest edge first
    opportunities.sort(key=lambda x: x['edge_pct'], reverse=True)
    return opportunities


# ── CLI helper ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    results = scan_arbitrage(min_edge_pct=2.0)
    if not results:
        print("[prediction_arb] No arbitrage opportunities found "
              "(feeds unavailable or no matching markets above threshold).")
    else:
        print(f"[prediction_arb] {len(results)} opportunity(ies) found:")
        for r in results:
            print(f"  {r['edge_pct']:.1f}pp | {r['description']}")
