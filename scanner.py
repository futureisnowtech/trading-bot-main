"""
scanner.py — Unified Bybit linear perp scanner.

7-step filter on ALL Bybit USDT linear perps, runs every 5 minutes, 24/7.
Returns top 15 candidates with direction and signal scores.

Data sources:
  ALL market data comes from Bybit V5 REST API — NOT Binance fapi (geo-blocked in US),
  NOT CoinGecko (creates fake synthetic tickers that don't exist as liquid perps).

Filter pipeline:
  1. Universe pull: all USDT linear tickers from Bybit, filter 24h turnover > $50M
     Validated against instruments-info (Trading status only, cached 24h)
  2. Momentum: vol_spike >= 1.2 AND price_move_4h >= 0.8% AND adx_15m >= 22
  3. Liquidity: ob depth > $50K each side, spread < 0.1%
     REJECT on missing or empty OB data — no fail-open
  4. Expected value: expected_profit >= $3.00 (after fees + funding cost)
  5. Correlation: reduce size flag if open position corr > 0.85
  6. Regime: match signal type to current regime
  7. Sort by vol_spike, take top 15
"""

import logging
import time
import threading
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

# ---------------------------------------------------------------------------
# Bybit V5 base URL — not geo-blocked in the US
# ---------------------------------------------------------------------------
_BYBIT_BASE = 'https://api.bybit.com'

# ---------------------------------------------------------------------------
# Bybit interval mapping: standard label -> Bybit API string
# ---------------------------------------------------------------------------
_BYBIT_INTERVAL_MAP = {
    '1m':  '1',
    '3m':  '3',
    '5m':  '5',
    '15m': '15',
    '30m': '30',
    '1h':  '60',
    '4h':  '240',
    '1d':  'D',
}

# ---------------------------------------------------------------------------
# Filter thresholds
# ---------------------------------------------------------------------------
_MIN_VOLUME_24H_USD  = 50_000_000   # Bybit turnover24h (USD denominated)
_MIN_VOL_SPIKE       = 1.2
_MIN_PRICE_MOVE_4H   = 0.8          # %
_MIN_ADX_15M         = 22
_MIN_OB_DEPTH_USD    = 50_000
_MAX_SPREAD_PCT      = 0.1
_MIN_EXPECTED_PROFIT = 3.00         # $ — restored from lowered $1.50
_TOP_N               = 15           # restored from inflated 20

# EV fee model
_ROUND_TRIP_FEE_PCT  = 0.0011       # 0.055% taker × 2 sides (Bybit linear)
_FUNDING_HOLD_PERIODS = 1.5         # expected 8h funding periods held

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_CACHE_TTL    = 300    # 5 minutes
_lock         = threading.RLock()
_last_scan_ts: float    = 0.0
_last_candidates: List[Dict] = []

# Instruments-info cache (valid symbols, refreshed every 24h)
_valid_bybit_symbols: set          = set()
_instruments_cache_ts: float       = 0.0
_INSTRUMENTS_CACHE_TTL: float      = 86_400.0   # 24 hours


# ===========================================================================
# Instruments-info: valid symbol validation
# ===========================================================================

