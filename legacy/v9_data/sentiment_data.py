"""
data/sentiment_data.py — v10 Unified sentiment data layer.

Aggregates all sentiment signals into a single dict per symbol.
Wires together the existing specialized feeds:
  - data/deribit_feed.py  → IV skew (options market directional bias)
  - data/onchain_feed.py  → Whale flow (on-chain activity)
  - Alternative.me        → Fear & Greed index (current + trend)
  - LunarCrush            → Social sentiment (optional, free tier)

Returns a SentimentSnapshot used by:
  - ml/feature_builder.py (5 sentiment features for ML)
  - signal_engine.py (market context for technical scoring)
  - dashboard (regime display)

Usage:
    from data.sentiment_data import get_sentiment
    s = get_sentiment('BTCUSDT')
    s.fg_current          # 0-100 Fear & Greed
    s.fg_momentum_7d      # normalized 7d change
    s.skew_direction      # 'bullish' | 'bearish' | 'neutral'
    s.skew_25d            # raw 25d skew value
    s.iv_pct_rank         # IV percentile rank 0-100
    s.whale_signal        # 'accumulating' | 'distributing' | 'neutral'
    s.whale_strength      # 0.0 – 1.0
    s.avoid_long          # bool: True if sentiment strongly bearish
    s.avoid_short         # bool: True if sentiment strongly bullish
    s.context_str         # human-readable summary for debate prompts
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

# Import specialized feeds
try:
    from data.deribit_feed import get_iv_skew
    _DERIBIT_OK = True
except ImportError:
    _DERIBIT_OK = False

try:
    from data.onchain_feed import get_whale_flow
    _ONCHAIN_OK = True
except ImportError:
    _ONCHAIN_OK = False

# ── Constants ─────────────────────────────────────────────────────────────────
_FG_API = 'https://api.alternative.me/fng/'
_FG_CACHE_TTL = 900        # 15 min (F&G updates hourly)
_SENTIMENT_CACHE_TTL = 300  # 5 min per symbol
_FG_HISTORY_SIZE = 168      # 7 days × 24h = hourly history

# ── State ─────────────────────────────────────────────────────────────────────
_lock = threading.RLock()
_fg_cache: dict = {}
_fg_history: list = []     # rolling hourly F&G values
_symbol_cache: dict = {}   # symbol → SentimentSnapshot


@dataclass
class SentimentSnapshot:
    symbol: str = ''

    # Fear & Greed
    fg_current: float = 50.0
    fg_label: str = 'Neutral'
    fg_momentum_7d: float = 0.0       # (current - 7d_avg) / std_dev, normalized
    fg_7d_avg: float = 50.0

    # Options skew (Deribit)
    skew_direction: str = 'neutral'
    skew_25d: float = 0.0
    iv_pct_rank: float = 50.0
    atm_iv: Optional[float] = None

    # On-chain / whale flow
    whale_signal: str = 'neutral'
    whale_strength: float = 0.0

    # Derived flags
    avoid_long: bool = False
    avoid_short: bool = False

    # Human-readable summary
    context_str: str = ''

    # Meta
    ts: float = field(default_factory=time.time)


# ── Fear & Greed ──────────────────────────────────────────────────────────────

def _fetch_fg() -> Optional[dict]:
    """Fetch current + 7-day F&G history from alternative.me."""
    if not _REQUESTS_OK:
        return None
    try:
        r = requests.get(_FG_API, params={'limit': 8}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            return data.get('data', [])
    except Exception as e:
        logger.debug(f'[sentiment_data] F&G fetch error: {e}')
    return None


def _get_fg_data() -> dict:
    """Return cached F&G data. Refreshes every 15 minutes."""
    with _lock:
        if _fg_cache and (time.time() - _fg_cache.get('_ts', 0)) < _FG_CACHE_TTL:
            return _fg_cache

    data = _fetch_fg()

    if not data or len(data) < 1:
        with _lock:
            if _fg_cache:
                return _fg_cache
        return {
            'fg_current': 50.0,
            'fg_label': 'Neutral',
            'fg_momentum_7d': 0.0,
            'fg_7d_avg': 50.0,
            '_ts': time.time(),
        }

    current = float(data[0].get('value', 50))
    current_label = data[0].get('value_classification', 'Neutral')

    # 7-day average from history
    history_values = [float(d.get('value', 50)) for d in data[1:8]]
    fg_7d_avg = sum(history_values) / len(history_values) if history_values else 50.0

    # Momentum: normalized deviation from 7d avg
    if len(history_values) >= 2:
        import statistics
        std = statistics.stdev(history_values) if len(history_values) > 1 else 1.0
        fg_momentum = (current - fg_7d_avg) / (std + 1e-9)
    else:
        fg_momentum = 0.0

    # Append to rolling hourly history
    with _lock:
        _fg_history.append(current)
        if len(_fg_history) > _FG_HISTORY_SIZE:
            _fg_history.pop(0)

    result = {
        'fg_current': current,
        'fg_label': current_label,
        'fg_momentum_7d': round(fg_momentum, 3),
        'fg_7d_avg': round(fg_7d_avg, 1),
        '_ts': time.time(),
    }

    with _lock:
        _fg_cache.update(result)

    return result


# ── LunarCrush (optional) ─────────────────────────────────────────────────────

def _fetch_lunarcrush(symbol: str) -> dict:
    """
    LunarCrush free tier API for social sentiment.
    Returns neutral dict if unavailable.
    """
    _neutral = {'social_volume': 0, 'galaxy_score': 50, 'social_dominance': 0.0, 'source': 'unavailable'}
    if not _REQUESTS_OK:
        return _neutral

    # Map BTCUSDT → BTC
    coin = symbol.replace('USDT', '').replace('USDC', '').replace('USD', '').upper()

    try:
        r = requests.get(
            f'https://lunarcrush.com/api4/public/coins/{coin.lower()}/v1',
            timeout=8
        )
        if r.status_code == 200:
            d = r.json().get('data', {})
            return {
                'social_volume':    d.get('social_volume', 0),
                'galaxy_score':     d.get('galaxy_score', 50),
                'social_dominance': d.get('social_dominance', 0.0),
                'source':           'lunarcrush',
            }
    except Exception:
        pass
    return _neutral


# ── Derived flags ─────────────────────────────────────────────────────────────

def _compute_flags(fg_current: float, fg_momentum: float,
                   skew_direction: str, whale_signal: str) -> tuple:
    """
    Returns (avoid_long, avoid_short).
    avoid_long: multiple signals pointing bearish
    avoid_short: multiple signals pointing bullish
    """
    bearish_count = 0
    bullish_count = 0

    # F&G
    if fg_current < 20:    bearish_count += 2   # Extreme Fear
    elif fg_current < 35:  bearish_count += 1
    elif fg_current > 80:  bullish_count += 2   # Extreme Greed
    elif fg_current > 65:  bullish_count += 1

    # F&G momentum
    if fg_momentum < -1.5: bearish_count += 1
    elif fg_momentum > 1.5: bullish_count += 1

    # Options skew
    if skew_direction == 'bearish':  bearish_count += 1
    elif skew_direction == 'bullish': bullish_count += 1

    # Whale flow
    if whale_signal == 'distributing':  bearish_count += 1
    elif whale_signal == 'accumulating': bullish_count += 1

    # Flags: avoid when ≥ 3 signals aligned
    avoid_long = bearish_count >= 3
    avoid_short = bullish_count >= 3

    return avoid_long, avoid_short


def _build_context_str(snap: SentimentSnapshot) -> str:
    """Human-readable single-line summary for debate prompts / dashboard."""
    parts = [f'F&G:{snap.fg_current:.0f}({snap.fg_label})']
    if snap.skew_direction != 'neutral':
        parts.append(f'Skew:{snap.skew_direction}')
    if snap.whale_signal != 'neutral':
        parts.append(f'Whale:{snap.whale_signal}({snap.whale_strength:.2f})')
    if snap.avoid_long:
        parts.append('AVOID_LONG')
    if snap.avoid_short:
        parts.append('AVOID_SHORT')
    return ' | '.join(parts)


# ── Main public API ───────────────────────────────────────────────────────────

def get_sentiment(symbol: str) -> SentimentSnapshot:
    """
    Return a SentimentSnapshot for the given symbol.
    Caches for 5 minutes. Never raises.

    Args:
        symbol: Binance futures symbol e.g. 'BTCUSDT'
    """
    sym = symbol.upper()

    with _lock:
        cached = _symbol_cache.get(sym)
        if cached and (time.time() - cached.ts) < _SENTIMENT_CACHE_TTL:
            return cached

    snap = SentimentSnapshot(symbol=sym)

    # Fear & Greed
    try:
        fg = _get_fg_data()
        snap.fg_current = fg.get('fg_current', 50.0)
        snap.fg_label = fg.get('fg_label', 'Neutral')
        snap.fg_momentum_7d = fg.get('fg_momentum_7d', 0.0)
        snap.fg_7d_avg = fg.get('fg_7d_avg', 50.0)
    except Exception as e:
        logger.debug(f'[sentiment_data] F&G error: {e}')

    # IV Skew (Deribit)
    if _DERIBIT_OK:
        try:
            skew = get_iv_skew(sym)
            snap.skew_direction = skew.get('skew_direction', 'neutral')
            snap.skew_25d = skew.get('skew', 0.0)
            snap.iv_pct_rank = skew.get('iv_pct_rank', 50.0)
            snap.atm_iv = skew.get('atm_iv')
        except Exception as e:
            logger.debug(f'[sentiment_data] Deribit error: {e}')

    # Whale flow (on-chain)
    if _ONCHAIN_OK:
        try:
            whale = get_whale_flow(sym)
            snap.whale_signal = whale.get('whale_signal', 'neutral')
            snap.whale_strength = whale.get('whale_strength', 0.0)
        except Exception as e:
            logger.debug(f'[sentiment_data] Onchain error: {e}')

    # Derived flags
    snap.avoid_long, snap.avoid_short = _compute_flags(
        snap.fg_current, snap.fg_momentum_7d,
        snap.skew_direction, snap.whale_signal
    )

    snap.context_str = _build_context_str(snap)
    snap.ts = time.time()

    with _lock:
        _symbol_cache[sym] = snap

    return snap


def get_fg_for_features() -> tuple:
    """
    Returns (fg_current, fg_momentum_7d) as floats for ML feature extraction.
    Safe to call even if F&G is unavailable.
    """
    try:
        fg = _get_fg_data()
        return float(fg.get('fg_current', 50.0)), float(fg.get('fg_momentum_7d', 0.0))
    except Exception:
        return 50.0, 0.0


def get_options_skew_for_features(symbol: str) -> float:
    """
    Returns options_skew_25delta as float for ML feature extraction.
    Positive = bullish (call premium), negative = bearish (put premium).
    """
    if not _DERIBIT_OK:
        return 0.0
    try:
        skew = get_iv_skew(symbol)
        return float(skew.get('skew', 0.0))
    except Exception:
        return 0.0
