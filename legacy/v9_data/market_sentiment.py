"""
data/market_sentiment.py — Market sentiment aggregator.

Sources:
  1. Reddit (PRAW) — crowd sentiment from WSB/investing/stocks/CryptoCurrency
  2. Options market data (yfinance) — put/call ratio, IV rank, term structure, skew

This module does NOT trade options. It uses options market data as a fear/greed
signal to understand what institutional and retail players are positioned for.

Cache TTLs:
  - Reddit:  15 minutes (posts don't change fast)
  - Options: 30 minutes (options data is slow-moving intraday)
  - Snapshot: 5 minutes (combined output refreshed frequently)
"""
import os
import sys
import time
import math
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Module-level cache ────────────────────────────────────────────────────────
_REDDIT_CACHE:   dict = {}
_OPTIONS_CACHE:  dict = {}
_SNAPSHOT_CACHE: dict = {}

_REDDIT_TTL:   int = 900   # 15 minutes
_OPTIONS_TTL:  int = 1800  # 30 minutes
_SNAPSHOT_TTL: int = 300   # 5 minutes

# ── PRAW optional import ──────────────────────────────────────────────────────
try:
    import praw as _praw
    _PRAW_AVAILABLE = True
except ImportError:
    _praw = None
    _PRAW_AVAILABLE = False
    print("[sentiment] praw not installed — Reddit sentiment unavailable. "
          "Install with: pip install praw")


def _is_stale(ts: float, ttl: int) -> bool:
    return time.time() - ts > ttl


# ── Keyword lists ─────────────────────────────────────────────────────────────
_BULLISH_WORDS = frozenset([
    'bull', 'bullish', 'moon', 'mooning', 'long', 'calls', 'call',
    'buy', 'buying', 'breakout', 'pump', 'pumping', 'surge', 'surging',
    'rally', 'rip', 'ripping', 'green', 'gains', 'squeeze', 'yolo',
    'ath', 'up', 'upside', 'growth', 'strong', 'strength',
])

_BEARISH_WORDS = frozenset([
    'bear', 'bearish', 'short', 'puts', 'put', 'crash', 'crashing',
    'dump', 'dumping', 'sell', 'selling', 'collapse', 'correction',
    'fall', 'falling', 'drop', 'dropping', 'red', 'loss', 'losses',
    'recession', 'down', 'downside', 'weak', 'weakness', 'rekt',
])

# ── Ticker extraction helpers ─────────────────────────────────────────────────
import re as _re
_TICKER_PATTERN = _re.compile(r'\b[A-Z]{2,5}\b')
_COMMON_WORDS = frozenset([
    'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN',
    'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'DAY', 'GET', 'HAS', 'HIM',
    'HIS', 'HOW', 'ITS', 'NEW', 'NOW', 'OLD', 'SEE', 'TWO', 'WHO',
    'BOY', 'DID', 'HAD', 'HOT', 'PUT', 'SAY', 'SHE', 'TOO', 'USE',
    'IMO', 'EPS', 'CEO', 'CFO', 'WSB', 'ATH', 'YTD', 'QOQ', 'YOY',
    'IPO', 'ETF', 'SPX', 'VIX', 'OTM', 'ITM', 'ATM', 'DTE', 'IV',
    'DD', 'TA', 'FA', 'PT', 'TP', 'SL', 'PE', 'PB', 'EV',
])


def _extract_tickers(text: str) -> list:
    """Extract likely stock tickers from text (2-5 uppercase letters, not common words)."""
    candidates = _TICKER_PATTERN.findall(text)
    return [t for t in candidates if t not in _COMMON_WORDS]


