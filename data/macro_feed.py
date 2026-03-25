"""
data/macro_feed.py — Macro context: cross-asset prices, VIX, funding rates.

All data is fetched from free/public sources:
  - yfinance: DXY, SPY, GLD, VIX, TLT (no API key needed)
  - Coinglass public API: perpetual funding rates (no key needed)

The macro context tells us whether the broader market environment is
risk-on or risk-off, which directly affects crypto entry conviction.

Cache TTL: 15 minutes (macro data is slow-moving).
"""
import os, sys, json, time
import urllib.request
import urllib.error
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_MACRO_CACHE: dict = {}
_CACHE_TTL: int = 900   # 15 minutes


def _is_stale(ts: float) -> bool:
    return time.time() - ts > _CACHE_TTL


def _fetch_yf_price(ticker: str) -> Optional[float]:
    """Fetch the latest price for a ticker via yfinance."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = getattr(info, 'last_price', None)
        if price is None:
            hist = t.history(period='1d', interval='5m')
            if not hist.empty:
                price = float(hist['Close'].iloc[-1])
        return float(price) if price is not None else None
    except Exception as e:
        print(f"[macro_feed] yfinance price error {ticker}: {e}")
        return None


def _fetch_yf_change(ticker: str) -> Optional[float]:
    """Fetch the 1-day % change for a ticker via yfinance."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period='2d', interval='1d')
        if len(hist) >= 2:
            prev = float(hist['Close'].iloc[-2])
            curr = float(hist['Close'].iloc[-1])
            return round((curr - prev) / prev * 100, 3)
        return 0.0
    except Exception as e:
        print(f"[macro_feed] yfinance change error {ticker}: {e}")
        return None