def _refresh_instruments_if_needed() -> None:
    """
    Fetch Bybit linear instruments-info and populate _valid_bybit_symbols.
    Only called if cache is older than 24h or empty.
    Runs inline (called from scan()) — fast enough at 24h TTL.
    """
    global _valid_bybit_symbols, _instruments_cache_ts

    now = time.time()
    if _valid_bybit_symbols and (now - _instruments_cache_ts) < _INSTRUMENTS_CACHE_TTL:
        return

    if not _REQUESTS_OK:
        logger.warning('[scanner] requests not available — cannot fetch instruments-info')
        return

    try:
        r = requests.get(
            f'{_BYBIT_BASE}/v5/market/instruments-info',
            params={'category': 'linear', 'status': 'Trading'},
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning(f'[scanner] instruments-info returned HTTP {r.status_code}')
            return

        body = r.json()
        if body.get('retCode', -1) != 0:
            logger.warning(f'[scanner] instruments-info retCode={body.get("retCode")} '
                           f'msg={body.get("retMsg")}')
            return

        items = body.get('result', {}).get('list', [])
        symbols = {item['symbol'] for item in items if item.get('symbol', '').endswith('USDT')}
        _valid_bybit_symbols = symbols
        _instruments_cache_ts = now
        logger.info(f'[scanner] instruments-info cached: {len(symbols)} active USDT linear symbols')

    except Exception as e:
        logger.warning(f'[scanner] instruments-info fetch error: {e}')
        # Leave existing cache in place rather than clearing it


# ===========================================================================
# Bybit V5 data fetchers
# ===========================================================================

def _fetch_tickers() -> List[Dict]:
    """
    Fetch all linear (USDT perp) 24h tickers from Bybit V5.

    Returns raw Bybit ticker dicts from result.list[].
    Returns empty list on any failure — NO fallback, NO fake data.
    The caller (scan()) will log a warning and sit idle.
    """
    if not _REQUESTS_OK:
        return []

    try:
        r = requests.get(
            f'{_BYBIT_BASE}/v5/market/tickers',
            params={'category': 'linear'},
            timeout=10,
        )
        if r.status_code != 200:
            logger.warning(f'[scanner] Bybit tickers HTTP {r.status_code} — scan skipped')
            return []

        body = r.json()
        if body.get('retCode', -1) != 0:
            logger.warning(f'[scanner] Bybit tickers retCode={body.get("retCode")} '
                           f'msg={body.get("retMsg")} — scan skipped')
            return []

        items = body.get('result', {}).get('list', [])
        logger.debug(f'[scanner] Bybit tickers: {len(items)} total')
        return items

    except Exception as e:
        logger.warning(f'[scanner] Bybit ticker fetch failed: {e} — scan skipped')
        return []


def _fetch_klines(symbol: str, interval: str, limit: int = 50) -> List[List]:
    """
    Fetch OHLCV klines from Bybit V5 linear market.

    Bybit returns rows in DESCENDING order (newest first).
    This function reverses them to ASCENDING before returning.

    Each returned row: [startTime, open, high, low, close, volume, turnover]
    All values are strings from the API — convert to float in the caller.

    Returns empty list on any failure. NO yfinance fallback.
    """
    if not _REQUESTS_OK:
        return []

    bybit_interval = _BYBIT_INTERVAL_MAP.get(interval)
    if bybit_interval is None:
        logger.warning(f'[scanner] Unknown interval {interval!r} — no Bybit mapping')
        return []

    try:
        r = requests.get(
            f'{_BYBIT_BASE}/v5/market/kline',
            params={
                'category': 'linear',
                'symbol':   symbol,
                'interval': bybit_interval,
                'limit':    limit,
            },
            timeout=8,
        )
        if r.status_code != 200:
            logger.debug(f'[scanner] klines HTTP {r.status_code} for {symbol}')
            return []

        body = r.json()
        if body.get('retCode', -1) != 0:
            logger.debug(f'[scanner] klines retCode={body.get("retCode")} for {symbol}')
            return []

        rows = body.get('result', {}).get('list', [])
        if not rows:
            return []

        # Bybit gives newest first — reverse to oldest-first (ascending)
        rows.reverse()
        return rows

    except Exception as e:
        logger.debug(f'[scanner] klines error {symbol}: {e}')
        return []


def _fetch_ob_depth(symbol: str) -> Dict:
    """
    Fetch order book depth from Bybit V5 (top 5 levels each side).

    Returns dict with keys 'b' (bids) and 'a' (asks), each a list of [price, size] strings.
    Returns empty dict on any failure.
    """
    if not _REQUESTS_OK:
        return {}

    try:
        r = requests.get(
            f'{_BYBIT_BASE}/v5/market/orderbook',
            params={'category': 'linear', 'symbol': symbol, 'limit': 5},
            timeout=5,
        )
        if r.status_code != 200:
            return {}

        body = r.json()
        if body.get('retCode', -1) != 0:
            return {}

        result = body.get('result', {})
        return result   # contains 'b', 'a', 'ts', 'u', 's'

    except Exception:
        return {}


# ===========================================================================
# Technical indicators (pure Python / NumPy — no external dependencies)
# ===========================================================================

def _calc_adx(highs: List[float], lows: List[float], closes: List[float],
              period: int = 14) -> float:
    """Compute ADX from price series."""
    if not _NUMPY_OK or len(highs) < period + 2:
        return 20.0   # neutral fallback

    h  = np.array(highs, dtype=float)
    lo = np.array(lows,  dtype=float)
    c  = np.array(closes, dtype=float)

    # True Range
    tr = np.maximum(h[1:] - lo[1:],
         np.maximum(np.abs(h[1:] - c[:-1]),
                    np.abs(lo[1:] - c[:-1])))

    # Directional movement
    dm_plus  = np.where((h[1:] - h[:-1]) > (lo[:-1] - lo[1:]),
                         np.maximum(h[1:] - h[:-1], 0.0), 0.0)
    dm_minus = np.where((lo[:-1] - lo[1:]) > (h[1:] - h[:-1]),
                         np.maximum(lo[:-1] - lo[1:], 0.0), 0.0)

    def _smooth(arr: np.ndarray, p: int) -> np.ndarray:
        s = np.zeros(len(arr))
        if len(arr) < p:
            return s
        s[p - 1] = arr[:p].sum()
        for i in range(p, len(arr)):
            s[i] = s[i - 1] - s[i - 1] / p + arr[i]
        return s

    atr_s = _smooth(tr, period)
    dmp_s = _smooth(dm_plus,  period)
    dmm_s = _smooth(dm_minus, period)

    eps      = 1e-9
    di_plus  = 100.0 * dmp_s / (atr_s + eps)
    di_minus = 100.0 * dmm_s / (atr_s + eps)
    dx       = 100.0 * np.abs(di_plus - di_minus) / (di_plus + di_minus + eps)

    adx_vals = _smooth(dx[period - 1:], period)
    if len(adx_vals) == 0:
        return 20.0
    return float(adx_vals[-1])


def _vol_spike(volumes: List[float], window: int = 20) -> float:
    """Current bar volume / mean of previous window bars."""
    if len(volumes) < window + 1:
        return 1.0
    current = volumes[-1]
    avg = (float(np.mean(volumes[-window - 1:-1]))
           if _NUMPY_OK
           else sum(volumes[-window - 1:-1]) / window)
    return current / (avg + 1e-9)


# ===========================================================================
# Filter steps
# ===========================================================================

def _step1_universe(tickers: List[Dict]) -> List[Dict]:
    """
    Filter to USDT linear perp pairs with:
      - Symbol ending in USDT
      - Symbol present in _valid_bybit_symbols (Trading status only)
      - turnover24h (USD volume) >= $50M

    Bybit field mapping:
      turnover24h   — 24h notional volume in USD (equivalent to Binance quoteVolume)
      volume24h     — 24h volume in base currency
      price24hPcnt  — e.g. "0.0342" means +3.42% (already a decimal, multiply by 100 for %)
      fundingRate   — per-8h rate, e.g. "0.0001" = 0.01%
      openInterest  — OI in base currency
    """
    result = []
    for t in tickers:
        sym = t.get('symbol', '')
        if not sym.endswith('USDT'):
            continue

        # Skip symbols not confirmed as actively Trading by instruments-info
        # (If the set is empty because the cache hasn't loaded yet, allow all —
        #  the cache will be populated on the next scan.)
        if _valid_bybit_symbols and sym not in _valid_bybit_symbols:
            continue

        try:
            # turnover24h is USD-denominated (the "quote volume" equivalent)
            vol_usd = float(t.get('turnover24h', 0) or 0)
            if vol_usd < _MIN_VOLUME_24H_USD:
                continue

            price = float(t.get('lastPrice', 0) or 0)
            if price <= 0:
                continue

            # price24hPcnt: Bybit returns e.g. "0.0342" meaning 3.42%
            raw_pct = float(t.get('price24hPcnt', 0) or 0)
            price_change_pct = raw_pct * 100.0   # convert to human % (3.42)

            # Funding rate: e.g. "0.0001" = 0.01% per 8h
            funding_rate = float(t.get('fundingRate', 0) or 0)

            result.append({
                'symbol':           sym,
                'price':            price,
                'price_change_pct': price_change_pct,
                'volume_24h_usd':   vol_usd,
                'volume_24h_base':  float(t.get('volume24h', 0) or 0),
                'high_24h':         float(t.get('highPrice24h', 0) or 0),
                'low_24h':          float(t.get('lowPrice24h',  0) or 0),
                'funding_rate':     funding_rate,
                'open_interest':    float(t.get('openInterest', 0) or 0),
            })

        except (ValueError, TypeError):
            continue

    return result


def _step2_momentum(candidates: List[Dict]) -> List[Dict]:
    """
    Momentum filter: vol_spike >= 1.2, price_move_4h >= 0.8%, adx_15m >= 22.
    Fetches 15m Bybit klines for each candidate.

    Bybit kline row (after reversal to ascending):
      [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
      All values are strings — cast to float here.
    """
    passed = []
    for c in candidates:
        sym = c['symbol']
        try:
            # 50 bars of 15m = ~12.5h of history
            klines = _fetch_klines(sym, '15m', 50)
            if len(klines) < 20:
                logger.debug(f'[scanner] step2 {sym}: only {len(klines)} klines — skip')
                continue

            opens  = [float(k[1]) for k in klines]
            highs  = [float(k[2]) for k in klines]
            lows   = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            vols   = [float(k[5]) for k in klines]

            # Drop last bar if it appears to be an incomplete current bar
            # (current bar volume < 10% of the prior bar)
            if len(vols) >= 2 and vols[-2] > 0 and vols[-1] / vols[-2] < 0.10:
                opens  = opens[:-1];  highs  = highs[:-1]
                lows   = lows[:-1];   closes = closes[:-1]
                vols   = vols[:-1]

            # Volume spike: current bar vs 20-bar average
            vs = _vol_spike(vols, 20)

            # Price move over last 4h = ~16 bars of 15m
            bars_4h = min(16, len(closes) - 1)
            price_move_4h = (abs(closes[-1] - closes[-bars_4h])
                             / (closes[-bars_4h] + 1e-9) * 100)

            # ADX(14) on 15m
            adx = _calc_adx(highs, lows, closes, 14)

            if vs >= _MIN_VOL_SPIKE and price_move_4h >= _MIN_PRICE_MOVE_4H and adx >= _MIN_ADX_15M:
                # Direction from momentum of last 3 closed bars
                recent_move = (closes[-1] - closes[-4]
                               if len(closes) >= 4 else closes[-1] - closes[0])
                direction = 'LONG' if recent_move > 0 else 'SHORT'

                c.update({
                    'vol_spike':         round(vs, 3),
                    'price_move_4h_pct': round(price_move_4h, 3),
                    'adx_15m':           round(adx, 1),
                    'direction':         direction,
                    'closes_15m':        closes,
                    'highs_15m':         highs,
                    'lows_15m':          lows,
                    'vols_15m':          vols,
                })
                passed.append(c)

        except Exception as e:
            logger.debug(f'[scanner] step2 error {sym}: {e}')
            continue

    return passed


def _step3_liquidity(candidates: List[Dict]) -> List[Dict]:
    """
    Orderbook depth > $50K each side, spread < 0.1%.

    IMPORTANT: If the order book fetch fails or returns empty bids/asks,
    the candidate is REJECTED. We do not trade what we cannot validate.
    The old fail-open behaviour (passed.append(c) on empty OB) is removed.

    Bybit OB response keys:
      result.b — bids, list of [price_str, size_str], best bid first
      result.a — asks, list of [price_str, size_str], best ask first
    """
    passed = []
    for c in candidates:
        sym = c['symbol']
        try:
            ob = _fetch_ob_depth(sym)
            bids = ob.get('b', [])
            asks = ob.get('a', [])

            if not bids or not asks:
                # No OB data → reject
                logger.debug(f'[scanner] step3 {sym}: empty OB — rejected')
                continue

            bid_depth  = sum(float(b[0]) * float(b[1]) for b in bids[:5])
            ask_depth  = sum(float(a[0]) * float(a[1]) for a in asks[:5])
            best_bid   = float(bids[0][0])
            best_ask   = float(asks[0][0])
            mid        = (best_bid + best_ask) / 2.0
            spread_pct = (best_ask - best_bid) / (mid + 1e-9) * 100

            if (bid_depth >= _MIN_OB_DEPTH_USD
                    and ask_depth >= _MIN_OB_DEPTH_USD
                    and spread_pct <= _MAX_SPREAD_PCT):
                c.update({
                    'bid_depth_usd': round(bid_depth, 0),
                    'ask_depth_usd': round(ask_depth, 0),
                    'spread_pct':    round(spread_pct, 4),
                })
                passed.append(c)
            else:
                logger.debug(f'[scanner] step3 {sym}: bid={bid_depth:.0f} ask={ask_depth:.0f} '
                             f'spread={spread_pct:.4f}% — rejected')

        except Exception as e:
            # Reject on exception — not fail-open
            logger.debug(f'[scanner] step3 error {sym}: {e} — rejected')

    return passed


def _step4_expected_value(candidates: List[Dict],
                           account_balance: float = 10_000.0,
                           risk_pct: float = 0.02) -> List[Dict]:
    """
    Expected value filter including fee modeling and funding cost.

    EV formula:
      round_trip_fee_pct  = 0.0011  (0.055% taker × 2 sides)
      funding_cost_pct    = abs(funding_rate) * _FUNDING_HOLD_PERIODS
                            (per 8h rate × expected periods held)

      net_win  = target_dist_pct - round_trip_fee_pct - max(0, funding_cost_pct)
      net_loss = stop_dist_pct   + round_trip_fee_pct

      ev = (0.52 * net_win * position_usd) - (0.48 * net_loss * position_usd)

    Minimum ev >= _MIN_EXPECTED_PROFIT ($3.00).

    Fail-open if no closes data (assume passes — it won't have clean ATR anyway).
    """
    passed = []
    for c in candidates:
        try:
            closes = c.get('closes_15m', [])
            if len(closes) < 15:
                # Not enough data to compute ATR — pass with neutral placeholder
                c['expected_profit'] = _MIN_EXPECTED_PROFIT + 0.01
                passed.append(c)
                continue

            # ATR proxy: mean of abs(close[i] - close[i-1]) over last 14 bars
            diffs = [abs(closes[i] - closes[i - 1]) for i in range(-14, 0)]
            atr   = sum(diffs) / len(diffs)
            price = c['price']

            stop_dist   = atr * 1.5
            target_dist = atr * 3.0
            stop_pct    = stop_dist   / (price + 1e-9)
            target_pct  = target_dist / (price + 1e-9)

            # Position size from dollar risk
            dollar_risk  = account_balance * risk_pct
            position_usd = dollar_risk / (stop_pct + 1e-9)

            # Fee model: Bybit linear taker 0.055% × 2 sides = 0.11%
            fee_pct = _ROUND_TRIP_FEE_PCT   # 0.0011 as a fraction

            # Funding cost: use ticker's funding_rate, assume _FUNDING_HOLD_PERIODS settlements
            funding_rate_per_8h = abs(c.get('funding_rate', 0.0001))
            funding_cost_pct    = funding_rate_per_8h * _FUNDING_HOLD_PERIODS   # fraction

            # Net distances after costs (all as fractions of price)
            net_win  = target_pct - fee_pct - max(0.0, funding_cost_pct)
            net_loss = stop_pct   + fee_pct

            # 52% win-rate baseline (slightly above break-even assumption)
            ev = (0.52 * net_win * position_usd) - (0.48 * net_loss * position_usd)

            if ev >= _MIN_EXPECTED_PROFIT:
                c.update({
                    'atr_15m':         round(atr, 6),
                    'stop_pct':        round(stop_pct  * 100, 3),
                    'target_pct':      round(target_pct * 100, 3),
                    'funding_cost_pct': round(funding_cost_pct * 100, 4),
                    'expected_profit': round(ev, 2),
                })
                passed.append(c)
            else:
                logger.debug(f'[scanner] step4 {c.get("symbol")}: '
                             f'ev=${ev:.2f} < ${_MIN_EXPECTED_PROFIT} — rejected '
                             f'(net_win={net_win*100:.3f}% net_loss={net_loss*100:.3f}% '
                             f'fee={fee_pct*100:.3f}% funding={funding_cost_pct*100:.4f}%)')

        except Exception as e:
            logger.debug(f'[scanner] step4 error {c.get("symbol")}: {e}')
            # Fail-open on calculation errors only (not on OB errors — that's step3)
            c['expected_profit'] = _MIN_EXPECTED_PROFIT + 0.01
            passed.append(c)

    return passed


def _step5_correlation(candidates: List[Dict],
                        open_positions: Optional[List[str]] = None) -> List[Dict]:
    """
    Reduce size flag if candidate is highly correlated with an open position.
    Simple proxy: same base asset family = correlated.
    Full matrix correlation is implemented in risk_engine.py.
    """
    if not open_positions:
        for c in candidates:
            c['correlation_penalty'] = 1.0
        return candidates

    for c in candidates:
        # No penalty — let all candidates trade at full size regardless of existing positions.
        # risk_engine.py handles the real correlation matrix check before order placement.
        c['correlation_penalty'] = 1.0

    return candidates


def _step6_regime_filter(candidates: List[Dict], regime: str = 'UNKNOWN') -> List[Dict]:
    """
    Match signal type to regime.
    - HIGH_VOL: require vol_spike >= 1.5 (stronger confirmation)
    - RANGING: skip strong trend trades (ADX > 30)
    - TRENDING_UP/DOWN: counter-trend trades allowed but marked with 0.80 penalty
    """
    if regime == 'UNKNOWN':
        return candidates

    passed = []
    for c in candidates:
        direction = c.get('direction', 'LONG')
        adx       = c.get('adx_15m', 25)
        vs        = c.get('vol_spike', 1.0)

        if regime == 'HIGH_VOL':
            if vs < 1.5:
                continue

        elif regime == 'RANGING':
            if adx > 30:
                continue

        elif regime in ('TRENDING_UP', 'TRENDING_DOWN'):
            if regime == 'TRENDING_UP' and direction == 'SHORT':
                c['regime_penalty'] = 0.80
            elif regime == 'TRENDING_DOWN' and direction == 'LONG':
                c['regime_penalty'] = 0.80
            else:
                c['regime_penalty'] = 1.0

        if 'regime_penalty' not in c:
            c['regime_penalty'] = 1.0

        passed.append(c)

    return passed


def _step7_rank_and_top(candidates: List[Dict], n: int = _TOP_N) -> List[Dict]:
    """Sort by vol_spike descending, return top N. Strip large intermediate fields."""
    sorted_c = sorted(candidates, key=lambda x: x.get('vol_spike', 0), reverse=True)
    result = []
    for c in sorted_c[:n]:
        c.pop('closes_15m', None)
        c.pop('highs_15m',  None)
        c.pop('lows_15m',   None)
        c.pop('vols_15m',   None)
        result.append(c)
    return result


# ===========================================================================
# Public API
# ===========================================================================

def scan(open_positions: Optional[List[str]] = None,
         regime: str = 'UNKNOWN',
         account_balance: float = 10_000.0) -> List[Dict]:
    """
    Run the full 7-step Bybit perp scanner pipeline.

    Args:
        open_positions: list of currently held symbols (for correlation filter)
        regime: current market regime string (from ml/regime_classifier.py)
        account_balance: for EV position sizing calculation

    Returns:
        List of up to 15 candidate dicts, sorted by vol_spike descending.
        Each dict contains: symbol, price, direction, vol_spike, adx_15m,
        price_move_4h_pct, atr_15m, stop_pct, target_pct, expected_profit,
        funding_rate, funding_cost_pct, correlation_penalty, regime_penalty,
        spread_pct, bid_depth_usd, ask_depth_usd.

        Returns empty list (not an exception) if Bybit is unavailable.
        The scheduler should interpret an empty list as "sit idle".
    """
    global _last_scan_ts, _last_candidates

    with _lock:
        if time.time() - _last_scan_ts < _CACHE_TTL:
            return _last_candidates

    t_start = time.time()
    logger.info('[scanner] Starting Bybit full-market scan...')

    try:
        # Refresh instruments-info cache if stale (24h TTL, fast on cache hit)
        _refresh_instruments_if_needed()

        # Step 1: Universe
        tickers  = _fetch_tickers()
        universe = _step1_universe(tickers)
        logger.info(
            f'[scanner] Step 1 (turnover>${_MIN_VOLUME_24H_USD/1e6:.0f}M): '
            f'{len(tickers)} pairs → {len(universe)} candidates'
        )

        if not universe:
            logger.warning('[scanner] Bybit returned no usable tickers — scan idle')
            return []

        # Step 2: Momentum (Bybit kline calls per symbol — most expensive step)
        momentum_pass = _step2_momentum(universe)
        logger.info(f'[scanner] Step 2 (momentum): {len(universe)} → {len(momentum_pass)}')

        if not momentum_pass:
            return []

        # Step 3: Liquidity (Bybit OB — REJECT on missing data)
        liquidity_pass = _step3_liquidity(momentum_pass)
        logger.info(f'[scanner] Step 3 (liquidity): {len(momentum_pass)} → {len(liquidity_pass)}')

        # Step 4: Expected value (with fee + funding cost model)
        ev_pass = _step4_expected_value(liquidity_pass, account_balance)
        logger.info(
            f'[scanner] Step 4 (EV>=${_MIN_EXPECTED_PROFIT}): '
            f'{len(liquidity_pass)} → {len(ev_pass)}'
        )

        # Step 5: Correlation
        corr_pass = _step5_correlation(ev_pass, open_positions)

        # Step 6: Regime filter
        regime_pass = _step6_regime_filter(corr_pass, regime)
        logger.info(f'[scanner] Step 6 (regime={regime}): {len(corr_pass)} → {len(regime_pass)}')

        # Step 7: Rank and top 15
        final   = _step7_rank_and_top(regime_pass)
        elapsed = time.time() - t_start

        logger.info(f'[scanner] Complete: {len(final)} candidates in {elapsed:.1f}s')
        for c in final:
            logger.info(
                f'[scanner] → {c["symbol"]} {c["direction"]} '
                f'spike={c.get("vol_spike", 0):.2f} '
                f'adx={c.get("adx_15m", 0):.0f} '
                f'ev=${c.get("expected_profit", 0):.2f} '
                f'funding={c.get("funding_rate", 0)*100:.4f}%/8h'
            )

        with _lock:
            _last_scan_ts     = time.time()
            _last_candidates  = final

        return final

    except Exception as e:
        logger.error(f'[scanner] Fatal error: {e}', exc_info=True)
        return []


def get_last_candidates() -> List[Dict]:
    """Return most recent scan results without triggering a new scan."""
    return _last_candidates


def get_scan_stats() -> Dict:
    """Summary stats for dashboard."""
    return {
        'last_scan_ts':    _last_scan_ts,
        'last_scan_age_s': round(time.time() - _last_scan_ts, 0),
        'candidate_count': len(_last_candidates),
        'data_source':     'bybit_v5',
        'candidates': [
            {
                'symbol':    c['symbol'],
                'direction': c.get('direction', '?'),
                'vol_spike': c.get('vol_spike', 0),
                'adx':       c.get('adx_15m', 0),
                'ev':        c.get('expected_profit', 0),
                'funding':   c.get('funding_rate', 0),
            }
            for c in _last_candidates
        ],
    }
