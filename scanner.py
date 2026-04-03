"""
scanner.py — Unified Kraken Futures linear perp scanner.

7-step filter on all Kraken PF_ (USD-settled linear) perpetuals, runs every
5 minutes, 24/7. Returns top 15 candidates with direction and signal scores.

Data sources:
  ALL market data from Kraken Futures public REST API — US-accessible, no auth
  required for any endpoint used here. Replaces Bybit V5 (geo-blocked after
  pybit removal) and Binance fapi (HTTP 451 hard geo-block from US).

Why Kraken:
  - No US geo-block on public endpoints
  - No API key required for tickers / klines / order book
  - pip install NOT required (uses stdlib urllib only)
  - 21 liquid PF_ perps (BTC, ETH, SOL, XRP, DOGE, ADA, BNB, SUI, AVAX, …)

Kraken Futures symbol convention:
  PF_ prefix = USD-settled (cash-settled) linear perpetual
  PF_XBTUSD  = Bitcoin (Kraken uses XBT, not BTC)
  PF_ETHUSD  = Ethereum
  All others: PF_{TICKER}USD

Filter pipeline:
  1. Universe: all PF_ perps from /tickers, filter volumeQuote > $5M, not suspended
  2. Momentum: vol_spike >= 1.2 AND abs(price_move_1h) >= 0.5% AND adx_15m >= 20
  3. Liquidity: OB depth > $30K each side, spread < 0.15%
     REJECT on missing or empty OB data — no fail-open
  4. Expected value: expected_profit >= $2.00 after fees + funding cost
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
    import urllib.request as _urllib
    import json as _json
    _HTTP_OK = True
except ImportError:
    _HTTP_OK = False

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

# ---------------------------------------------------------------------------
# Kraken Futures REST base URLs — both are US-accessible with no API key
# ---------------------------------------------------------------------------
_KRAKEN_BASE   = 'https://futures.kraken.com/derivatives/api/v3'
_KRAKEN_CHARTS = 'https://futures.kraken.com/api/charts/v1'

# ---------------------------------------------------------------------------
# Kraken Futures interval labels (used directly in the URL path)
# ---------------------------------------------------------------------------
_KRAKEN_INTERVALS = {'1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'}

# ---------------------------------------------------------------------------
# Filter thresholds
# ---------------------------------------------------------------------------
_MIN_VOLUME_24H_USD  = 5_000_000    # volumeQuote (USD) — Kraken is smaller than Bybit
_MIN_VOL_SPIKE       = 0.4           # Kraken is smaller exchange — 0.4× avg is sufficient signal
_MIN_PRICE_MOVE_1H   = 0.15         # % — 4 bars of 15m (Bybit was 0.5%, Kraken quieter)
_MIN_ADX_15M         = 15
_MIN_OB_DEPTH_USD    = 10_000             # $10K each side — $3K was at/below position size, slippage exceeded spread assumption in economics gate
_MAX_SPREAD_PCT      = 0.15
_MIN_EXPECTED_PROFIT = 0.50         # $ — Kraken positions are smaller; $0.50 min positive EV
_TOP_N               = 15

# EV fee model — Kraken taker 0.065% × 2 sides = 0.13%
_ROUND_TRIP_FEE_PCT   = 0.00130
_FUNDING_HOLD_PERIODS = 1.5         # expected 8h-equivalent funding periods held

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_CACHE_TTL            = 300    # 5 minutes
_lock                 = threading.RLock()
_last_scan_ts: float  = 0.0
_last_candidates: List[Dict] = []


# ===========================================================================
# HTTP helper
# ===========================================================================

def _get(url: str, timeout: int = 10) -> Optional[Dict]:
    """
    Minimal HTTP GET returning a parsed JSON dict, or None on failure.
    Uses stdlib urllib — no requests dependency.
    """
    if not _HTTP_OK:
        return None
    try:
        req = _urllib.Request(url, headers={'User-Agent': 'AlgoBot/1.0'})
        with _urllib.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.debug(f'[scanner] GET {url!r} failed: {e}')
        return None


# ===========================================================================
# Kraken Futures data fetchers
# ===========================================================================

def _fetch_tickers() -> List[Dict]:
    """
    Fetch all Kraken Futures tickers.

    Returns raw ticker dicts from result.tickers[].
    Returns empty list on any failure — no fallback, no fake data.

    Key fields used downstream:
      symbol         — e.g. 'PF_XBTUSD'
      tag            — 'perpetual' for perps
      suspended      — bool, skip if True
      last           — last traded price
      volumeQuote    — 24h USD volume (base * price)
      fundingRate    — per-period funding rate (decimal)
      change24h      — 24h price change as a percentage (e.g. -1.59 means -1.59%)
      bid / ask      — best bid/ask price (for spread check from ticker)
      openInterest   — OI in base currency
    """
    data = _get(f'{_KRAKEN_BASE}/tickers', timeout=10)
    if data is None:
        logger.warning('[scanner] Kraken tickers fetch failed — scan skipped')
        return []

    tickers = data.get('tickers', [])
    logger.debug(f'[scanner] Kraken raw tickers: {len(tickers)}')
    return tickers


def _fetch_klines(symbol: str, interval: str, n_bars: int = 50) -> List[Dict]:
    """
    Fetch OHLCV klines from Kraken Futures charts API.

    URL: GET /api/charts/v1/trade/{symbol}/{interval}?from={unix_ts}
    Returns candles in ASCENDING order (oldest first) — no reversal needed.

    Each candle dict: {time (ms), open, high, low, close, volume} — strings.
    Convert to float in the caller.

    Args:
        symbol:   Kraken PF_ symbol, e.g. 'PF_XBTUSD'
        interval: '1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'
        n_bars:   how many bars of history to request (determines 'from' timestamp)

    Returns empty list on failure. No fallback.
    """
    if interval not in _KRAKEN_INTERVALS:
        logger.warning(f'[scanner] Unknown interval {interval!r}')
        return []

    # Compute seconds per bar to determine 'from' timestamp
    _bar_seconds = {
        '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
        '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800,
    }
    bar_secs = _bar_seconds.get(interval, 900)
    from_ts  = int(time.time()) - (n_bars + 5) * bar_secs  # +5 buffer for partial bar

    url  = f'{_KRAKEN_CHARTS}/trade/{symbol}/{interval}?from={from_ts}'
    data = _get(url, timeout=8)
    if data is None:
        return []

    candles = data.get('candles', [])
    return candles   # already ascending (oldest first)


def _fetch_ob_depth(symbol: str) -> Dict:
    """
    Fetch order book from Kraken Futures.

    URL: GET /derivatives/api/v3/orderbook?symbol={symbol}
    Response: {orderBook: {bids: [[price, qty], ...], asks: [[price, qty], ...]}}

    IMPORTANT — Kraken OB sort order:
      bids: ASCENDING by price → best bid  = bids[-1]
      asks: ASCENDING by price → best ask  = asks[0]

    Returns dict with 'bids' and 'asks' lists, or empty dict on failure.
    """
    url  = f'{_KRAKEN_BASE}/orderbook?symbol={symbol}'
    data = _get(url, timeout=5)
    if data is None:
        return {}

    ob = data.get('orderBook', {})
    return ob   # keys: 'bids', 'asks'


# ===========================================================================
# Technical indicators (pure Python / NumPy — no external dependencies)
# ===========================================================================

def _calc_adx(highs: List[float], lows: List[float], closes: List[float],
              period: int = 14) -> float:
    """Compute ADX from price series. Returns 20.0 (neutral) on insufficient data."""
    if not _NUMPY_OK or len(highs) < period + 2:
        return 20.0

    h  = np.array(highs,  dtype=float)
    lo = np.array(lows,   dtype=float)
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

    atr_s = _smooth(tr,       period)
    dmp_s = _smooth(dm_plus,  period)
    dmm_s = _smooth(dm_minus, period)

    eps      = 1e-9
    di_plus  = 100.0 * dmp_s / (atr_s + eps)
    di_minus = 100.0 * dmm_s / (atr_s + eps)
    dx       = 100.0 * np.abs(di_plus - di_minus) / (di_plus + di_minus + eps)

    # ADX seed = mean of first 14 DX values (Wilder's definition), not raw sum
    dx_slice = dx[period - 1:]
    if len(dx_slice) < period:
        return 20.0
    adx_s = np.zeros(len(dx_slice))
    adx_s[period - 1] = dx_slice[:period].mean()
    for i in range(period, len(dx_slice)):
        adx_s[i] = adx_s[i - 1] - adx_s[i - 1] / period + dx_slice[i]
    val = adx_s[-1]
    return float(min(val, 100.0))  # ADX is always 0-100


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
    Filter to active PF_ (linear USD-settled) perpetuals with volumeQuote >= $5M.

    Kraken field mapping:
      tag           — 'perpetual' for perps (also 'inverse_perpetual' for PI_ series)
      suspended     — skip if True
      last          — last traded price (float or int)
      volumeQuote   — 24h USD volume (base_vol * price) — this is what we want
      change24h     — 24h price change in PERCENT (e.g. -1.59 means -1.59%)
      fundingRate   — current per-period funding rate as a decimal
                      (treat as fractional, not percentage, for EV math)
      openInterest  — OI in base currency
      bid / ask     — best bid/ask from ticker snapshot
    """
    result = []
    for t in tickers:
        sym = t.get('symbol', '')
        # Only PF_ (USD-settled linear perpetuals)
        if not sym.startswith('PF_'):
            continue
        # Skip inverse perpetuals (PI_) and futures (FI_, FF_)
        if t.get('tag') != 'perpetual':
            continue
        # Skip suspended
        if t.get('suspended', False):
            continue

        try:
            vol_usd = float(t.get('volumeQuote', 0) or 0)
            if vol_usd < _MIN_VOLUME_24H_USD:
                continue

            price = float(t.get('last', 0) or 0)
            if price <= 0:
                continue

            # change24h is already in percent (e.g. -1.59 means -1.59%)
            price_change_pct = float(t.get('change24h', 0) or 0)

            # fundingRate is a decimal fraction (not percent) — treat accordingly
            funding_rate = float(t.get('fundingRate', 0) or 0)

            result.append({
                'symbol':           sym,
                'price':            price,
                'price_change_pct': price_change_pct,
                'volume_24h_usd':   vol_usd,
                'high_24h':         float(t.get('high24h', 0) or 0),
                'low_24h':          float(t.get('low24h',  0) or 0),
                'funding_rate':     funding_rate,
                'open_interest':    float(t.get('openInterest', 0) or 0),
                'bid':              float(t.get('bid', price) or price),
                'ask':              float(t.get('ask', price) or price),
            })

        except (ValueError, TypeError):
            continue

    return result


