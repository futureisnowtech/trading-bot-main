"""
data/liquidation_feed.py — Coinglass public liquidation data.

Returns hourly long/short liquidation volumes for a symbol.
liq_long_ratio > 0.7 = long liquidation cascade = avoid LONG entries.
liq_short_ratio > 0.7 = short squeeze = favor LONG.

Uses Binance Futures public API (no auth required):
  GET https://fapi.binance.com/futures/data/globalLongShortAccountRatio
  GET https://fapi.binance.com/futures/data/takerlongshortRatio

Coinglass requires auth for liquidation history; Binance Futures provides
equivalent long/short ratio data as a public endpoint.

Cache: 10 minutes (liquidations are hourly data).
Fail-open: on any error returns neutral signal.
"""

import time
import logging
from typing import Dict

import requests

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE: Dict[str, dict] = {}         # symbol -> result dict
_CACHE_TS: Dict[str, float] = {}     # symbol -> epoch timestamp of last fetch
_CACHE_TTL = 600                      # 10 minutes

# ── Thresholds ────────────────────────────────────────────────────────────────
CASCADE_THRESHOLD = 0.70   # liq_long_ratio above this = cascade
SQUEEZE_THRESHOLD = 0.70   # liq_short_ratio above this = squeeze

# ── Neutral fallback (returned on any error) ──────────────────────────────────
_NEUTRAL = {
    'liq_long_ratio': 0.5,
    'liq_short_ratio': 0.5,
    'liq_signal': 'neutral',
    'liq_avoid_long': False,
}

# ── Symbol normalisation ──────────────────────────────────────────────────────
def _to_binance_symbol(symbol: str) -> str:
    """Convert Coinbase-style 'BTC-USDC' or 'BTC-USD' to Binance 'BTCUSDT'."""
    sym = symbol.upper().replace('-', '')
    # Replace USDC/USD suffix → USDT (Binance perp pairs use USDT)
    for suffix in ('USDC', 'USD'):
        if sym.endswith(suffix):
            sym = sym[:-len(suffix)] + 'USDT'
            break
    return sym


def _classify(long_ratio: float) -> dict:
    """Classify the ratio and return the full signal dict."""
    short_ratio = 1.0 - long_ratio

    if long_ratio > CASCADE_THRESHOLD:
        signal = 'cascade'
        avoid_long = True
    elif short_ratio > SQUEEZE_THRESHOLD:
        signal = 'squeeze'
        avoid_long = False
    else:
        signal = 'neutral'
        avoid_long = False

    return {
        'liq_long_ratio': round(long_ratio, 4),
        'liq_short_ratio': round(short_ratio, 4),
        'liq_signal': signal,
        'liq_avoid_long': avoid_long,
    }


def _fetch_binance_lsratio(binance_symbol: str) -> dict:
    """
    Fetch taker buy/sell ratio from Binance Futures public endpoint.

    Endpoint: GET /futures/data/takerlongshortRatio
    Returns 1 candle of period '1h' — the most recent completed hour.
    'buySellRatio' = taker buy volume / taker sell volume.
      > 1.0 = more longs being bought (bullish pressure)
      < 1.0 = more shorts being sold (bearish pressure / liquidation risk)

    We convert to a 0..1 long_ratio:
      long_ratio = buySellRatio / (1 + buySellRatio)
    so that:
      buySellRatio = 3.0 → long_ratio = 0.75 (longs dominate → cascade risk)
      buySellRatio = 0.33 → long_ratio = 0.25 (shorts dominate → squeeze risk)
    """
    url = 'https://fapi.binance.com/futures/data/takerlongshortRatio'
    params = {
        'symbol': binance_symbol,
        'period': '1h',
        'limit': 1,
    }
    resp = requests.get(url, params=params, timeout=5)
    resp.raise_for_status()
    data = resp.json()

    if not data or not isinstance(data, list):
        raise ValueError(f"Unexpected response: {data!r}")

    row = data[-1]  # most recent row
    buy_sell_ratio = float(row['buySellRatio'])
    long_ratio = buy_sell_ratio / (1.0 + buy_sell_ratio)
    return _classify(long_ratio)


def _fetch_binance_lsaccount(binance_symbol: str) -> dict:
    """
    Fallback: use globalLongShortAccountRatio.

    Endpoint: GET /futures/data/globalLongShortAccountRatio
    'longShortRatio' = long accounts / short accounts (not volume).
    Converted to long_ratio = ratio / (1 + ratio).
    """
    url = 'https://fapi.binance.com/futures/data/globalLongShortAccountRatio'
    params = {
        'symbol': binance_symbol,
        'period': '1h',
        'limit': 1,
    }
    resp = requests.get(url, params=params, timeout=5)
    resp.raise_for_status()
    data = resp.json()

    if not data or not isinstance(data, list):
        raise ValueError(f"Unexpected response: {data!r}")

    row = data[-1]
    ls_ratio = float(row['longShortRatio'])
    long_ratio = ls_ratio / (1.0 + ls_ratio)
    return _classify(long_ratio)


def get_liquidation_signal(symbol: str) -> dict:
    """
    Return long/short liquidation signal for symbol.

    Tries Binance takerlongshortRatio first (volume-weighted, preferred).
    Falls back to globalLongShortAccountRatio on error.
    Falls back to neutral on all errors.

    Args:
        symbol: Trading pair in any format (BTC-USDC, BTC-USD, BTCUSDT, etc.)

    Returns:
        {
            'liq_long_ratio': float,   # 0..1; fraction of volume on long side
            'liq_short_ratio': float,  # 0..1; 1 - liq_long_ratio
            'liq_signal': str,         # 'cascade' | 'squeeze' | 'neutral'
            'liq_avoid_long': bool,    # True when signal == 'cascade'
        }
    """
    # Cache lookup
    now = time.time()
    if symbol in _CACHE and (now - _CACHE_TS.get(symbol, 0)) < _CACHE_TTL:
        return _CACHE[symbol]

    binance_sym = _to_binance_symbol(symbol)

    # Binance only lists perpetual pairs — skip equity or non-USDT symbols
    if not binance_sym.endswith('USDT'):
        return dict(_NEUTRAL)

    result = None
    try:
        result = _fetch_binance_lsratio(binance_sym)
        logger.debug(
            "[liquidation_feed] %s taker L/S ratio: long=%.3f signal=%s",
            binance_sym, result['liq_long_ratio'], result['liq_signal'],
        )
    except Exception as primary_err:
        logger.debug(
            "[liquidation_feed] taker ratio failed for %s (%s), trying account ratio",
            binance_sym, primary_err,
        )
        try:
            result = _fetch_binance_lsaccount(binance_sym)
            logger.debug(
                "[liquidation_feed] %s account L/S ratio: long=%.3f signal=%s",
                binance_sym, result['liq_long_ratio'], result['liq_signal'],
            )
        except Exception as fallback_err:
            logger.debug(
                "[liquidation_feed] both endpoints failed for %s: %s",
                binance_sym, fallback_err,
            )

    if result is None:
        result = dict(_NEUTRAL)

    _CACHE[symbol] = result
    _CACHE_TS[symbol] = now
    return result