def get_reddit_sentiment(
    subreddits: list = None,
    limit: int = 50,
) -> dict:
    """
    Scrape recent hot posts from financial subreddits and score bullish/bearish sentiment.

    Returns:
        {
            'score':        float,  # -1 (max bearish) to +1 (max bullish)
            'bullish_pct':  float,  # fraction of sentiment-bearing words that are bullish
            'bearish_pct':  float,  # fraction of sentiment-bearing words that are bearish
            'post_count':   int,    # number of posts analyzed
            'top_tickers':  list,   # most-mentioned tickers across posts
            'source':       str,    # 'reddit' | 'unavailable'
        }
    """
    if subreddits is None:
        subreddits = ['wallstreetbets', 'investing', 'stocks', 'CryptoCurrency']

    cache_k = 'reddit:sentiment'
    if cache_k in _REDDIT_CACHE:
        if not _is_stale(_REDDIT_CACHE[cache_k]['ts'], _REDDIT_TTL):
            return _REDDIT_CACHE[cache_k]['data']

    _neutral = {
        'score': 0.0,
        'bullish_pct': 0.0,
        'bearish_pct': 0.0,
        'post_count': 0,
        'top_tickers': [],
        'source': 'unavailable',
    }

    if not _PRAW_AVAILABLE:
        return _neutral

    try:
        from config import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
    except ImportError:
        REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID', '')
        REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET', '')
        REDDIT_USER_AGENT = os.getenv('REDDIT_USER_AGENT', 'AlgoTradingBot/1.0')

    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        print("[sentiment] Reddit credentials not set — skipping Reddit sentiment")
        return _neutral

    try:
        reddit = _praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )

        bullish_count = 0
        bearish_count = 0
        total_words   = 0
        ticker_counts: dict = {}
        post_count    = 0

        for sub_name in subreddits:
            try:
                sub = reddit.subreddit(sub_name)
                for post in sub.hot(limit=limit):
                    text = (post.title + ' ' + (post.selftext or '')).lower()
                    words = text.split()
                    post_count += 1

                    for w in words:
                        clean = w.strip('.,!?;:()')
                        if clean in _BULLISH_WORDS:
                            bullish_count += 1
                            total_words   += 1
                        elif clean in _BEARISH_WORDS:
                            bearish_count += 1
                            total_words   += 1

                    # Ticker extraction (uppercase matching from title only)
                    for ticker in _extract_tickers(post.title):
                        ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1

            except Exception as sub_err:
                print(f"[sentiment] r/{sub_name} fetch error: {sub_err}")
                continue

        if total_words == 0:
            return _neutral

        score       = (bullish_count - bearish_count) / total_words
        score       = max(-1.0, min(1.0, score))
        bullish_pct = bullish_count / total_words
        bearish_pct = bearish_count / total_words

        # Top tickers by mention count (exclude generic words already filtered)
        top_tickers = sorted(ticker_counts, key=ticker_counts.get, reverse=True)[:10]

        result = {
            'score':       round(score, 4),
            'bullish_pct': round(bullish_pct, 4),
            'bearish_pct': round(bearish_pct, 4),
            'post_count':  post_count,
            'top_tickers': top_tickers,
            'source':      'reddit',
        }

        _REDDIT_CACHE[cache_k] = {'data': result, 'ts': time.time()}
        print(f"[sentiment] Reddit: score={score:+.3f}, posts={post_count}, "
              f"bull={bullish_pct:.1%}, bear={bearish_pct:.1%}")
        return result

    except Exception as e:
        print(f"[sentiment] Reddit error: {e}")
        return _neutral