def _step2_momentum(candidates: List[Dict]) -> List[Dict]:
    """
    Momentum filter: vol_spike >= 1.2, abs(price_move_1h) >= 0.5%, adx_15m >= 20.
    Fetches 15m Kraken klines for each candidate.

    Kraken kline dict (ascending, oldest first):
      {time (ms), open, high, low, close, volume} — all strings, cast to float here.
    """
    passed = []
    for c in candidates:
        sym = c['symbol']
        try:
            # 55 bars of 15m ≈ 13.75h of history (gives enough for ADX(14))
            klines = _fetch_klines(sym, '15m', 55)
            if len(klines) < 20:
                logger.debug(f'[scanner] step2 {sym}: only {len(klines)} klines — skip')
                continue

            opens  = [float(k['open'])   for k in klines]
            highs  = [float(k['high'])   for k in klines]
            lows   = [float(k['low'])    for k in klines]
            closes = [float(k['close'])  for k in klines]
            vols   = [float(k['volume']) for k in klines]

            # Drop last bar if it's an incomplete current bar
            # (current bar volume < 10% of the prior bar)
            if len(vols) >= 2 and vols[-2] > 0 and vols[-1] / vols[-2] < 0.10:
                opens  = opens[:-1];  highs  = highs[:-1]
                lows   = lows[:-1];   closes = closes[:-1]
                vols   = vols[:-1]

            if len(closes) < 10:
                continue

            # Volume spike: current bar vs 20-bar average
            vs = _vol_spike(vols, 20)

            # Price move over last 1h = 4 bars of 15m
            bars_1h       = min(4, len(closes) - 1)
            price_move_1h = (abs(closes[-1] - closes[-bars_1h])
                             / (closes[-bars_1h] + 1e-9) * 100)

            # ADX(14) on 15m
            adx = _calc_adx(highs, lows, closes, 14)

            # Pass if EITHER vol spike OR price move shows activity (plus ADX for trend)
            activity = (vs >= _MIN_VOL_SPIKE) or (price_move_1h >= _MIN_PRICE_MOVE_1H)
            if activity and adx >= _MIN_ADX_15M:
                # Direction from momentum of last 3 closed bars
                recent_move = (closes[-1] - closes[-4]
                               if len(closes) >= 4 else closes[-1] - closes[0])
                direction = 'LONG' if recent_move > 0 else 'SHORT'

                c.update({
                    'vol_spike':          round(vs, 3),
                    'price_move_1h_pct':  round(price_move_1h, 3),
                    'adx_15m':            round(adx, 1),
                    'direction':          direction,
                    'closes_15m':         closes,
                    'highs_15m':          highs,
                    'lows_15m':           lows,
                    'vols_15m':           vols,
                })
                passed.append(c)

        except Exception as e:
            logger.debug(f'[scanner] step2 error {sym}: {e}')
            continue

    return passed


