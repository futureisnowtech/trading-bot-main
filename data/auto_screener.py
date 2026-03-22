"""
data/auto_screener.py

Fully automated stock discovery. No manual watchlist.
Every scan cycle this rebuilds the candidate universe from scratch
using multiple live data sources.

Sources (all free):
  1. Finviz unusual volume screener
  2. Yahoo Finance top gainers/active
  3. MarketWatch most active
  4. SEC EDGAR recent filings (catalysts)
  5. yfinance momentum filter

The output is a ranked list of tickers with the highest probability
of a clean momentum setup RIGHT NOW.
"""
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import Optional
import time
import json
import re
import pytz

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    EQUITY_MIN_PRICE, EQUITY_MAX_PRICE, EQUITY_MIN_VOLUME,
    EQUITY_MIN_DOLLAR_VOLUME, EQUITY_VOLUME_SPIKE_MULTIPLIER,
    MARKET_TIMEZONE
)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


# ─── Source 1: Finviz unusual volume ─────────────────────────────────────────

def get_finviz_unusual_volume(limit: int = 30) -> list[dict]:
    """
    Scrape Finviz for stocks with unusual volume today.
    Filters: price $1–200, volume > 500K, market cap small+
    """
    url = (
        'https://finviz.com/screener.ashx?v=111'
        '&f=cap_smallover,price_o1,price_u200,sh_curvol_o500,ta_unusualvolume'
        '&o=-volume&r=1'
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'lxml')
        rows = soup.select('tr.styled-row, #screener-content tr')
        results = []
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 12:
                continue
            try:
                ticker = cells[1].get_text(strip=True)
                price_str = cells[8].get_text(strip=True).replace(',', '')
                change_str = cells[9].get_text(strip=True).replace('%', '').replace('+', '')
                volume_str = cells[10].get_text(strip=True).replace(',', '')
                if not ticker or not price_str.replace('.','').isdigit():
                    continue
                results.append({
                    'symbol': ticker,
                    'price': float(price_str),
                    'change_pct': float(change_str) if change_str.lstrip('-').replace('.','').isdigit() else 0,
                    'volume': int(volume_str) if volume_str.isdigit() else 0,
                    'source': 'finviz_unusual_vol',
                })
            except (ValueError, IndexError):
                continue
        return results[:limit]
    except Exception as e:
        print(f"[auto_screener] Finviz error: {e}")
        return []


# ─── Source 2: Yahoo Finance top gainers & most active ───────────────────────

def get_yahoo_movers(limit: int = 20) -> list[dict]:
    """Pull Yahoo Finance top gainers and most active."""
    results = []
    endpoints = [
        ('https://finance.yahoo.com/gainers', 'yahoo_gainers'),
        ('https://finance.yahoo.com/most-active', 'yahoo_active'),
    ]
    for url, source in endpoints:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, 'lxml')
            rows = soup.select('tr')
            for row in rows:
                cells = row.find_all('td')
                if len(cells) < 5:
                    continue
                try:
                    ticker = cells[0].get_text(strip=True)
                    price_str = cells[2].get_text(strip=True).replace(',', '')
                    change_pct_str = cells[4].get_text(strip=True).replace('%', '').replace('+', '')
                    if not ticker or len(ticker) > 5 or not price_str.replace('.','').isdigit():
                        continue
                    results.append({
                        'symbol': ticker,
                        'price': float(price_str),
                        'change_pct': float(change_pct_str) if change_pct_str.lstrip('-').replace('.','').isdigit() else 0,
                        'volume': 0,
                        'source': source,
                    })
                except (ValueError, IndexError):
                    continue
            time.sleep(0.5)
        except Exception as e:
            print(f"[auto_screener] Yahoo {url} error: {e}")
    return results[:limit]


# ─── Source 3: SEC EDGAR recent filings (catalyst detection) ─────────────────

