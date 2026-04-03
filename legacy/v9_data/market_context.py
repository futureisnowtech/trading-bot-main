"""
data/market_context.py — Unified market context assembler.

Pulls together:
  - Session metadata (current trading session window + quality)
  - News sentiment (data/news_feed.py)
  - Macro snapshot (data/macro_feed.py)

Consumed by:
  - debate_engine.py — enriches every debate with macro + news overlay
  - strategies/ai_agents/session_analyst.py — session-open analysis (future)
  - scheduler/job_runner.py — pre-scan context gate and conviction adjustment

Context is cached at 5 minutes (updated more frequently than macro/news sources
to ensure session changes are reflected promptly).
"""
import os, sys, time
from datetime import datetime
from typing import Optional
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MARKET_TIMEZONE, CRYPTO_PAIRS

_CTX_CACHE: dict = {}
_CACHE_TTL: int = 300   # 5 minutes


def _is_stale(ts: float) -> bool:
    return time.time() - ts > _CACHE_TTL


def get_current_session() -> dict:
    """
    Detect the current trading session based on Eastern time.

    Session quality reflects expected edge and liquidity:
      HIGH     → elevated volume, documented intraday predictability
      MEDIUM   → moderate activity, signals still valid
      LOW      → thin book, momentum signals weaker
      BLOCKED  → system hard-blocks new entries (dead zone 2am–3am ET)
    """
    tz = pytz.timezone(MARKET_TIMEZONE)
    now = datetime.now(tz)
    h = now.hour + now.minute / 60.0

    # Dead zone first (hard override) — 2am-3am ET only; London opens at 3am
    if 2.0 <= h < 3.0:
        return {
            'session': 'DEAD_ZONE',
            'session_quality': 'BLOCKED',
            'hour_et': round(h, 2),
            'notes': 'Dead zone 2am–3am ET: system blocks new entries. Pre-London gap, no edge.',
            'timestamp': now.isoformat(),
        }

    if 3.0 <= h < 8.0:
        session, quality = 'LONDON', 'HIGH'
        notes = ('London open: best breakout window for crypto. EUR institutional flow. '
                 'BTC/ETH correlation to EUR equities highest here. '
                 'Momentum signals most reliable — 3am-5am typically has the cleanest moves.')
    elif 8.0 <= h < 9.5:
        session, quality = 'PREMARKET', 'MEDIUM'
        notes = 'Premarket: US futures active. Watch for overnight gap fills and macro data releases.'
    elif 9.5 <= h < 12.0:
        session, quality = 'NY_OPEN', 'HIGH'
        notes = ('NY open: highest equity volatility. '
                 'Crypto often follows SPY momentum intraday. '
                 'Volume-confirmed breakouts most reliable 9:30-11:30am ET.')
    elif 12.0 <= h < 16.0:
        session, quality = 'NY_AFTERNOON', 'MEDIUM'
        notes = 'NY afternoon: lower vol. Continuation trades preferred over new breakouts.'
    elif 16.0 <= h < 20.0:
        session, quality = 'AFTERHOURS', 'LOW'
        notes = 'After hours: equity closed. Crypto continues but thinner liquidity on alts.'
    else:
        # 20:00 ET → 03:00 ET next day = Asia session
        session, quality = 'ASIA', 'MEDIUM'
        notes = ('Asia session: BTC/ETH active (Japanese/Korean/Singaporean flows). '
                 'Lower liquidity on small-cap alts. JPY risk-on/off moves can lead crypto.')

    return {
        'session': session,
        'session_quality': quality,
        'hour_et': round(h, 2),
        'notes': notes,
        'timestamp': now.isoformat(),
    }