def get_options_market_signals(symbol: str = 'SPY') -> dict:
    """
    Derive market fear/greed signals from options market data via yfinance.

    Uses SPY options as a proxy for broad market sentiment.

    Returns:
        {
            'put_call_ratio':  float,  # total put OI / total call OI
            'iv_rank':         float,  # 0–100; current IV vs 52-week range
            'iv_percentile':   float,  # 0–100; fraction of days IV was below current
            'term_structure':  str,    # 'contango' | 'backwardation' | 'flat'
            'skew':            float,  # OTM put IV - OTM call IV (positive = fear)
            'market_fear':     str,    # 'extreme_fear'|'fear'|'neutral'|'greed'|'extreme_greed'
        }

    put_call_ratio interpretation:
        > 1.2  = bearish (puts dominate, hedging pressure)
        < 0.7  = bullish (calls dominate, speculative buying)
        0.7–1.2 = neutral
    """
    cache_k = f'options:{symbol}'
    if cache_k in _OPTIONS_CACHE:
        if not _is_stale(_OPTIONS_CACHE[cache_k]['ts'], _OPTIONS_TTL):
            return _OPTIONS_CACHE[cache_k]['data']

    _neutral = {
        'put_call_ratio': 1.0,
        'iv_rank':        50.0,
        'iv_percentile':  50.0,
        'term_structure': 'flat',
        'skew':           0.0,
        'market_fear':    'neutral',
    }

    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        expirations = ticker.options
        if not expirations:
            print(f"[sentiment] No options data for {symbol}")
            return _neutral

        # ── Put/Call ratio from front two expiries ────────────────────────────
        total_put_oi  = 0
        total_call_oi = 0
        skew_put_iv   = None
        skew_call_iv  = None

        for exp in expirations[:2]:
            try:
                chain = ticker.option_chain(exp)
                calls = chain.calls
                puts  = chain.puts

                if not calls.empty and 'openInterest' in calls.columns:
                    total_call_oi += int(calls['openInterest'].fillna(0).sum())
                if not puts.empty and 'openInterest' in puts.columns:
                    total_put_oi  += int(puts['openInterest'].fillna(0).sum())

                # Skew: OTM put IV vs OTM call IV at front expiry
                if skew_put_iv is None and exp == expirations[0]:
                    try:
                        spot = ticker.fast_info.last_price or ticker.history(period='1d')['Close'].iloc[-1]
                        spot = float(spot)

                        # OTM put: strike ~3% below spot
                        otm_put_strike = spot * 0.97
                        if not puts.empty and 'strike' in puts.columns and 'impliedVolatility' in puts.columns:
                            put_row = puts.iloc[(puts['strike'] - otm_put_strike).abs().argsort()[:1]]
                            if not put_row.empty:
                                skew_put_iv = float(put_row['impliedVolatility'].iloc[0])

                        # OTM call: strike ~3% above spot
                        otm_call_strike = spot * 1.03
                        if not calls.empty and 'strike' in calls.columns and 'impliedVolatility' in calls.columns:
                            call_row = calls.iloc[(calls['strike'] - otm_call_strike).abs().argsort()[:1]]
                            if not call_row.empty:
                                skew_call_iv = float(call_row['impliedVolatility'].iloc[0])
                    except Exception:
                        pass

            except Exception as chain_err:
                print(f"[sentiment] Option chain {exp} error: {chain_err}")
                continue

        put_call_ratio = (
            round(total_put_oi / total_call_oi, 3)
            if total_call_oi > 0 else 1.0
        )

        skew = 0.0
        if skew_put_iv is not None and skew_call_iv is not None and skew_call_iv > 0:
            skew = round(skew_put_iv - skew_call_iv, 4)

        # ── IV rank via VIX 52-week range ─────────────────────────────────────
        iv_rank       = 50.0
        iv_percentile = 50.0
        try:
            vix_ticker = yf.Ticker('^VIX')
            vix_hist   = vix_ticker.history(period='1y', interval='1d')
            if not vix_hist.empty and len(vix_hist) >= 20:
                vix_52w_high = float(vix_hist['Close'].max())
                vix_52w_low  = float(vix_hist['Close'].min())
                vix_current  = float(vix_hist['Close'].iloc[-1])

                rng = vix_52w_high - vix_52w_low
                if rng > 0:
                    iv_rank = round((vix_current - vix_52w_low) / rng * 100, 1)

                # IV percentile: % of days VIX was below current level
                below = (vix_hist['Close'] < vix_current).sum()
                iv_percentile = round(below / len(vix_hist) * 100, 1)
        except Exception as vix_err:
            print(f"[sentiment] VIX history error: {vix_err}")

        # ── Term structure: VIX vs VIX3M ──────────────────────────────────────
        term_structure = 'flat'
        try:
            vix3m_ticker  = yf.Ticker('^VIX3M')
            vix3m_info    = vix3m_ticker.fast_info
            vix3m_price   = getattr(vix3m_info, 'last_price', None)
            if vix3m_price is None:
                vix3m_hist = vix3m_ticker.history(period='1d', interval='5m')
                if not vix3m_hist.empty:
                    vix3m_price = float(vix3m_hist['Close'].iloc[-1])

            vix_spot_ticker = yf.Ticker('^VIX')
            vix_spot_info   = vix_spot_ticker.fast_info
            vix_spot        = getattr(vix_spot_info, 'last_price', None)
            if vix_spot is None:
                vix_spot_hist = vix_spot_ticker.history(period='1d', interval='5m')
                if not vix_spot_hist.empty:
                    vix_spot = float(vix_spot_hist['Close'].iloc[-1])

            if vix_spot and vix3m_price:
                vix_spot    = float(vix_spot)
                vix3m_price = float(vix3m_price)
                ratio       = vix_spot / vix3m_price
                if ratio < 0.95:
                    term_structure = 'contango'      # near-term calm vs medium-term elevated = normal
                elif ratio > 1.05:
                    term_structure = 'backwardation'  # near-term fear > 3-month = stressed
                else:
                    term_structure = 'flat'
        except Exception as ts_err:
            print(f"[sentiment] Term structure error: {ts_err}")

        # ── Market fear label ─────────────────────────────────────────────────
        # Composite: P/C ratio + IV rank + term structure
        fear_score = 0.0

        if put_call_ratio > 1.2:
            fear_score += 2.0
        elif put_call_ratio > 0.9:
            fear_score += 0.5
        elif put_call_ratio < 0.7:
            fear_score -= 1.5
        else:
            fear_score -= 0.5

        if iv_rank > 70:
            fear_score += 2.0
        elif iv_rank > 50:
            fear_score += 1.0
        elif iv_rank < 25:
            fear_score -= 1.0

        if term_structure == 'backwardation':
            fear_score += 1.5
        elif term_structure == 'contango':
            fear_score -= 0.5

        if skew > 0.05:
            fear_score += 0.5

        if fear_score >= 4.0:
            market_fear = 'extreme_fear'
        elif fear_score >= 2.0:
            market_fear = 'fear'
        elif fear_score <= -2.0:
            market_fear = 'extreme_greed'
        elif fear_score <= -1.0:
            market_fear = 'greed'
        else:
            market_fear = 'neutral'

        result = {
            'put_call_ratio': put_call_ratio,
            'iv_rank':        iv_rank,
            'iv_percentile':  iv_percentile,
            'term_structure': term_structure,
            'skew':           skew,
            'market_fear':    market_fear,
        }

        _OPTIONS_CACHE[cache_k] = {'data': result, 'ts': time.time()}
        print(f"[sentiment] Options({symbol}): P/C={put_call_ratio:.2f}, "
              f"IV_rank={iv_rank:.0f}, term={term_structure}, fear={market_fear}")
        return result

    except Exception as e:
        print(f"[sentiment] Options market signals error: {e}")
        return _neutral