def _step3_liquidity(candidates: List[Dict]) -> List[Dict]:
    """
    Orderbook depth > $30K each side, spread < 0.15%.

    IMPORTANT: If the order book fetch fails or returns empty bids/asks,
    the candidate is REJECTED. We do not trade what we cannot validate.

    Kraken OB format: bids/asks are [[price, qty], ...] sorted ASCENDING.
      Best bid = bids[-1]  (highest bid price)
      Best ask = asks[0]   (lowest ask price)
    """
    passed = []
    for c in candidates:
        sym = c['symbol']
        try:
            ob   = _fetch_ob_depth(sym)
            bids = ob.get('bids', [])
            asks = ob.get('asks', [])

            if not bids or not asks:
                # No OB data → reject (fail-closed)
                logger.debug(f'[scanner] step3 {sym}: empty OB — rejected')
                continue

            # Depth: sum price * qty across top 10 levels each side
            # Bids ascending → last entries are near-market depth
            near_bids   = bids[-10:]  # closest 10 bid levels
            bid_depth   = sum(float(b[0]) * float(b[1]) for b in near_bids)
            ask_depth   = sum(float(a[0]) * float(a[1]) for a in asks[:10])

            best_bid    = float(bids[-1][0])
            best_ask    = float(asks[0][0])
            mid         = (best_bid + best_ask) / 2.0
            spread_pct  = (best_ask - best_bid) / (mid + 1e-9) * 100

            if (bid_depth  >= _MIN_OB_DEPTH_USD
                    and ask_depth  >= _MIN_OB_DEPTH_USD
                    and spread_pct <= _MAX_SPREAD_PCT):
                c.update({
                    'bid_depth_usd': round(bid_depth,  0),
                    'ask_depth_usd': round(ask_depth,  0),
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
                           account_balance: float = 5_000.0,
                           risk_pct: float = 0.015) -> List[Dict]:
    """
    Expected value filter including fee modeling and funding cost.

    EV formula:
      round_trip_fee_pct  = 0.00130  (0.065% Kraken taker × 2 sides)
      funding_cost        = abs(funding_rate) * _FUNDING_HOLD_PERIODS
                            (funding_rate is a decimal fraction)

      net_win  = target_dist_pct - round_trip_fee_pct - max(0, funding_cost)
      net_loss = stop_dist_pct   + round_trip_fee_pct

      ev = (0.52 * net_win * position_usd) - (0.48 * net_loss * position_usd)

    Minimum ev >= _MIN_EXPECTED_PROFIT ($2.00).
    Fail-open on ATR calculation error (the OB liquidity gate already validated).
    """
    passed = []
    for c in candidates:
        try:
            closes = c.get('closes_15m', [])
            if len(closes) < 15:
                # Not enough history — reject; fake EV is not a valid entry basis
                logger.debug(f'[scanner] step4 {c.get("symbol")}: only {len(closes)} bars — rejected (need ≥15)')
                continue

            # ATR proxy: mean |close[i] - close[i-1]| over last 14 bars
            diffs   = [abs(closes[i] - closes[i - 1]) for i in range(-14, 0)]
            atr     = sum(diffs) / len(diffs)
            price   = c['price']

            stop_dist   = atr * 1.5
            target_dist = atr * 3.0
            stop_pct    = stop_dist   / (price + 1e-9)
            target_pct  = target_dist / (price + 1e-9)

            # Position size from dollar risk
            dollar_risk  = account_balance * risk_pct
            position_usd = dollar_risk / (stop_pct + 1e-9)

            # Fee model: Kraken taker 0.065% × 2 sides = 0.13%
            fee_pct = _ROUND_TRIP_FEE_PCT

            # Funding cost: Kraken fundingRate is ANNUALIZED as a decimal fraction
            # (e.g. -0.56 = -56%/year). Convert to per-8h cost:
            #   / (365 * 3)  →  per-8h rate  ×  expected hold periods
            annualized_rate = abs(c.get('funding_rate', 0.0))
            funding_cost = (annualized_rate / (365 * 3)) * _FUNDING_HOLD_PERIODS

            # Net distances after costs (fractions of price)
            net_win  = target_pct - fee_pct - max(0.0, funding_cost)
            net_loss = stop_pct   + fee_pct

            # 52% win-rate baseline (conservative assumption)
            ev = (0.52 * net_win * position_usd) - (0.48 * net_loss * position_usd)

            if ev >= _MIN_EXPECTED_PROFIT:
                c.update({
                    'atr_15m':          round(atr, 6),
                    'stop_pct':         round(stop_pct   * 100, 3),
                    'target_pct':       round(target_pct * 100, 3),
                    'funding_cost_pct': round(funding_cost * 100, 4),
                    'expected_profit':  round(ev, 2),
                })
                passed.append(c)
            else:
                logger.debug(f'[scanner] step4 {c.get("symbol")}: '
                             f'ev=${ev:.2f} < ${_MIN_EXPECTED_PROFIT} — rejected '
                             f'(net_win={net_win*100:.3f}% net_loss={net_loss*100:.3f}% '
                             f'fee={fee_pct*100:.3f}% funding={funding_cost*100:.4f}%)')

        except Exception as e:
            logger.debug(f'[scanner] step4 error {c.get("symbol")}: {e} — rejected (EV calc failed)')
            # Reject on exception; auto-approving with fake EV would bypass the economics gate

    return passed


def _step5_correlation(candidates: List[Dict],
                        open_positions: Optional[List[str]] = None) -> List[Dict]:
    """
    Flag candidates correlated with open positions.
    Full matrix correlation is handled by risk_engine.py before order placement.
    """
    for c in candidates:
        c['correlation_penalty'] = 1.0
    return candidates


def _step6_regime_filter(candidates: List[Dict], regime: str = 'UNKNOWN') -> List[Dict]:
    """
    Match signal type to regime.
    - HIGH_VOL: require vol_spike >= 1.5
    - RANGING: skip strong trend trades (ADX > 30)
    - TRENDING_UP/DOWN: counter-trend marked with 0.80 penalty
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
         account_balance: float = 5_000.0) -> List[Dict]:
    """
    Run the full 7-step Kraken Futures perp scanner pipeline.

    Args:
        open_positions: list of currently held symbols (for correlation filter)
        regime: current market regime string (from ml/regime_classifier.py)
        account_balance: for EV position sizing calculation

    Returns:
        List of up to 15 candidate dicts, sorted by vol_spike descending.
        Each dict contains: symbol, price, direction, vol_spike, adx_15m,
        price_move_1h_pct, atr_15m, stop_pct, target_pct, expected_profit,
        funding_rate, funding_cost_pct, correlation_penalty, regime_penalty,
        spread_pct, bid_depth_usd, ask_depth_usd.

        Returns empty list (not an exception) if Kraken is unavailable.
        The scheduler will interpret an empty list as "sit idle this cycle".
    """
    global _last_scan_ts, _last_candidates

    with _lock:
        if time.time() - _last_scan_ts < _CACHE_TTL:
            return _last_candidates

    t_start = time.time()
    logger.info('[scanner] Starting Kraken Futures full-market scan...')

    try:
        # Step 1: Universe — PF_ perps with volumeQuote > $5M
        tickers  = _fetch_tickers()
        universe = _step1_universe(tickers)
        logger.info(
            f'[scanner] Step 1 (volumeQuote>${_MIN_VOLUME_24H_USD/1e6:.0f}M): '
            f'{len(tickers)} tickers → {len(universe)} candidates'
        )

        if not universe:
            logger.warning('[scanner] Kraken returned no usable tickers — scan idle')
            return []

        # Step 2: Momentum (kline calls per symbol — most expensive step)
        momentum_pass = _step2_momentum(universe)
        logger.info(f'[scanner] Step 2 (momentum): {len(universe)} → {len(momentum_pass)}')

        if not momentum_pass:
            return []

        # Step 3: Liquidity (OB — REJECT on missing data)
        liquidity_pass = _step3_liquidity(momentum_pass)
        logger.info(f'[scanner] Step 3 (liquidity): {len(momentum_pass)} → {len(liquidity_pass)}')

        # Step 4: Expected value (fee + funding cost model)
        ev_pass = _step4_expected_value(liquidity_pass, account_balance)
        logger.info(
            f'[scanner] Step 4 (EV>=${_MIN_EXPECTED_PROFIT}): '
            f'{len(liquidity_pass)} → {len(ev_pass)}'
        )

        # Step 5: Correlation (always passes; real check in risk_engine)
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
                f'funding={c.get("funding_rate", 0)*100:.4f}%'
            )

        with _lock:
            _last_scan_ts    = time.time()
            _last_candidates = final

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
        'data_source':     'kraken_futures',
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
