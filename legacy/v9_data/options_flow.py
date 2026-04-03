"""
data/options_flow.py — Options market signals as market intelligence.

We don't trade options. But the options market is the smartest money in the room.
VIX term structure, VVIX, and SKEW encode fear, tail risk, and volatility regime
information that directy improves entry/exit timing for crypto and futures.

Signals computed:
  iv_rank          float  — VIX percentile (52-week). 0.0–1.0.
                            > 0.60 = HIGH IV → favor mean reversion setups.
                            < 0.30 = LOW IV  → favor momentum/breakout setups.
  vix_level        float  — raw VIX close
  vix9d_level      float  — 9-day VIX (near-term fear)
  vix3m_level      float  — 3-month VIX (intermediate fear)
  vvix_level       float  — VIX of VIX (vol-of-vol; spikes = regime shifts imminent)
  skew_level       float  — CBOE SKEW index (tail risk; > 130 = elevated tail risk)
  contango_ratio   float  — vix3m / vix. > 1.05 = calm (contango). < 0.95 = fear (backwardation).
  term_structure   str    — 'CONTANGO' | 'FLAT' | 'BACKWARDATION'
  iv_regime        str    — 'HIGH_IV' | 'NORMAL_IV' | 'LOW_IV'
  panic_signal     bool   — backwardation AND vvix > 95. Imminent volatility spike.
  tail_risk_elevated bool — SKEW > 130. Big players hedging downside.
  options_regime   str    — one-line summary for agent prompts

Cached 30 minutes (data changes slowly; prevents rate limiting on yfinance).
Fails silently — if yfinance is down, all fields return safe neutral defaults.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_cache: Optional[dict] = None
_cache_time: Optional[datetime] = None
_CACHE_MINUTES = 30


def get_options_signals() -> dict:
    """
    Fetch and compute all options market signals.
    Returns safe neutral defaults on any error — never raises.
    """
    global _cache, _cache_time

    if _cache is not None and _cache_time is not None:
        age = (datetime.now() - _cache_time).total_seconds() / 60
        if age < _CACHE_MINUTES:
            return dict(_cache)

    result = _fetch_options_signals()
    _cache = result
    _cache_time = datetime.now()
    return dict(result)


def _fetch_options_signals() -> dict:
    try:
        import yfinance as yf
        import pandas as pd

        # Download VIX complex + VVIX + SKEW — 1 year of daily data
        tickers = ['^VIX', '^VIX9D', '^VIX3M', '^VVIX', '^SKEW']
        data = yf.download(tickers, period='1y', interval='1d', progress=False, auto_adjust=True)

        # yfinance multi-ticker returns MultiIndex columns: (field, ticker)
        closes = data['Close'] if 'Close' in data.columns.get_level_values(0) else data

        def _latest(ticker: str) -> Optional[float]:
            try:
                col = closes[ticker].dropna()
                return float(col.iloc[-1]) if len(col) > 0 else None
            except Exception:
                return None

        def _series(ticker: str) -> Optional['pd.Series']:
            try:
                s = closes[ticker].dropna()
                return s if len(s) >= 20 else None
            except Exception:
                return None

        vix  = _latest('^VIX')
        vix9 = _latest('^VIX9D')
        v3m  = _latest('^VIX3M')
        vvix = _latest('^VVIX')
        skew = _latest('^SKEW')

        # ── IV rank: VIX percentile over 52 weeks ─────────────────────────────
        iv_rank: float = 0.50   # neutral default
        vix_series = _series('^VIX')
        if vix is not None and vix_series is not None and len(vix_series) >= 50:
            vix_52w_low  = float(vix_series.min())
            vix_52w_high = float(vix_series.max())
            if vix_52w_high > vix_52w_low:
                iv_rank = round((vix - vix_52w_low) / (vix_52w_high - vix_52w_low), 3)

        # ── Term structure ─────────────────────────────────────────────────────
        contango_ratio: float = 1.0
        term_structure = 'FLAT'
        if vix is not None and v3m is not None and vix > 0:
            contango_ratio = round(v3m / vix, 3)
            if contango_ratio > 1.05:
                term_structure = 'CONTANGO'
            elif contango_ratio < 0.95:
                term_structure = 'BACKWARDATION'

        # ── IV regime ─────────────────────────────────────────────────────────
        if iv_rank >= 0.60:
            iv_regime = 'HIGH_IV'
        elif iv_rank <= 0.30:
            iv_regime = 'LOW_IV'
        else:
            iv_regime = 'NORMAL_IV'

        # ── Composite signals ─────────────────────────────────────────────────
        panic_signal       = (term_structure == 'BACKWARDATION') and (vvix is not None and vvix > 95)
        tail_risk_elevated = (skew is not None and skew > 130)

        # ── One-line regime summary for agent prompts ─────────────────────────
        options_regime = _format_regime(
            iv_rank, iv_regime, term_structure, contango_ratio,
            vix, vvix, skew, panic_signal, tail_risk_elevated
        )

        return {
            'iv_rank':             iv_rank,
            'vix_level':           vix,
            'vix9d_level':         vix9,
            'vix3m_level':         v3m,
            'vvix_level':          vvix,
            'skew_level':          skew,
            'contango_ratio':      contango_ratio,
            'term_structure':      term_structure,
            'iv_regime':           iv_regime,
            'panic_signal':        panic_signal,
            'tail_risk_elevated':  tail_risk_elevated,
            'options_regime':      options_regime,
        }

    except Exception as e:
        print(f"[options_flow] Failed to fetch options signals: {e}")
        return _default_signals()


def _format_regime(iv_rank, iv_regime, term_structure, contango_ratio,
                   vix, vvix, skew, panic, tail_risk) -> str:
    parts = []

    if panic:
        parts.append("⚠️ PANIC: VIX backwardation + VVIX spike — volatility regime shift imminent")
    elif tail_risk:
        parts.append("⚠️ TAIL RISK: SKEW elevated — institutional downside hedging active")

    if vix is not None:
        parts.append(f"VIX={vix:.1f}")
    if vvix is not None:
        parts.append(f"VVIX={vvix:.1f}")
    if skew is not None:
        parts.append(f"SKEW={skew:.1f}")

    parts.append(f"TS={term_structure}({contango_ratio:.2f}x)")
    parts.append(f"IV_RANK={iv_rank:.0%}({iv_regime})")

    if iv_regime == 'HIGH_IV':
        parts.append("→ Favor mean-reversion entries")
    elif iv_regime == 'LOW_IV':
        parts.append("→ Favor momentum/breakout entries")

    return ' | '.join(parts)


def format_options_context(signals: dict) -> str:
    """One-line context string for injection into agent debate prompts."""
    if not signals or signals.get('options_regime') is None:
        return ''
    return f"OPTIONS FLOW: {signals['options_regime']}"


def invalidate_cache() -> None:
    """Force fresh fetch on next call."""
    global _cache, _cache_time
    _cache = None
    _cache_time = None


def _default_signals() -> dict:
    return {
        'iv_rank':             0.50,
        'vix_level':           None,
        'vix9d_level':         None,
        'vix3m_level':         None,
        'vvix_level':          None,
        'skew_level':          None,
        'contango_ratio':      1.0,
        'term_structure':      'FLAT',
        'iv_regime':           'NORMAL_IV',
        'panic_signal':        False,
        'tail_risk_elevated':  False,
        'options_regime':      'OPTIONS DATA UNAVAILABLE — proceeding without options context',
    }