def _fetch_funding_rates() -> dict:
    """
    Fetch perpetual funding rates from Coinglass public endpoint.
    Returns dict of {BASE_SYMBOL: rate_pct_8h}.
    Funding rate > 0 = longs paying shorts (bullish but can signal overheating).
    Funding rate < 0 = shorts paying longs (bearish dominance).
    """
    result = {}
    # Coinglass public API (no auth required for basic endpoint)
    urls_to_try = [
        "https://open-api.coinglass.com/public/v2/funding",
    ]
    for url in urls_to_try:
        try:
            req = urllib.request.Request(
                url, headers={'User-Agent': 'AlgoBot/1.0', 'accept': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                for item in data.get('data', []):
                    sym = item.get('symbol', '')
                    for ex in item.get('uMarginList', []):
                        if ex.get('exchangeName', '').lower() in ('binance', 'bybit', 'okx'):
                            rate = ex.get('fundingRate')
                            if rate is not None:
                                result[sym.upper()] = round(float(rate) * 100, 5)
                            break
            if result:
                break
        except Exception as e:
            print(f"[macro_feed] Coinglass error: {e}")

    return result


def _interpret_funding_for_symbols(raw_rates: dict, symbols: list) -> dict:
    """
    Classify funding rate signal for a list of trading symbols.
    E.g. 'BTC-USDC' → looks up 'BTC' in raw_rates.
    """
    out = {}
    for sym in symbols:
        base = sym.split('-')[0].split('/')[0].upper()
        rate = raw_rates.get(base, raw_rates.get(base + 'USDT', None))

        if rate is None:
            out[sym] = {'rate_pct': None, 'signal': 'unknown'}
        elif rate > 0.05:
            out[sym] = {'rate_pct': rate, 'signal': 'overheated_long'}   # longs paying a lot
        elif rate > 0.01:
            out[sym] = {'rate_pct': rate, 'signal': 'mildly_long'}
        elif rate < -0.01:
            out[sym] = {'rate_pct': rate, 'signal': 'short_heavy'}       # short bias = bearish dominance
        else:
            out[sym] = {'rate_pct': rate, 'signal': 'neutral'}
    return out


def get_macro_snapshot(symbols_of_interest: Optional[list] = None,
                       force_refresh: bool = False) -> dict:
    """
    Fetch and return a full macro context snapshot.

    Returns:
        {
            'dxy':            float | None,   # US Dollar Index (strong DXY = risk-off for crypto)
            'dxy_change':     float | None,   # DXY 1-day % change
            'spy_change':     float | None,   # SPY 1-day % change
            'gold_change':    float | None,   # Gold 1-day % change (safe-haven demand)
            'vix':            float | None,   # VIX level (>20 = elevated, >25 = fear)
            'tlt_change':     float | None,   # Long bond change (rising = flight to safety)
            'btc_change':     float | None,   # BTC 1-day % change (crypto pulse)
            'risk_regime':    str,            # 'RISK_ON' | 'RISK_OFF' | 'NEUTRAL'
            'macro_bias':     str,            # 'bullish' | 'bearish' | 'neutral' for crypto
            'vix_regime':     str,            # 'fear' | 'neutral' | 'complacent'
            'funding_rates':  dict,           # symbol → {rate_pct, signal}
            'macro_notes':    list[str],      # human-readable key observations
            'macro_score':    int,            # -5 to +5 risk scoring
            'cached':         bool,
        }
    """
    cache_k = 'macro:snapshot'
    if not force_refresh and cache_k in _MACRO_CACHE:
        if not _is_stale(_MACRO_CACHE[cache_k]['ts']):
            return {**_MACRO_CACHE[cache_k]['data'], 'cached': True}

    # ── Fetch all cross-asset data ────────────────────────────────────────────
    spy_change  = _fetch_yf_change('SPY')
    dxy         = _fetch_yf_price('DX-Y.NYB')
    dxy_change  = _fetch_yf_change('DX-Y.NYB')
    gold_change = _fetch_yf_change('GC=F')
    vix         = _fetch_yf_price('^VIX')
    tlt_change  = _fetch_yf_change('TLT')
    btc_change  = _fetch_yf_change('BTC-USD')

    # ── Regime scoring: +positive = risk-on, -negative = risk-off ────────────
    macro_score = 0
    macro_notes = []

    if spy_change is not None:
        if spy_change < -1.5:
            macro_score -= 2
            macro_notes.append(f"SPY -${spy_change:.1f}% — significant equity weakness")
        elif spy_change < -0.5:
            macro_score -= 1
            macro_notes.append(f"SPY {spy_change:.1f}% — mild equity pressure")
        elif spy_change > 1.0:
            macro_score += 2
            macro_notes.append(f"SPY +{spy_change:.1f}% — strong equity risk-on")
        elif spy_change > 0.3:
            macro_score += 1
            macro_notes.append(f"SPY +{spy_change:.1f}% — mild equity strength")

    if dxy_change is not None:
        if dxy_change > 0.5:
            macro_score -= 2
            macro_notes.append(f"DXY +{dxy_change:.2f}% — strong USD = risk-off, crypto headwind")
        elif dxy_change > 0.2:
            macro_score -= 1
            macro_notes.append(f"DXY +{dxy_change:.2f}% — USD strengthening, mild headwind")
        elif dxy_change < -0.3:
            macro_score += 1
            macro_notes.append(f"DXY {dxy_change:.2f}% — USD weakening, crypto tailwind")

    if vix is not None:
        if vix > 30:
            macro_score -= 3
            macro_notes.append(f"VIX {vix:.1f} — EXTREME FEAR. Capital preservation mode.")
        elif vix > 25:
            macro_score -= 2
            macro_notes.append(f"VIX {vix:.1f} — Fear territory. Reduce sizing, no chasing.")
        elif vix > 20:
            macro_score -= 1
            macro_notes.append(f"VIX {vix:.1f} — Elevated. Use caution on breakouts.")
        elif vix < 13:
            macro_score += 1
            macro_notes.append(f"VIX {vix:.1f} — Very low (complacent). Trending regime favored.")
        else:
            macro_notes.append(f"VIX {vix:.1f} — Calm, normal range.")

    if gold_change is not None and gold_change > 0.7:
        macro_score -= 1
        macro_notes.append(f"Gold +{gold_change:.1f}% — safe-haven flows active")

    if tlt_change is not None and tlt_change > 0.5:
        macro_score -= 1
        macro_notes.append(f"TLT +{tlt_change:.1f}% — flight to bonds (risk-off)")

    if btc_change is not None:
        if btc_change > 3.0:
            macro_score += 1
            macro_notes.append(f"BTC +{btc_change:.1f}% — crypto broadly bullish")
        elif btc_change < -3.0:
            macro_score -= 1
            macro_notes.append(f"BTC {btc_change:.1f}% — crypto broadly weak")

    # ── Classify regime ───────────────────────────────────────────────────────
    if macro_score >= 2:
        risk_regime = 'RISK_ON'
        macro_bias = 'bullish'
    elif macro_score <= -2:
        risk_regime = 'RISK_OFF'
        macro_bias = 'bearish'
    else:
        risk_regime = 'NEUTRAL'
        macro_bias = 'neutral'

    vix_regime = ('fear' if vix and vix > 25 else
                  'complacent' if vix and vix < 13 else
                  'neutral' if vix else 'unknown')

    # ── Funding rates ─────────────────────────────────────────────────────────
    syms = symbols_of_interest or ['BTC-USDC', 'ETH-USDC', 'SOL-USDC', 'AVAX-USDC']
    raw_rates = _fetch_funding_rates()
    funding_rates = _interpret_funding_for_symbols(raw_rates, syms)

    result = {
        'dxy':          dxy,
        'dxy_change':   dxy_change,
        'spy_change':   spy_change,
        'gold_change':  gold_change,
        'vix':          vix,
        'tlt_change':   tlt_change,
        'btc_change':   btc_change,
        'risk_regime':  risk_regime,
        'macro_bias':   macro_bias,
        'vix_regime':   vix_regime,
        'funding_rates': funding_rates,
        'macro_notes':  macro_notes,
        'macro_score':  macro_score,
        'cached':       False,
    }

    _MACRO_CACHE[cache_k] = {'data': result, 'ts': time.time()}
    return result


def format_macro_for_debate(symbol: str) -> str:
    """Return a concise macro summary string for injection into debate prompts."""
    try:
        from config import CRYPTO_PAIRS
        macro = get_macro_snapshot(symbols_of_interest=[symbol])
        lines = [
            f"MACRO: regime={macro['risk_regime']} | VIX={macro.get('vix','?')} ({macro['vix_regime']}) "
            f"| SPY={macro.get('spy_change','?')}% | DXY={macro.get('dxy_change','?')}% | macro_score={macro.get('macro_score', 0)}"
        ]
        if macro['macro_notes']:
            lines.append("  " + ' | '.join(macro['macro_notes'][:3]))

        fr = macro['funding_rates'].get(symbol, {})
        if fr.get('rate_pct') is not None:
            lines.append(f"  Funding rate ({symbol}): {fr['rate_pct']:.4f}%/8h → {fr['signal']}")

        return '\n'.join(lines)
    except Exception as e:
        return f"[macro_feed error: {e}]"