def _options_score_to_float(options: dict) -> float:
    """
    Convert options signals to a -1 to +1 float for blending.
    Bearish options environment = negative, bullish = positive.
    """
    score = 0.0
    weight_sum = 0.0

    # P/C ratio: weight 0.5
    pcr = options.get('put_call_ratio', 1.0)
    if pcr > 1.2:
        score += -0.8 * 0.5
    elif pcr > 0.9:
        score += -0.2 * 0.5
    elif pcr < 0.7:
        score += 0.8 * 0.5
    else:
        score += 0.2 * 0.5
    weight_sum += 0.5

    # IV rank: weight 0.3 — high IV = fear = bearish signal
    iv = options.get('iv_rank', 50.0)
    if iv > 70:
        score += -0.8 * 0.3
    elif iv > 50:
        score += -0.3 * 0.3
    elif iv < 25:
        score += 0.5 * 0.3
    else:
        score += 0.0 * 0.3
    weight_sum += 0.3

    # Term structure: weight 0.2
    ts = options.get('term_structure', 'flat')
    if ts == 'backwardation':
        score += -0.6 * 0.2
    elif ts == 'contango':
        score += 0.3 * 0.2
    weight_sum += 0.2

    return round(score / weight_sum if weight_sum > 0 else 0.0, 4)


