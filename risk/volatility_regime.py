"""
risk/volatility_regime.py — Per-market volatility regime detector.

Computes the ratio of short-window realized vol to long-window realized vol
and maps it to a V_score multiplier used by unified_sizer.py.

  ratio = rv_5d / rv_20d
  ratio > 1.5  → HIGH_VOLATILITY  → V_score 0.20 (size down 80%)
  ratio > 1.2  → ELEVATED         → V_score 0.50 (size down 50%)
  0.8–1.2      → NORMAL           → V_score 0.75 (slight discount)
  ratio < 0.8  → LOW_VOLATILITY   → V_score 1.00 (full size — compressed vol favors entries)

Crypto extension: funding rate gate.
  funding_rate > FUNDING_OVERHEATED_PCT → cap V_score at 0.50 regardless of vol ratio.

Cache TTL: 5 minutes (vol regime changes slowly; re-fetch once per scan cycle).
"""
import os
import sys
import math
import time
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FUNDING_OVERHEATED_PCT

# ─── Cache ──────────────────────────────────────────────────────────────────
_CACHE: dict = {}
_CACHE_TTL: int = 300  # 5 minutes


def _is_stale(ts: float) -> bool:
    return time.time() - ts > _CACHE_TTL


# ─── Symbol normalisation ────────────────────────────────────────────────────

def _to_yf_symbol(symbol: str) -> str:
    """Convert broker symbol format to yfinance format.

    BTC-USDC → BTC-USD
    ETH-USDC → ETH-USD
    BTCUSDT  → BTC-USD
    ES, MES  → ES=F
    SPY      → SPY
    """
    sym = symbol.upper().strip()

    # Coinbase format: BASE-QUOTE
    if '-' in sym:
        base = sym.split('-')[0]
        return f"{base}-USD"

    # Binance perp format: BTCUSDT
    for stable in ('USDT', 'USDC', 'BUSD'):
        if sym.endswith(stable):
            base = sym[: -len(stable)]
            return f"{base}-USD"

    # Futures
    if sym in ('ES', 'MES'):
        return 'ES=F'

    return sym


def _get_daily_returns(symbol: str, days: int) -> Optional[list]:
    """Fetch `days` daily close-to-close returns for `symbol` via yfinance."""
    try:
        import yfinance as yf
        yf_sym = _to_yf_symbol(symbol)
        hist = yf.Ticker(yf_sym).history(period=f'{days + 10}d', interval='1d')
        if hist is None or hist.empty or len(hist) < days + 1:
            return None
        closes = hist['Close'].tail(days + 1).values
        returns = [
            (float(closes[i]) - float(closes[i - 1])) / max(float(closes[i - 1]), 1e-10)
            for i in range(1, len(closes))
        ]
        return returns[-days:]
    except Exception:
        return None


def _realized_vol(returns: list) -> float:
    """Annualized realized volatility from a list of daily returns."""
    if not returns or len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    return math.sqrt(variance) * math.sqrt(252)


# ─── Main public function ────────────────────────────────────────────────────

def get_volatility_regime(
    symbol: str,
    market: str = 'crypto',
    n_short: int = 5,
    n_long: int = 20,
    funding_rate: float = 0.0,
) -> dict:
    """
    Compute the volatility regime for `symbol`.

    Args:
        symbol:       Instrument symbol (any broker format).
        market:       'crypto' | 'polymarket' | 'mes'
        n_short:      Short realized-vol window in days (default 5).
        n_long:       Long realized-vol window in days (default 20).
        funding_rate: Current 8-hour funding rate (crypto only, fraction: 0.0005 = 0.05%).

    Returns dict:
        label     : str   'NORMAL' | 'LOW_VOLATILITY' | 'ELEVATED' | 'HIGH_VOLATILITY'
        v_score   : float 0.20 – 1.00
        ratio     : float rv_short / rv_long (nan if data unavailable)
        rv_short  : float annualised short-window realized vol
        rv_long   : float annualised long-window realized vol
        funding_capped : bool  True if funding rate forced a V_score cap
        data_ok   : bool  False = fell back to default (NORMAL, 0.75)
    """
    cache_key = f"{symbol}:{market}"
    if cache_key in _CACHE and not _is_stale(_CACHE[cache_key]['_ts']):
        result = dict(_CACHE[cache_key])
        result.pop('_ts', None)
        return result

    result = _compute_regime(symbol, market, n_short, n_long, funding_rate)
    _CACHE[cache_key] = {**result, '_ts': time.time()}
    return result


def _compute_regime(
    symbol: str,
    market: str,
    n_short: int,
    n_long: int,
    funding_rate: float,
) -> dict:
    # Fetch enough data for the long window
    returns = _get_daily_returns(symbol, n_long)
    if returns is None or len(returns) < n_long:
        return {
            'label': 'NORMAL',
            'v_score': 0.75,
            'ratio': float('nan'),
            'rv_short': 0.0,
            'rv_long': 0.0,
            'funding_capped': False,
            'data_ok': False,
        }

    short_returns = returns[-n_short:]
    rv_short = _realized_vol(short_returns)
    rv_long = _realized_vol(returns)

    ratio = rv_short / max(rv_long, 1e-10)

    # Map ratio → label + v_score
    if ratio > 1.5:
        label, v_score = 'HIGH_VOLATILITY', 0.20
    elif ratio > 1.2:
        label, v_score = 'ELEVATED', 0.50
    elif ratio < 0.8:
        label, v_score = 'LOW_VOLATILITY', 1.00
    else:
        label, v_score = 'NORMAL', 0.75

    # Crypto funding rate gate: overheated longs → cap size
    funding_capped = False
    if market == 'crypto' and funding_rate > FUNDING_OVERHEATED_PCT:
        if v_score > 0.50:
            v_score = 0.50
            funding_capped = True

    return {
        'label': label,
        'v_score': round(v_score, 4),
        'ratio': round(ratio, 4),
        'rv_short': round(rv_short, 6),
        'rv_long': round(rv_long, 6),
        'funding_capped': funding_capped,
        'data_ok': True,
    }


def invalidate_cache(symbol: Optional[str] = None) -> None:
    """Force a fresh fetch next call. Pass None to clear all."""
    if symbol is None:
        _CACHE.clear()
    else:
        for key in list(_CACHE.keys()):
            if key.startswith(f"{symbol}:"):
                del _CACHE[key]
