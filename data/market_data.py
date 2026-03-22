"""
data/market_data.py
Free market data via yfinance.
Handles historical OHLCV, real-time quotes, and equity screener logic.
"""
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
    candidates = []

    for symbol in EQUITY_WATCHLIST:
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
