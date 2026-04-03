"""
data/news_feed.py — Crypto & equity news sentiment feed.

Sources (in priority order):
  1. CryptoPanic API (requires CRYPTOPANIC_API_KEY in .env — free tier available)
  2. CoinDesk RSS fallback (no API key needed)

Returns structured sentiment data injected into debate agent context and
used by the AI Session Analyst to flag news risk before opening positions.

Cache TTL: 10 minutes (news changes slowly; avoid hammering APIs).
"""
import os, sys, json, time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CRYPTOPANIC_API_KEY

_NEWS_CACHE: dict = {}
_CACHE_TTL: int = 600  # 10 minutes

# ── Keyword heuristics ────────────────────────────────────────────────────────
_BEARISH_WORDS = [
    'hack', 'exploit', 'breach', 'crash', 'scam', 'fraud', 'lawsuit',
    'ban', 'regulate', 'arrest', 'shut down', 'collapse', 'bankrupt',
    'sell-off', 'dump', 'manipulation', 'delisted', 'freeze', 'seized',
    'rug pull', 'exit scam', 'insolvent', 'criminal', 'ponzi',
    'investigation', 'subpoena', 'fine', 'penalty', 'charges',
]
_BULLISH_WORDS = [
    'adoption', 'partnership', 'etf', 'institutional', 'all-time high',
    'breakthrough', 'upgrade', 'launch', 'approved', 'bullish',
    'acquisition', 'integration', 'record', 'milestone', 'accumulate',
    'rally', 'breakout', 'surge', 'soar', 'inflow', 'spot etf',
    'reserve', 'treasury', 'buy',
]
_RISK_WORDS = [
    'sec', 'cftc', 'doj', 'subpoena', 'investigation', 'fine', 'penalty',
    'war', 'sanctions', 'recession', 'inflation', 'rate hike',
    'fed ', 'fomc', 'jerome powell', 'yellen', 'debt ceiling', 'default',
]


def _is_stale(ts: float) -> bool:
    return time.time() - ts > _CACHE_TTL


def _score_headlines(headlines: list) -> dict:
    """Score headlines for sentiment and risk flags."""
    if not headlines:
        return {'sentiment_score': 0.0, 'bullish_count': 0,
                'bearish_count': 0, 'risk_count': 0, 'warning_flags': []}

    bullish, bearish, risk_flags = 0, 0, []
    for h in headlines:
        hl = h.lower()
        bullish += sum(1 for w in _BULLISH_WORDS if w in hl)
        bearish += sum(1 for w in _BEARISH_WORDS if w in hl)
        risk_flags.extend(w for w in _RISK_WORDS if w in hl)

    total = (bullish + bearish) or 1
    sentiment = (bullish - bearish) / total  # -1.0 (very bearish) to +1.0 (very bullish)

    return {
        'sentiment_score': round(sentiment, 3),
        'bullish_count': bullish,
        'bearish_count': bearish,
        'risk_count': len(risk_flags),
        'warning_flags': list(set(risk_flags))[:5],
    }


def _fetch_cryptopanic(symbol: str, api_key: str) -> Optional[list]:
    """Fetch headlines from CryptoPanic API for a specific currency."""
    currency = symbol.split('-')[0].split('/')[0].upper()
    url = (f"https://cryptopanic.com/api/v1/posts/"
           f"?auth_token={api_key}&currencies={currency}&kind=news&public=true")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'AlgoBot/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return [r.get('title', '') for r in data.get('results', [])[:20]]
    except Exception as e:
        print(f"[news_feed] CryptoPanic error for {currency}: {e}")
        return None


def _fetch_cryptopanic_general(api_key: str) -> Optional[list]:
    """Fetch top important headlines from CryptoPanic (market-wide)."""
    url = (f"https://cryptopanic.com/api/v1/posts/"
           f"?auth_token={api_key}&kind=news&public=true&filter=important")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'AlgoBot/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return [r.get('title', '') for r in data.get('results', [])[:20]]
    except Exception as e:
        print(f"[news_feed] CryptoPanic general error: {e}")
        return None