def get_sec_catalysts(limit: int = 15) -> list[dict]:
    """
    Check SEC EDGAR for recent 8-K filings (material events).
    8-K = earnings, M&A, major contracts — potential catalysts.
    Uses the free EDGAR full-text search API.
    """
    url = 'https://efts.sec.gov/LATEST/search-index?q=%228-K%22&dateRange=custom&startdt={}&enddt={}&forms=8-K'
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        resp = requests.get(
            f'https://efts.sec.gov/LATEST/search-index?forms=8-K&dateRange=custom'
            f'&startdt={yesterday}&enddt={today}',
            headers={**HEADERS, 'Accept': 'application/json'},
            timeout=10
        )
        data = resp.json()
        hits = data.get('hits', {}).get('hits', [])
        results = []
        seen = set()
        for hit in hits[:50]:
            src = hit.get('_source', {})
            ticker = src.get('ticker', '')
            if ticker and ticker not in seen and len(ticker) <= 5:
                seen.add(ticker)
                results.append({
                    'symbol': ticker,
                    'price': 0,
                    'change_pct': 0,
                    'volume': 0,
                    'source': 'sec_8k_catalyst',
                    'catalyst': src.get('file_date', ''),
                })
        return results[:limit]
    except Exception as e:
        print(f"[auto_screener] SEC EDGAR error: {e}")
        return []


# ─── Source 4: yfinance momentum validation ──────────────────────────────────

def validate_and_enrich(candidates: list[dict]) -> list[dict]:
    """
    Take the raw candidate list, fetch real OHLCV data from yfinance,
    compute volume spike and momentum score, filter to only valid setups.
    Deduplicates by symbol.
    """
    # Deduplicate
    seen = {}
    for c in candidates:
        sym = c['symbol']
        if sym not in seen:
            seen[sym] = c

    unique = list(seen.values())
    validated = []

    for candidate in unique:
        sym = candidate['symbol']
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(period='1mo', interval='1d')
            if df.empty or len(df) < 5:
                continue

            df.columns = [c.lower() for c in df.columns]
            last = df.iloc[-1]
            price = float(last['close'])
            volume = float(last['volume'])
            vol_ma20 = float(df['volume'].iloc[-20:].mean()) if len(df) >= 20 else float(df['volume'].mean())
            vol_spike = volume / vol_ma20 if vol_ma20 > 0 else 0
            dollar_volume = price * volume

            # Apply hard filters
            if price < EQUITY_MIN_PRICE or price > EQUITY_MAX_PRICE:
                continue
            if volume < EQUITY_MIN_VOLUME:
                continue
            if dollar_volume < EQUITY_MIN_DOLLAR_VOLUME:
                continue

            # Momentum score
            returns = df['close'].pct_change().dropna()
            momentum_3d = float(df['close'].iloc[-1] / df['close'].iloc[-4] - 1) if len(df) >= 4 else 0
            momentum_score = (
                (vol_spike * 0.4) +
                (min(abs(momentum_3d) * 10, 3.0) * 0.3) +
                (min(dollar_volume / 5_000_000, 2.0) * 0.3)
            )

            validated.append({
                **candidate,
                'price': price,
                'volume': int(volume),
                'vol_spike': round(vol_spike, 2),
                'dollar_volume': dollar_volume,
                'momentum_3d': round(momentum_3d * 100, 2),
                'momentum_score': round(momentum_score, 3),
            })

            time.sleep(0.15)  # Rate limit yfinance

        except Exception as e:
            continue

    # Sort by momentum score descending
    validated.sort(key=lambda x: x['momentum_score'], reverse=True)
    return validated


# ─── Master discovery function ────────────────────────────────────────────────

def discover_candidates(max_results: int = 10) -> list[dict]:
    """
    Full automated discovery pipeline. Calls all sources in parallel (sequential
    for safety) and returns top candidates ranked by momentum score.
    No human input required.
    """
    print("[auto_screener] 🔍 Discovering trading candidates...")
    all_candidates = []

    # Source 1: Finviz unusual volume
    fv = get_finviz_unusual_volume(30)
    all_candidates.extend(fv)
    print(f"  Finviz: {len(fv)} candidates")

    # Source 2: Yahoo movers
    yh = get_yahoo_movers(20)
    all_candidates.extend(yh)
    print(f"  Yahoo:  {len(yh)} candidates")

    # Source 3: SEC catalysts
    sec = get_sec_catalysts(15)
    all_candidates.extend(sec)
    print(f"  SEC:    {len(sec)} candidates")

    if not all_candidates:
        print("[auto_screener] No candidates from any source — market may be closed")
        return []

    # Validate and enrich with real OHLCV data
    print(f"  Validating {len(all_candidates)} total (deduped)...")
    validated = validate_and_enrich(all_candidates)
    top = validated[:max_results]

    print(f"  ✅ {len(top)} qualified candidates ranked by momentum score")
    for i, c in enumerate(top, 1):
        print(f"     {i}. {c['symbol']:6} | ${c['price']:7.2f} | vol spike {c['vol_spike']:.1f}x | score {c['momentum_score']:.2f} | src: {c['source']}")

    return top