def get_market_sentiment_snapshot(force_refresh: bool = False) -> dict:
    """
    Unified sentiment snapshot combining Reddit crowd sentiment + options market signals.

    Returns:
        {
            'reddit':          dict,   # from get_reddit_sentiment()
            'options':         dict,   # from get_options_market_signals()
            'combined_score':  float,  # weighted blend: reddit 30% + options 70%
            'label':           str,    # 'VERY_BEARISH'|'BEARISH'|'NEUTRAL'|'BULLISH'|'VERY_BULLISH'
            'debate_context':  str,    # formatted string for injection into debates
            'avoid_long':      bool,   # True if combined_score < -0.4
            'ts':              float,
        }
    """
    cache_k = 'snapshot:sentiment'
    if not force_refresh and cache_k in _SNAPSHOT_CACHE:
        if not _is_stale(_SNAPSHOT_CACHE[cache_k]['ts'], _SNAPSHOT_TTL):
            return _SNAPSHOT_CACHE[cache_k]['data']

    reddit  = get_reddit_sentiment()
    options = get_options_market_signals()

    reddit_score  = reddit.get('score', 0.0)
    options_score = _options_score_to_float(options)

    # Weighted blend: options 70% (more reliable), Reddit 30%
    if reddit.get('source') == 'unavailable':
        combined_score = options_score
    else:
        combined_score = round(0.30 * reddit_score + 0.70 * options_score, 4)

    combined_score = max(-1.0, min(1.0, combined_score))

    if combined_score >= 0.4:
        label = 'VERY_BULLISH'
    elif combined_score >= 0.15:
        label = 'BULLISH'
    elif combined_score <= -0.4:
        label = 'VERY_BEARISH'
    elif combined_score <= -0.15:
        label = 'BEARISH'
    else:
        label = 'NEUTRAL'

    avoid_long = combined_score < -0.4

    # ── Debate context string ─────────────────────────────────────────────────
    pcr         = options.get('put_call_ratio', 1.0)
    iv_rank     = options.get('iv_rank', 50.0)
    term        = options.get('term_structure', 'flat')
    market_fear = options.get('market_fear', 'neutral')
    r_posts     = reddit.get('post_count', 0)
    r_score     = reddit.get('score', 0.0)

    pcr_label  = ('bearish' if pcr > 1.2 else 'bullish' if pcr < 0.7 else 'neutral')
    iv_label   = ('elevated' if iv_rank > 60 else 'low' if iv_rank < 30 else 'normal')
    reddit_str = (f"Reddit: {r_score:+.2f} ({r_posts} posts)"
                  if reddit.get('source') == 'reddit' else "Reddit: unavailable")

    debate_context = (
        f"MARKET SENTIMENT: {label} (score {combined_score:+.2f}) | "
        f"P/C ratio {pcr:.2f} ({pcr_label}) | "
        f"IV rank {iv_rank:.0f} ({iv_label}) | "
        f"Options term: {term} | "
        f"Fear gauge: {market_fear} | "
        f"{reddit_str}"
    )

    result = {
        'reddit':         reddit,
        'options':        options,
        'combined_score': combined_score,
        'label':          label,
        'debate_context': debate_context,
        'avoid_long':     avoid_long,
        'ts':             time.time(),
    }

    _SNAPSHOT_CACHE[cache_k] = {'data': result, 'ts': time.time()}
    print(f"[sentiment] Snapshot: {label} ({combined_score:+.3f}), "
          f"avoid_long={avoid_long}")
    return result