def get_full_market_context(
    symbol: Optional[str] = None,
    include_news: bool = True,
    include_macro: bool = True,
    force_refresh: bool = False,
) -> dict:
    """
    Assemble the complete market context for a symbol (or market-wide).

    Returns a structured dict with session info, news, macro, and derived signals.
    The 'summary' field is a ready-to-inject one-liner for debate agent context.
    """
    cache_k = f"ctx:{symbol or 'market'}"
    if not force_refresh and cache_k in _CTX_CACHE:
        if not _is_stale(_CTX_CACHE[cache_k]['ts']):
            return {**_CTX_CACHE[cache_k]['data'], 'cached': True}

    ctx: dict = {}

    # ── Session ───────────────────────────────────────────────────────────────
    ctx['session'] = get_current_session()

    # ── News ──────────────────────────────────────────────────────────────────
    if include_news:
        try:
            from data.news_feed import get_news_sentiment, get_general_market_news
            ctx['news'] = (get_news_sentiment(symbol) if symbol
                           else get_general_market_news())
        except Exception as e:
            ctx['news'] = {'sentiment_score': 0.0, 'news_risk': 'UNKNOWN',
                           'headlines': [], 'warning_flags': [], 'error': str(e)}

    # ── Macro ─────────────────────────────────────────────────────────────────
    if include_macro:
        try:
            from data.macro_feed import get_macro_snapshot
            ctx['macro'] = get_macro_snapshot(
                symbols_of_interest=[symbol] if symbol else CRYPTO_PAIRS[:4]
            )
        except Exception as e:
            ctx['macro'] = {'risk_regime': 'UNKNOWN', 'macro_bias': 'neutral',
                            'macro_notes': [], 'vix': None, 'macro_score': 0,
                            'funding_rates': {}, 'error': str(e)}

    # ── Derived: no-trade flags and conviction hints ──────────────────────────
    no_trade_flags = []
    conviction_hints = []

    session_info = ctx.get('session', {})
    if session_info.get('session') == 'DEAD_ZONE':
        no_trade_flags.append('DEAD_ZONE: hard block on entries 2am-5am ET')

    news = ctx.get('news', {})
    if news.get('news_risk') == 'HIGH':
        no_trade_flags.append(
            f"HIGH_NEWS_RISK: {', '.join(news.get('warning_flags', []))}"
        )
    elif news.get('sentiment_score', 0) > 0.35:
        conviction_hints.append(
            f"Bullish news sentiment: {news.get('sentiment_score', 0):+.2f}"
        )
    elif news.get('sentiment_score', 0) < -0.35:
        no_trade_flags.append(
            f"Bearish news sentiment: {news.get('sentiment_score', 0):+.2f}"
        )

    macro = ctx.get('macro', {})
    macro_regime = macro.get('risk_regime', 'NEUTRAL')
    macro_notes = macro.get('macro_notes', [])

    if macro_regime == 'RISK_OFF':
        no_trade_flags.append(
            f"RISK_OFF macro: {' | '.join(macro_notes[:2])}"
        )
    elif macro_regime == 'RISK_ON':
        conviction_hints.append(
            f"RISK_ON macro: {' | '.join(macro_notes[:2])}"
        )

    vix = macro.get('vix')
    if vix and vix > 30:
        no_trade_flags.append(f"VIX EXTREME FEAR: {vix:.1f} — capital preservation mode")
    elif vix and vix > 25:
        no_trade_flags.append(f"VIX FEAR: {vix:.1f} — elevated risk, reduce sizing")

    # Funding rate check for the specific symbol
    if symbol and macro.get('funding_rates'):
        fr = macro['funding_rates'].get(symbol, {})
        signal = fr.get('signal', 'unknown')
        rate = fr.get('rate_pct')
        if signal == 'overheated_long':
            no_trade_flags.append(
                f"Overheated longs: funding {rate:.4f}%/8h — longs crowded, fade risk"
            )
        elif signal == 'short_heavy':
            conviction_hints.append(
                f"Short-heavy funding {rate:.4f}%/8h — contrarian long signal"
            )

    ctx['no_trade_flags'] = no_trade_flags
    ctx['conviction_hints'] = conviction_hints
    ctx['has_blocks'] = len(no_trade_flags) > 0

    # ── One-line summary for agent prompts ───────────────────────────────────
    news_score = news.get('sentiment_score', 0)
    session_label = session_info.get('session', '?')
    session_q = session_info.get('session_quality', '?')
    macro_score = macro.get('macro_score', 0)

    ctx['summary'] = (
        f"[CONTEXT] Session: {session_label} ({session_q}). "
        f"Macro: {macro_regime} (score={macro_score:+d}) | "
        f"VIX: {vix:.1f if vix else '?'}. "
        f"News: {news_score:+.2f}. "
        + (f"BLOCKS: {'; '.join(no_trade_flags[:2])}. " if no_trade_flags
           else "No macro blocks. ")
        + (f"Tailwinds: {'; '.join(conviction_hints[:2])}." if conviction_hints else "")
    ).strip()

    ctx['cached'] = False
    _CTX_CACHE[cache_k] = {'data': ctx, 'ts': time.time()}
    return ctx


def get_context_for_debate(symbol: str, market_data: dict) -> str:
    """
    Return a concise multi-line context string for injection into debate agent prompts.
    Includes session, macro regime, news risk, any no-trade flags, and market sentiment.
    Called by debate_engine.run_debate() to enrich every agent's user prompt.
    """
    try:
        ctx = get_full_market_context(symbol=symbol, include_news=True, include_macro=True)
        lines = [ctx.get('summary', '')]

        macro = ctx.get('macro', {})
        if macro.get('macro_notes'):
            lines.append('Macro notes: ' + ' | '.join(macro['macro_notes'][:4]))

        news = ctx.get('news', {})
        if news.get('headlines'):
            lines.append(
                f"Recent headlines ({news.get('source', '?')}): "
                + ' | '.join(news['headlines'][:3])
            )

        # Funding rate for this specific symbol
        fr = macro.get('funding_rates', {}).get(symbol, {})
        if fr.get('rate_pct') is not None:
            lines.append(
                f"Funding rate ({symbol}): {fr['rate_pct']:.4f}%/8h → {fr['signal']}"
            )

        no_trade_flags = ctx.get('no_trade_flags', [])
        if no_trade_flags:
            lines.append('⚠️  MACRO/NEWS BLOCKS: ' + ' | '.join(no_trade_flags))

        conviction_hints = ctx.get('conviction_hints', [])
        if conviction_hints:
            lines.append('✅ Tailwinds: ' + ' | '.join(conviction_hints))

        # ── Market sentiment (Reddit + options market signals) ────────────────
        try:
            from data.market_sentiment import get_market_sentiment_snapshot
            sentiment = get_market_sentiment_snapshot()
            if sentiment.get('debate_context'):
                lines.append(sentiment['debate_context'])
            # Expose avoid_long flag so callers can check it
            ctx['avoid_long'] = sentiment.get('avoid_long', False)
        except Exception:
            pass  # Fail-open: sentiment is enrichment, not a gate

        return '\n'.join(filter(None, lines))

    except Exception as e:
        return f"[market_context unavailable: {e}]"


def should_block_trade(symbol: str) -> tuple:
    """
    Quick gate: returns (should_block: bool, reason: str).
    Called by job_runner.py before debate to avoid wasting API tokens.
    """
    try:
        ctx = get_full_market_context(symbol=symbol, include_news=True, include_macro=True)
        if ctx.get('has_blocks'):
            flags = ctx.get('no_trade_flags', [])
            return True, ' | '.join(flags[:2])
        return False, ''
    except Exception:
        return False, ''   # Fail open — don't block on context errors