def _fetch_rss(feed_url: str, max_items: int = 15) -> list:
    """Fetch headlines from an RSS/Atom feed. Silent on failure."""
    try:
        req = urllib.request.Request(
            feed_url,
            headers={'User-Agent': 'AlgoBot/1.0',
                     'Accept': 'application/rss+xml,application/atom+xml,*/*'}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            root = ET.parse(resp).getroot()
            # Try RSS items
            items = root.findall('channel/item')
            if not items:
                # Try Atom entries
                ns = '{http://www.w3.org/2005/Atom}'
                items = root.findall(f'{ns}entry')
            headlines = []
            for item in items[:max_items]:
                title = item.find('title') or item.find(f'{ns}title' if 'ns' in dir() else 'title')
                if title is not None and title.text:
                    headlines.append(title.text.strip())
            return headlines
    except Exception as e:
        print(f"[news_feed] RSS error {feed_url[:60]}: {e}")
        return []


def _classify_news_risk(scores: dict) -> str:
    if scores['bearish_count'] >= 3 or scores['risk_count'] >= 2:
        return 'HIGH'
    elif scores['bearish_count'] >= 1 or scores['risk_count'] >= 1:
        return 'MEDIUM'
    return 'LOW'


def get_news_sentiment(symbol: str = 'BTC-USDC', force_refresh: bool = False) -> dict:
    """
    Get news sentiment for a specific crypto/equity symbol.

    Returns:
        {
            'symbol':         str,
            'headlines':      list[str],      # top 8 headlines
            'sentiment_score': float,          # -1.0 to +1.0
            'bullish_count':  int,
            'bearish_count':  int,
            'risk_count':     int,
            'warning_flags':  list[str],       # regulatory/risk terms found
            'news_risk':      str,             # 'HIGH' | 'MEDIUM' | 'LOW'
            'source':         str,
            'headline_count': int,
            'cached':         bool,
        }
    """
    cache_k = f"{symbol}:news"
    if not force_refresh and cache_k in _NEWS_CACHE:
        if not _is_stale(_NEWS_CACHE[cache_k]['ts']):
            return {**_NEWS_CACHE[cache_k]['data'], 'cached': True}

    headlines = []
    source = 'none'

    # 1. Try CryptoPanic
    if CRYPTOPANIC_API_KEY and len(CRYPTOPANIC_API_KEY) > 5:
        fetched = _fetch_cryptopanic(symbol, CRYPTOPANIC_API_KEY)
        if fetched:
            headlines = fetched
            source = 'cryptopanic'

    # 2. RSS fallback — CoinDesk
    if not headlines:
        headlines = _fetch_rss('https://www.coindesk.com/arc/outboundfeeds/rss/')
        if headlines:
            source = 'coindesk_rss'

    scores = _score_headlines(headlines)
    result = {
        'symbol': symbol,
        'headlines': headlines[:8],
        'sentiment_score': scores['sentiment_score'],
        'bullish_count': scores['bullish_count'],
        'bearish_count': scores['bearish_count'],
        'risk_count': scores['risk_count'],
        'warning_flags': scores['warning_flags'],
        'news_risk': _classify_news_risk(scores),
        'source': source,
        'headline_count': len(headlines),
        'cached': False,
    }

    _NEWS_CACHE[cache_k] = {'data': result, 'ts': time.time()}
    return result


def get_general_market_news(force_refresh: bool = False) -> dict:
    """
    Get broad market news (not symbol-specific).
    Used for session-open context by the AI Session Analyst.
    """
    cache_k = 'general:market_news'
    if not force_refresh and cache_k in _NEWS_CACHE:
        if not _is_stale(_NEWS_CACHE[cache_k]['ts']):
            return {**_NEWS_CACHE[cache_k]['data'], 'cached': True}

    headlines = []
    source = 'none'

    if CRYPTOPANIC_API_KEY and len(CRYPTOPANIC_API_KEY) > 5:
        fetched = _fetch_cryptopanic_general(CRYPTOPANIC_API_KEY)
        if fetched:
            headlines = fetched
            source = 'cryptopanic'

    if not headlines:
        headlines = _fetch_rss('https://www.coindesk.com/arc/outboundfeeds/rss/')
        if headlines:
            source = 'coindesk_rss'

    scores = _score_headlines(headlines)
    result = {
        'headlines': headlines[:10],
        'sentiment_score': scores['sentiment_score'],
        'bullish_count': scores['bullish_count'],
        'bearish_count': scores['bearish_count'],
        'news_risk': _classify_news_risk(scores),
        'warning_flags': scores['warning_flags'],
        'source': source,
        'headline_count': len(headlines),
        'cached': False,
    }

    _NEWS_CACHE[cache_k] = {'data': result, 'ts': time.time()}
    return result


def format_news_for_debate(symbol: str) -> str:
    """Return a concise news summary string for injection into debate prompts."""
    try:
        news = get_news_sentiment(symbol)
        if not news.get('headlines'):
            return ''
        risk = news['news_risk']
        score = news['sentiment_score']
        lines = [
            f"NEWS ({news['source']}): sentiment={score:+.2f}, risk={risk}",
        ]
        if news['warning_flags']:
            lines.append(f"  Risk flags: {', '.join(news['warning_flags'])}")
        if news['headlines']:
            lines.append(f"  Top headlines: {' | '.join(news['headlines'][:3])}")
        return '\n'.join(lines)
    except Exception as e:
        return f"[news_feed error: {e}]"
