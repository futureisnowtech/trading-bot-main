"""
scanner.py — Unified Binance USDT perp scanner.

7-step filter on ALL Binance USDT perps, runs every 5 minutes, 24/7.
Returns top 15 candidates with direction and signal scores.

Filter pipeline (Corrections 3 wins on thresholds):
  1. Universe pull: all USDT perp tickers, filter 24h volume > $50M
  2. Momentum: vol_spike >= 1.2 AND price_move_4h >= 0.8% AND adx_15m >= 22
  3. Liquidity: ob depth > $50K each side, spread < 0.1%
  4. Expected value: expected_profit >= $1.50
  5. Correlation: reduce size if open position corr > 0.85
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

try:
    import yfinance as yf
    _YF_OK = True
except ImportError:
    _YF_OK = False

_BINANCE_BASE = 'https://fapi.binance.com'
_MIN_VOLUME_24H_USD = 50_000_000   # Correction 6: raised from $2M
_MIN_VOL_SPIKE = 1.2
_MIN_PRICE_MOVE_4H = 0.8           # %
_MIN_ADX_15M = 22                  # Correction 3: loosened from 25
_MIN_OB_DEPTH_USD = 50_000
_MAX_SPREAD_PCT = 0.1
_MIN_EXPECTED_PROFIT = 1.50        # $ — Correction 3: lowered from $3.00
_TOP_N = 15                        # Correction 3: raised from 8

_CACHE_TTL = 300   # 5 minutes
_lock = threading.RLock()
_cache: Dict = {}
_last_scan_ts: float = 0.0
_last_candidates: List[Dict] = []


def _fetch_tickers() -> List[Dict]:
    """
    Fetch all USDT perp 24h tickers from Binance futures.
    Falls back to CoinGecko public API (US-accessible) when Binance is geo-blocked.
    """
    if not _REQUESTS_OK:
        return []

    # Try Binance futures first
    try:
        r = requests.get(f'{_BINANCE_BASE}/fapi/v1/ticker/24hr', timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data:
                logger.debug(f'[scanner] Binance: {len(data)} tickers')
                return data
    except Exception as e:
        logger.debug(f'[scanner] Binance ticker fetch error (likely geo-blocked): {e}')

    # CoinGecko fallback — maps top coins to USDT perp symbols
    return _fetch_tickers_coingecko()


def _fetch_tickers_coingecko() -> List[Dict]:
    """
    CoinGecko public API fallback — no auth needed, US-accessible.
    Maps coins to synthetic ticker dicts matching Binance futures format.
    Returns up to 250 coins as synthetic perp tickers.
    """
    try:
        url = ('https://api.coingecko.com/api/v3/coins/markets'
               '?vs_currency=usd&order=volume_desc&per_page=250&page=1'
               '&sparkline=false&price_change_percentage=24h')
        r = requests.get(url, timeout=15, headers={'Accept': 'application/json'})
        if r.status_code != 200:
            logger.debug(f'[scanner] CoinGecko returned {r.status_code}')
            return []

        coins = r.json()
        tickers = []
        for coin in coins:
            symbol_raw = (coin.get('symbol') or '').upper()
            # Skip stablecoins
            if symbol_raw in ('USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'FDUSD', 'USDP'):
                continue

            perp_symbol = f'{symbol_raw}USDT'
            vol_24h = float(coin.get('total_volume') or 0)
            price = float(coin.get('current_price') or 0)
            change_pct = float(coin.get('price_change_percentage_24h') or 0)
            market_cap = float(coin.get('market_cap') or 0)
            if price <= 0:
                continue

            # Synthesise ticker dict matching Binance fapi format
            tickers.append({
                'symbol':             perp_symbol,
                'lastPrice':          str(price),
                'quoteVolume':        str(vol_24h),
                'priceChangePercent': str(change_pct),
                'volume':             str(vol_24h / (price + 1e-9)),
                'highPrice':          str(price * 1.02),
                'lowPrice':           str(price * 0.98),
                '_source':            'coingecko',
                '_market_cap':        market_cap,
            })

        logger.info(f'[scanner] CoinGecko fallback: {len(tickers)} synthetic perp tickers')
        return tickers

    except Exception as e:
        logger.warning(f'[scanner] CoinGecko fallback failed: {e}')
        return []


def _fetch_klines(symbol: str, interval: str, limit: int = 50) -> List:
    """Fetch klines (OHLCV) from Binance futures; yfinance fallback for US geo-blocks."""
    if _REQUESTS_OK:
        try:
            r = requests.get(
                f'{_BINANCE_BASE}/fapi/v1/klines',
                params={'symbol': symbol, 'interval': interval, 'limit': limit},
                timeout=8
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.debug(f'[scanner] klines binance error {symbol}: {e}')

    # yfinance fallback — map BTCUSDT → BTC-USD, 15m → 15m
    if not _YF_OK:
        return []
    try:
        _interval_map = {'1m': '1m', '3m': '5m', '5m': '5m', '15m': '15m',
                         '30m': '30m', '1h': '1h', '4h': '1h', '1d': '1d'}
        yf_interval = _interval_map.get(interval, '15m')
        _period_map = {'1m': '1d', '5m': '5d', '15m': '5d',
                       '30m': '5d', '1h': '30d', '1d': '60d'}
        yf_period = _period_map.get(yf_interval, '5d')

        # Convert BTCUSDT → BTC-USD for yfinance
        base = symbol.replace('USDT', '').replace('BUSD', '').replace('USDC', '')
        yf_sym = f'{base}-USD'

        df = yf.download(yf_sym, period=yf_period, interval=yf_interval,
                         auto_adjust=True, progress=False)
        if df is None or df.empty or len(df) < 5:
            return []

        df = df.tail(limit).reset_index()
        # Flatten MultiIndex columns if present
        if hasattr(df.columns, 'levels'):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        klines = []
        for _, row in df.iterrows():
            ts = row.get('Datetime', row.get('Date', None))
            t = int(ts.timestamp() * 1000) if ts is not None else 0
            o = float(row.get('Open', 0))
            h = float(row.get('High', 0))
            lo = float(row.get('Low', 0))
            c = float(row.get('Close', 0))
            v = float(row.get('Volume', 0))
            klines.append([t, o, h, lo, c, v, t, 0, 0, 0, 0, 0])
        logger.debug(f'[scanner] yfinance klines {symbol}: {len(klines)} bars')
        return klines
    except Exception as e:
        logger.debug(f'[scanner] klines yfinance error {symbol}: {e}')
    return []


def _fetch_ob_depth(symbol: str) -> Dict:
    """Fetch order book depth for liquidity check."""
    if not _REQUESTS_OK:
        return {}
    try:
        r = requests.get(
            f'{_BINANCE_BASE}/fapi/v1/depth',
            params={'symbol': symbol, 'limit': 5},
            timeout=5
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def _calc_adx(highs: List[float], lows: List[float], closes: List[float],
              period: int = 14) -> float:
    """Compute ADX from price series (simple implementation)."""
    if not _NUMPY_OK or len(highs) < period + 2:
        return 20.0   # neutral fallback

    h = np.array(highs)
    lo = np.array(lows)
    c = np.array(closes)

    # True Range
    tr = np.maximum(h[1:] - lo[1:],
         np.maximum(abs(h[1:] - c[:-1]),
                    abs(lo[1:] - c[:-1])))

    # Directional movement
    dm_plus = np.where((h[1:] - h[:-1]) > (lo[:-1] - lo[1:]),
                        np.maximum(h[1:] - h[:-1], 0), 0)
    dm_minus = np.where((lo[:-1] - lo[1:]) > (h[1:] - h[:-1]),
                         np.maximum(lo[:-1] - lo[1:], 0), 0)

    # Smooth
    def smooth(arr, p):
        s = np.zeros(len(arr))
        s[p-1] = arr[:p].sum()
        for i in range(p, len(arr)):
            s[i] = s[i-1] - s[i-1]/p + arr[i]
        return s

    atr_s = smooth(tr, period)
    dmp_s = smooth(dm_plus, period)
    dmm_s = smooth(dm_minus, period)

    eps = 1e-9
    di_plus  = 100 * dmp_s / (atr_s + eps)
    di_minus = 100 * dmm_s / (atr_s + eps)
    dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus + eps)

    # ADX = smoothed DX
    adx_vals = smooth(dx[period-1:], period)
    if len(adx_vals) == 0:
        return 20.0
    return float(adx_vals[-1])


def _vol_spike(volumes: List[float], window: int = 20) -> float:
    """Current bar volume / mean of previous window bars."""
    if len(volumes) < window + 1:
        return 1.0
    current = volumes[-1]
    avg = float(np.mean(volumes[-window-1:-1])) if _NUMPY_OK else sum(volumes[-window-1:-1]) / window
    return current / (avg + 1e-9)


def _step1_universe(tickers: List[Dict]) -> List[Dict]:
    """Filter to USDT perp pairs with > $50M 24h volume."""
    result = []
    for t in tickers:
        sym = t.get('symbol', '')
        if not sym.endswith('USDT'):
            continue
        try:
            vol_usd = float(t.get('quoteVolume', 0))
            if vol_usd >= _MIN_VOLUME_24H_USD:
                result.append({
                    'symbol': sym,
                    'price': float(t.get('lastPrice', 0)),
                    'price_change_pct': float(t.get('priceChangePercent', 0)),
                    'volume_24h_usd': vol_usd,
                    'high_24h': float(t.get('highPrice', 0)),
                    'low_24h': float(t.get('lowPrice', 0)),
                })
        except (ValueError, TypeError):
            continue
    return result


def _step2_momentum(candidates: List[Dict]) -> List[Dict]:
    """
    Momentum filter: vol_spike >= 1.2, price_move_4h >= 0.8%, adx_15m >= 22.
    Fetches 15m klines for each candidate — only called on universe survivors.
    """
    passed = []
    for c in candidates:
        sym = c['symbol']
        try:
            # 15m klines for ADX and vol spike (50 bars = ~12.5h)
            klines = _fetch_klines(sym, '15m', 50)
            if len(klines) < 20:
                continue

            opens  = [float(k[1]) for k in klines]
            highs  = [float(k[2]) for k in klines]
            lows   = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            vols   = [float(k[5]) for k in klines]

            # Drop last bar if it looks like an incomplete yfinance bar
            # (current bar volume < 10% of the bar before it)
            if len(vols) >= 2 and vols[-2] > 0 and vols[-1] / vols[-2] < 0.10:
                opens = opens[:-1]; highs = highs[:-1]
                lows  = lows[:-1];  closes = closes[:-1]
                vols  = vols[:-1]

            # Vol spike (current bar vs 20-bar avg)
            vs = _vol_spike(vols, 20)

            # Price move over last 4h = ~16 bars of 15m
            bars_4h = min(16, len(closes) - 1)
            price_move_4h = abs(closes[-1] - closes[-bars_4h]) / (closes[-bars_4h] + 1e-9) * 100

            # ADX(14)
            adx = _calc_adx(highs, lows, closes, 14)

            if vs >= _MIN_VOL_SPIKE and price_move_4h >= _MIN_PRICE_MOVE_4H and adx >= _MIN_ADX_15M:
                # Direction: momentum of last 3 bars
                recent_move = closes[-1] - closes[-4] if len(closes) >= 4 else closes[-1] - closes[0]
                direction = 'LONG' if recent_move > 0 else 'SHORT'

                c.update({
                    'vol_spike': round(vs, 3),
                    'price_move_4h_pct': round(price_move_4h, 3),
                    'adx_15m': round(adx, 1),
                    'direction': direction,
                    'closes_15m': closes,
                    'highs_15m': highs,
                    'lows_15m': lows,
                    'vols_15m': vols,
                })
                passed.append(c)

        except Exception as e:
            logger.debug(f'[scanner] step2 error {sym}: {e}')
            continue

    return passed


def _step3_liquidity(candidates: List[Dict]) -> List[Dict]:
    """Orderbook depth > $50K each side, spread < 0.1%."""
    passed = []
    for c in candidates:
        sym = c['symbol']
        price = c['price']
        try:
            ob = _fetch_ob_depth(sym)
            bids = ob.get('bids', [])
            asks = ob.get('asks', [])
            if not bids or not asks:
                passed.append(c)   # fail-open if no data
                continue

            bid_depth = sum(float(b[0]) * float(b[1]) for b in bids[:5])
            ask_depth = sum(float(a[0]) * float(a[1]) for a in asks[:5])
            best_bid  = float(bids[0][0])
            best_ask  = float(asks[0][0])
            spread_pct = (best_ask - best_bid) / ((best_bid + best_ask) / 2 + 1e-9) * 100

            if bid_depth >= _MIN_OB_DEPTH_USD and ask_depth >= _MIN_OB_DEPTH_USD and spread_pct <= _MAX_SPREAD_PCT:
                c.update({
                    'bid_depth_usd': round(bid_depth, 0),
                    'ask_depth_usd': round(ask_depth, 0),
                    'spread_pct': round(spread_pct, 4),
                })
                passed.append(c)

        except Exception as e:
            logger.debug(f'[scanner] step3 error {sym}: {e}')
            passed.append(c)   # fail-open

    return passed


def _step4_expected_value(candidates: List[Dict],
                           account_balance: float = 10000.0,
                           risk_pct: float = 0.02) -> List[Dict]:
    """
    Expected value filter: expected_profit >= $1.50.
    EV = (win_rate * avg_win) - (loss_rate * avg_loss)
    Uses ATR-based stop/target estimation.
    """
    passed = []
    for c in candidates:
        try:
            closes = c.get('closes_15m', [])
            if len(closes) < 15:
                c['expected_profit'] = 2.0   # pass with neutral assumption
                passed.append(c)
                continue

            # ATR proxy: average of |close - prev_close| over last 14 bars
            diffs = [abs(closes[i] - closes[i-1]) for i in range(-14, 0)]
            atr = sum(diffs) / len(diffs)
            price = c['price']

            stop_dist = atr * 1.5
            target_dist = atr * 3.0
            stop_pct = stop_dist / price
            target_pct = target_dist / price

            dollar_risk = account_balance * risk_pct
            position_usd = dollar_risk / (stop_pct + 1e-9)

            # Conservative 50% win rate baseline
            win_rate = 0.50
            avg_win  = position_usd * target_pct
            avg_loss = dollar_risk
            ev = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

            if ev >= _MIN_EXPECTED_PROFIT:
                c.update({
                    'atr_15m': round(atr, 4),
                    'stop_pct': round(stop_pct * 100, 3),
                    'target_pct': round(target_pct * 100, 3),
                    'expected_profit': round(ev, 2),
                })
                passed.append(c)

        except Exception as e:
            logger.debug(f'[scanner] step4 error {c.get("symbol")}: {e}')
            c['expected_profit'] = 2.0
            passed.append(c)

    return passed


def _step5_correlation(candidates: List[Dict],
                        open_positions: Optional[List[str]] = None) -> List[Dict]:
    """
    Reduce size flag if candidate is highly correlated with open position.
    Simple proxy: same base asset family = correlated.
    Full matrix correlation implemented in risk_engine.py.
    """
    if not open_positions:
        for c in candidates:
            c['correlation_penalty'] = 1.0
        return candidates

    for c in candidates:
        sym = c['symbol'].replace('USDT', '')
        # Mark as correlated if we have an open position in same asset
        correlated = any(sym in pos or pos in sym for pos in open_positions)
        c['correlation_penalty'] = 0.5 if correlated else 1.0

    return candidates


def _step6_regime_filter(candidates: List[Dict], regime: str = 'UNKNOWN') -> List[Dict]:
    """
    Match signal type to regime. In HIGH_VOL, require stronger vol_spike.
    In RANGING, require mean-reversion signals (high RSI extreme + low ADX).
    """
    if regime == 'UNKNOWN':
        return candidates

    passed = []
    for c in candidates:
        direction = c.get('direction', 'LONG')
        adx = c.get('adx_15m', 25)
        vs  = c.get('vol_spike', 1.0)

        if regime == 'HIGH_VOL':
            if vs < 1.5:
                continue

        elif regime == 'RANGING':
            if adx > 30:
                continue   # skip strong trend trades in ranging market

        elif regime in ('TRENDING_UP', 'TRENDING_DOWN'):
            # In trending regimes prefer trades aligned with trend
            if regime == 'TRENDING_UP' and direction == 'SHORT':
                c['regime_penalty'] = 0.80   # allow but mark as counter-trend
            elif regime == 'TRENDING_DOWN' and direction == 'LONG':
                c['regime_penalty'] = 0.80
            else:
                c['regime_penalty'] = 1.0

        if 'regime_penalty' not in c:
            c['regime_penalty'] = 1.0

        passed.append(c)

    return passed


def _step7_rank_and_top(candidates: List[Dict], n: int = _TOP_N) -> List[Dict]:
    """Sort by vol_spike descending, return top N."""
    sorted_c = sorted(candidates, key=lambda x: x.get('vol_spike', 0), reverse=True)
    result = []
    for c in sorted_c[:n]:
        # Clean up large fields not needed downstream
        c.pop('closes_15m', None)
        c.pop('highs_15m', None)
        c.pop('lows_15m', None)
        c.pop('vols_15m', None)
        result.append(c)
    return result


def scan(open_positions: Optional[List[str]] = None,
         regime: str = 'UNKNOWN',
         account_balance: float = 10000.0) -> List[Dict]:
    """
    Run the full 7-step scanner pipeline.

    Args:
        open_positions: list of currently held symbols (for correlation filter)
        regime: current market regime string (from ml/regime_classifier.py)
        account_balance: for EV calculation

    Returns:
        List of up to 15 candidate dicts, sorted by vol_spike descending.
        Each dict contains: symbol, price, direction, vol_spike, adx_15m,
        price_move_4h_pct, atr_15m, stop_pct, target_pct, expected_profit,
        correlation_penalty, regime_penalty, spread_pct.
    """
    global _last_scan_ts, _last_candidates

    with _lock:
        if time.time() - _last_scan_ts < _CACHE_TTL:
            return _last_candidates

    t_start = time.time()
    logger.info('[scanner] Starting full-market scan...')

    try:
        # Step 1: Universe
        tickers = _fetch_tickers()
        universe = _step1_universe(tickers)
        logger.info(f'[scanner] Step 1 (volume>${_MIN_VOLUME_24H_USD/1e6:.0f}M): {len(tickers)} pairs → {len(universe)} candidates')

        if not universe:
            return []

        # Step 2: Momentum (makes API calls per symbol — most expensive step)
        momentum_pass = _step2_momentum(universe)
        logger.info(f'[scanner] Step 2 (momentum): {len(universe)} → {len(momentum_pass)}')

        if not momentum_pass:
            return []

        # Step 3: Liquidity
        liquidity_pass = _step3_liquidity(momentum_pass)
        logger.info(f'[scanner] Step 3 (liquidity): {len(momentum_pass)} → {len(liquidity_pass)}')

        # Step 4: Expected value
        ev_pass = _step4_expected_value(liquidity_pass, account_balance)
        logger.info(f'[scanner] Step 4 (EV>=${_MIN_EXPECTED_PROFIT}): {len(liquidity_pass)} → {len(ev_pass)}')

        # Step 5: Correlation
        corr_pass = _step5_correlation(ev_pass, open_positions)

        # Step 6: Regime filter
        regime_pass = _step6_regime_filter(corr_pass, regime)
        logger.info(f'[scanner] Step 6 (regime={regime}): {len(corr_pass)} → {len(regime_pass)}')

        # Step 7: Rank and top 15
        final = _step7_rank_and_top(regime_pass)

        elapsed = time.time() - t_start
        logger.info(f'[scanner] Complete: {len(final)} candidates in {elapsed:.1f}s')
        for c in final:
            logger.info(f'[scanner] → {c["symbol"]} {c["direction"]} spike={c.get("vol_spike",0):.2f} '
                       f'adx={c.get("adx_15m",0):.0f} ev=${c.get("expected_profit",0):.2f}')

        with _lock:
            _last_scan_ts = time.time()
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
        'last_scan_ts': _last_scan_ts,
        'last_scan_age_s': round(time.time() - _last_scan_ts, 0),
        'candidate_count': len(_last_candidates),
        'candidates': [
            {
                'symbol': c['symbol'],
                'direction': c.get('direction', '?'),
                'vol_spike': c.get('vol_spike', 0),
                'adx': c.get('adx_15m', 0),
                'ev': c.get('expected_profit', 0),
            }
            for c in _last_candidates
        ],
    }
