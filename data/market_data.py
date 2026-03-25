"""
data/market_data.py
Free market data via yfinance.
Handles historical OHLCV, real-time quotes, and equity screener logic.
"""
import json
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional
import time
import pytz
import requests
from bs4 import BeautifulSoup

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    EQUITY_MIN_PRICE, EQUITY_MAX_PRICE,
    EQUITY_MIN_VOLUME, EQUITY_MIN_DOLLAR_VOLUME,
    EQUITY_VOLUME_SPIKE_MULTIPLIER, MARKET_TIMEZONE
)


# ─── OHLCV data ───────────────────────────────────────────────────────────────

def get_bars(
    symbol: str,
    interval: str = '5m',
    period: str = '5d',
    prepost: bool = False
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV data via yfinance.
    interval: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo
    period:   1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(interval=interval, period=period, prepost=prepost)
        if df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        print(f"[market_data] Error fetching {symbol} {interval}: {e}")
        return None


def get_daily_bars(symbol: str, period: str = '6mo') -> Optional[pd.DataFrame]:
    return get_bars(symbol, interval='1d', period=period)


def get_weekly_bars(symbol: str, period: str = '2y') -> Optional[pd.DataFrame]:
    return get_bars(symbol, interval='1wk', period=period)


def get_quote(symbol: str) -> Optional[dict]:
    """Get current quote (last price, bid, ask, volume)."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        return {
            'symbol': symbol,
            'last_price': getattr(info, 'last_price', None),
            'bid': getattr(info, 'bid', None),
            'ask': getattr(info, 'ask', None),
            'volume': getattr(info, 'three_month_average_volume', None),
            'market_cap': getattr(info, 'market_cap', None),
        }
    except Exception as e:
        print(f"[market_data] Error getting quote for {symbol}: {e}")
        return None


def get_current_price(symbol: str) -> Optional[float]:
    """Fast single-price lookup."""
    try:
        df = yf.download(symbol, period='1d', interval='1m', progress=False)
        if df.empty:
            return None
        return float(df['Close'].iloc[-1])
    except Exception:
        return None


# ─── Equity screener ──────────────────────────────────────────────────────────

def screen_watchlist() -> list[dict]:
    """
    Screen the configured watchlist for trading candidates.
    Returns list of tickers meeting all criteria, sorted by volume spike.
    """
    try:
        watchlist = EQUITY_WATCHLIST  # type: ignore[name-defined]  # noqa
    except NameError:
        return []  # no watchlist configured — auto_screener handles discovery

    candidates = []

    for symbol in watchlist:
        try:
            df = get_daily_bars(symbol, period='3mo')
            if df is None or len(df) < 22:
                continue

            last = df.iloc[-1]
            close = last['close']
            volume = last['volume']
            vol_ma20 = df['volume'].iloc[-21:-1].mean()
            vol_spike = volume / vol_ma20 if vol_ma20 > 0 else 0
            dollar_volume = close * volume

            # Apply filters
            if close < EQUITY_MIN_PRICE:
                continue
            if close > EQUITY_MAX_PRICE:
                continue
            if volume < EQUITY_MIN_VOLUME:
                continue
            if dollar_volume < EQUITY_MIN_DOLLAR_VOLUME:
                continue
            if vol_spike < EQUITY_VOLUME_SPIKE_MULTIPLIER:
                continue

            candidates.append({
                'symbol': symbol,
                'price': close,
                'volume': volume,
                'vol_ma20': vol_ma20,
                'vol_spike': vol_spike,
                'dollar_volume': dollar_volume,
            })

        except Exception as e:
            print(f"[screener] Error on {symbol}: {e}")
            continue

        time.sleep(0.1)  # Polite delay to avoid rate limiting

    # Sort by volume spike descending
    candidates.sort(key=lambda x: x['vol_spike'], reverse=True)
    return candidates


def get_top_volume_movers(limit: int = 10) -> list[dict]:
    """
    Scrape Finviz for top volume movers as a discovery feed.
    Supplements the watchlist with fresh ideas.
    Free — no API key needed.
    """
    url = (
        'https://finviz.com/screener.ashx?v=111&s=ta_unusualvolume'
        '&f=cap_smallover,price_o1,price_u200,sh_curvol_o500'
        '&o=-volume'
    )
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'lxml')
        table = soup.find('table', {'id': 'screener-content'})
        if not table:
            return []

        rows = table.find_all('tr')[1:]
        movers = []
        for row in rows[:limit]:
            cols = row.find_all('td')
            if len(cols) < 10:
                continue
            try:
                movers.append({
                    'symbol': cols[1].text.strip(),
                    'company': cols[2].text.strip(),
                    'price': float(cols[8].text.strip()),
                    'volume': cols[9].text.strip(),
                })
            except (ValueError, IndexError):
                continue

        return movers

    except Exception as e:
        print(f"[screener] Finviz scrape failed: {e}")
        return []


# ─── Market session helpers ───────────────────────────────────────────────────

def is_market_open() -> bool:
    tz = pytz.timezone(MARKET_TIMEZONE)
    now = datetime.now(tz)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close


def is_in_no_trade_window() -> bool:
    """First 30 minutes after open — no trading."""
    tz = pytz.timezone(MARKET_TIMEZONE)
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return False
    no_trade_end = now.replace(hour=10, minute=0, second=0, microsecond=0)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    return market_open <= now < no_trade_end


def minutes_to_market_open() -> int:
    tz = pytz.timezone(MARKET_TIMEZONE)
    now = datetime.now(tz)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= market_open:
        return 0
    delta = market_open - now
    return int(delta.total_seconds() / 60)


_spy_breadth_cache: dict = {'ts': 0.0, 'ok': True, 'spy_pct': 0.0}

def get_market_breadth() -> dict:
    """
    Check SPY intraday % change vs prior close.
    Returns {'ok': bool, 'spy_pct': float}.
    Cached 5 minutes — safe to call on every scan.
    """
    import time as _time
    now = _time.time()
    if now - _spy_breadth_cache['ts'] < 300:
        return _spy_breadth_cache
    try:
        df = get_bars('SPY', interval='1d', period='5d')
        if df is not None and len(df) >= 2:
            prev_close = float(df.iloc[-2]['close'])
            current    = float(df.iloc[-1]['close'])
            pct = (current - prev_close) / prev_close * 100
        else:
            pct = 0.0
    except Exception:
        pct = 0.0

    import sys as _sys
    _sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from config import MARKET_BREADTH_MIN_SPY_PCT
        ok = pct >= MARKET_BREADTH_MIN_SPY_PCT
    except Exception:
        ok = pct >= -2.0

    _spy_breadth_cache.update({'ts': now, 'ok': ok, 'spy_pct': pct})
    return _spy_breadth_cache


def has_earnings_within_days(symbol: str, days: int = 3) -> bool:
    """
    Return True if the stock has an earnings announcement within `days` calendar days.
    Avoids entering ahead of binary events. Uses yfinance calendar data.
    """
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None or (hasattr(cal, 'empty') and cal.empty):
            return False
        # calendar is a DataFrame; earnings dates are in the 'Earnings Date' row
        if 'Earnings Date' in cal.index:
            dates = cal.loc['Earnings Date']
        else:
            return False
        tz = pytz.timezone(MARKET_TIMEZONE)
        today = datetime.now(tz).date()
        for d in (dates if hasattr(dates, '__iter__') else [dates]):
            try:
                ed = pd.Timestamp(d).date()
                if 0 <= (ed - today).days <= days:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def is_near_market_close(minutes_before: int = 15) -> bool:
    """True if within `minutes_before` minutes of 4:00 PM ET market close."""
    tz = pytz.timezone(MARKET_TIMEZONE)
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return False
    close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    gate  = now.replace(hour=16 - (minutes_before // 60),
                        minute=(60 - minutes_before % 60) % 60,
                        second=0, microsecond=0)
    return gate <= now <= close


# ─── Sentiment & volatility signals ───────────────────────────────────────────

_fear_greed_cache: dict = {'ts': 0.0, 'score': 50, 'label': 'Neutral'}

def get_fear_greed() -> dict:
    """
    Fetch Crypto Fear & Greed Index from Alternative.me (crypto-native, more reliable than CNN).
    Falls back to CNN if Alternative.me fails. Cached 30 minutes (index updates once daily).
    Returns {'score': 0-100, 'label': str}.
    Extreme Fear < 25, Fear < 45, Neutral 45-55, Greed > 55, Extreme Greed > 75.
    """
    import time as _time
    now = _time.time()
    if now - _fear_greed_cache['ts'] < 1800:  # 30-min cache — index updates daily anyway
        return _fear_greed_cache

    # Primary: Alternative.me Crypto Fear & Greed (free, no auth, highly reliable)
    try:
        import urllib.request
        req = urllib.request.Request(
            'https://api.alternative.me/fng/?limit=1',
            headers={'User-Agent': 'Mozilla/5.0'},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            entry = data.get('data', [{}])[0]
            score = float(entry.get('value', 50))
            raw_label = entry.get('value_classification', 'Neutral')
            # Normalise label
            label_map = {
                'Extreme Fear': 'Extreme Fear', 'Fear': 'Fear',
                'Neutral': 'Neutral', 'Greed': 'Greed', 'Extreme Greed': 'Extreme Greed'
            }
            label = label_map.get(raw_label, raw_label.title())
            _fear_greed_cache.update({'ts': now, 'score': score, 'label': label})
            return _fear_greed_cache
    except Exception:
        pass

    # Fallback: CNN Fear & Greed
    try:
        import urllib.request
        req = urllib.request.Request(
            'https://production.dataviz.cnn.io/index/fearandgreed/graphdata',
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.cnn.com/'},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            fg = data.get('fear_and_greed', {})
            score = float(fg.get('score', 50))
            label = fg.get('rating', 'Neutral').replace('_', ' ').title()
            _fear_greed_cache.update({'ts': now, 'score': score, 'label': label})
            return _fear_greed_cache
    except Exception:
        pass

    return _fear_greed_cache


_iv_rank_cache: dict = {}

def get_iv_rank(symbol: str) -> Optional[float]:
    """
    Estimate IV rank (0-100) for a symbol using 1-year historical volatility range.
    Returns None on failure. Cached 30 minutes per symbol.
    """
    import time as _time
    import json as _json
    now = _time.time()
    cached = _iv_rank_cache.get(symbol)
    if cached and now - cached['ts'] < 1800:
        return cached['iv']

    try:
        df = get_daily_bars(symbol, period='1y')
        if df is None or len(df) < 30:
            return None
        # 30-day rolling realized vol as IV proxy
        log_ret = np.log(df['close'] / df['close'].shift(1)).dropna()
        rolling_vol = log_ret.rolling(30).std() * np.sqrt(252) * 100
        rolling_vol = rolling_vol.dropna()
        if len(rolling_vol) < 20:
            return None
        current_vol = float(rolling_vol.iloc[-1])
        min_vol = float(rolling_vol.min())
        max_vol = float(rolling_vol.max())
        if max_vol == min_vol:
            return None
        iv_rank = (current_vol - min_vol) / (max_vol - min_vol) * 100
        _iv_rank_cache[symbol] = {'ts': now, 'iv': iv_rank}
        return iv_rank
    except Exception:
        return None


def check_minervini_setup(symbol: str, df_daily: Optional[pd.DataFrame] = None) -> dict:
    """
    Minervini SEPA setup check.
    Valid = above 200-day MA + breaking 20-day high + 40%+ above-average volume.
    Returns {'valid': bool, 'reason': str, 'vol_pct_above': float}
    """
    try:
        if df_daily is None:
            df_daily = get_daily_bars(symbol, period='1y')
        if df_daily is None or len(df_daily) < 50:
            return {'valid': False, 'reason': 'Insufficient daily data', 'vol_pct_above': 0}

        close = df_daily['close']
        volume = df_daily['volume']
        current_close = float(close.iloc[-1])

        # 200-day MA (use 50d if less history)
        ma_period = min(200, len(close) - 1)
        ma_val = float(close.rolling(ma_period).mean().iloc[-1])
        if current_close < ma_val:
            return {'valid': False,
                    'reason': f'Below {ma_period}-day MA (${current_close:.2f} < ${ma_val:.2f})',
                    'vol_pct_above': 0}

        # 20-day high breakout (exclude today's bar)
        prior_high = float(close.iloc[-21:-1].max()) if len(close) >= 21 else float(close.iloc[:-1].max())
        if current_close <= prior_high:
            return {'valid': False,
                    'reason': f'No 20-day high breakout (${current_close:.2f} vs high ${prior_high:.2f})',
                    'vol_pct_above': 0}

        # Volume confirmation
        vol_ma20 = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.iloc[:-1].mean())
        current_vol = float(volume.iloc[-1])
        vol_pct = (current_vol / vol_ma20 - 1) * 100 if vol_ma20 > 0 else 0

        if vol_pct < 40:
            return {'valid': False,
                    'reason': f'Volume weak: only +{vol_pct:.0f}% vs avg (need 40%+)',
                    'vol_pct_above': vol_pct}

        return {'valid': True,
                'reason': f'SEPA: above {ma_period}d MA, 20d high breakout, vol +{vol_pct:.0f}%',
                'vol_pct_above': vol_pct}
    except Exception as e:
        return {'valid': False, 'reason': f'Minervini check error: {e}', 'vol_pct_above': 0}


def count_pullback_bars(df: 'pd.DataFrame') -> dict:
    """
    Landry pullback detection on any timeframe.
    Counts consecutive bars moving against the established EMA trend.
    Valid Landry entry = 3-5 bars of pullback then reversal.
    Returns {'pullback_bars': int, 'trend': str, 'is_valid_pullback': bool}
    """
    try:
        if df is None or len(df) < 25:
            return {'pullback_bars': 0, 'trend': 'unclear', 'is_valid_pullback': False}

        close = df['close']
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        trend = 'up' if float(ema20.iloc[-1]) > float(ema50.iloc[-1]) else 'down'

        # Count consecutive bars going against the trend (pullback)
        pullback_count = 0
        for i in range(-2, -10, -1):  # Start from second-to-last bar
            try:
                c = float(close.iloc[i])
                c_prev = float(close.iloc[i - 1])
                if trend == 'up' and c < c_prev:
                    pullback_count += 1
                elif trend == 'down' and c > c_prev:
                    pullback_count += 1
                else:
                    break
            except Exception:
                break

        is_valid = 3 <= pullback_count <= 5
        return {
            'pullback_bars': pullback_count,
            'trend': trend,
            'is_valid_pullback': is_valid,
        }
    except Exception:
        return {'pullback_bars': 0, 'trend': 'unclear', 'is_valid_pullback': False}


_cot_cache: dict = {'ts': 0.0, 'commercial_net': 0, 'is_bullish': True}

def get_cot_sentiment() -> dict:
    """
    Fetch CFTC Commitment of Traders data for S&P 500 financial futures.
    Commercials net long = institutional bullish bias → favor MES longs.
    Cached 1 week (COT reports released every Friday).
    Returns {'commercial_net': int, 'is_bullish': bool}
    """
    import io
    import zipfile
    import urllib.request
    import time as _time
    now = _time.time()
    if now - _cot_cache['ts'] < 7 * 24 * 3600 and _cot_cache['ts'] > 0:
        return _cot_cache

    year = datetime.now().year
    url = f'https://www.cftc.gov/sites/default/files/files/dea/history/fut_fin_txt_{year}.zip'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as resp:
            zip_data = resp.read()
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
            names = [n for n in z.namelist() if n.endswith('.txt') or n.endswith('.csv')]
            if not names:
                return _cot_cache
            with z.open(names[0]) as f:
                df_cot = pd.read_csv(f, low_memory=False)
        sp_rows = df_cot[df_cot['Market_and_Exchange_Names'].str.contains('S&P 500', na=False)]
        if sp_rows.empty:
            return _cot_cache
        latest = sp_rows.sort_values('As_of_Date_In_Form_YYMMDD').iloc[-1]
        longs = int(latest.get('Comm_Positions_Long_All', 0))
        shorts = int(latest.get('Comm_Positions_Short_All', 0))
        net = longs - shorts
        _cot_cache.update({'ts': now, 'commercial_net': net, 'is_bullish': net > 0})
        print(f"[cot] S&P commercials net {net:+,} — {'bullish' if net > 0 else 'bearish'}")
    except Exception as e:
        print(f"[cot] CFTC fetch failed: {e} — using cached/default")
    return _cot_cache


def get_williams_r(df: 'pd.DataFrame', period: int = 14) -> float:
    """
    Calculate Williams %R from OHLCV DataFrame.
    Returns -50 (mid-range) if insufficient data.
    Range: 0 (overbought) to -100 (oversold). Below -80 = oversold extreme.
    """
    try:
        if df is None or len(df) < period:
            return -50.0
        high = df['high'].iloc[-period:].max()
        low  = df['low'].iloc[-period:].min()
        close = float(df['close'].iloc[-1])
        if high == low:
            return -50.0
        return ((high - close) / (high - low)) * -100
    except Exception:
        return -50.0


def get_momentum_score(df: 'pd.DataFrame') -> float:
    """
    Clenow momentum score: exponential regression slope × R².
    Higher = stronger, more consistent uptrend.
    Returns 0.0 on failure.
    """
    try:
        if df is None or len(df) < 90:
            return 0.0
        closes = df['close'].iloc[-90:].values
        x = np.arange(len(closes), dtype=float)
        # Fit log-linear regression (exponential trend)
        log_closes = np.log(closes)
        slope, intercept = np.polyfit(x, log_closes, 1)
        predicted = slope * x + intercept
        ss_res = np.sum((log_closes - predicted) ** 2)
        ss_tot = np.sum((log_closes - log_closes.mean()) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        # Annualised slope (daily bars → *252; minute bars leave as-is)
        score = slope * r_squared * 252
        return float(max(score, 0.0))
    except Exception:
        return 0.0
