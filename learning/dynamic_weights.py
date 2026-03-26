"""
learning/dynamic_weights.py — Bayesian conviction scoring engine.

Replaces the hardcoded conviction point blocks in job_runner.py.
Loads current Bayesian weights from signal_stats table.
Falls back to hardcoded priors when a signal has < MIN_FIRES_TO_LEARN fires.

Usage in job_runner:
    from learning.dynamic_weights import get_conviction_score, get_weights_snapshot

    conviction, breakdown = get_conviction_score(signals_active, regime)
    # signals_active: dict of {signal_name: bool}
"""
import os
import sys
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from learning.signal_performance import (
    get_signal_bayesian_pts,
    get_all_weights,
    SIGNAL_PRIOR_PTS,
)

# Cache weights for 5 minutes to avoid repeated DB reads during hot scan loops
import time
_weight_cache: dict = {}
_cache_ts: float = 0
_CACHE_TTL = 300  # 5 minutes


def _load_weights(regime: str) -> dict[str, float]:
    global _weight_cache, _cache_ts
    now = time.time()
    cache_key = regime
    if cache_key in _weight_cache and (now - _cache_ts) < _CACHE_TTL:
        return _weight_cache[cache_key]
    weights = get_all_weights(regime)
    _weight_cache[cache_key] = weights
    _cache_ts = now
    return weights


def invalidate_cache():
    """Call after a trade closes to force weight reload on next scan."""
    global _weight_cache, _cache_ts
    _weight_cache = {}
    _cache_ts = 0


# ── Map raw market_data signals to canonical signal names ─────────────────────
# This mirrors the extraction logic in post_trade_analyzer but for scoring
# (we need this at ENTRY time, not exit time)

def market_data_to_signals(md: dict) -> dict[str, bool]:
    """
    Convert a market_data dict to {canonical_signal_name: bool}.
    Mirrors post_trade_analyzer.extract_signals_from_market_data exactly.
    """
    def _b(key, default=False):
        v = md.get(key, default)
        return bool(v) if v is not None else default

    def _f(key, default=0.0):
        try:
            return float(md.get(key) or default)
        except Exception:
            return default

    return {
        'macd_consensus':       _b('macd_consensus'),
        'williams_r':           _f('williams_r', 0) <= -80,
        'momentum_volume':      _f('momentum_score', 0) > 0.6 and _f('vol_spike', 1) > 1.3,
        'squeeze_fired':        _b('squeeze_fired') and _f('squeeze_bars', 0) >= 20,
        'rv_expansion':         (_f('rv_ratio') or 0) >= 1.3,
        'kalman_deviation':     (_f('kalman_dev', 0) or 0) <= -0.01,
        'avwap_deviation':      (_f('avwap_dev', 0) or 0) <= -0.005,
        'ou_halflife':          3 <= (_f('ou_halflife_minutes', 0) or 0) <= 60,
        'kyle_lambda':          0 < (_f('kyle_lambda_pct', 100) or 100) <= 30,
        'supertrend_bullish':   _b('supertrend_bullish'),
        'wavetrend_cross':      _b('wt_oversold_cross'),
        'ichimoku_bullish':     _b('cloud_bullish'),
        'fisher_cross_up':      _b('fisher_cross_up'),
        'lrsi_oversold':        (_f('lrsi', 0.5) or 0.5) < 0.15,
        'wae_bullish_exploding': _b('wae_bullish') and _b('wae_exploding'),
        'wae_bullish':          _b('wae_bullish') and not _b('wae_exploding'),
        'chop_trending':        _b('chop_trending'),
        'lrsi_mild_oversold':   0.15 <= (_f('lrsi', 0.5) or 0.5) < 0.25,
        'tradingview_signal':   _b('tv_signal_active'),
    }


def _load_meta_adjustments(regime: str) -> dict[str, float]:
    """Load meta-learner weight adjustments (delta pts per signal)."""
    try:
        from learning.meta_learner import get_meta_weight_adjustments
        return get_meta_weight_adjustments(regime)
    except Exception:
        return {}


def get_conviction_score(
    market_data: dict,
    regime: Optional[str] = None,
) -> tuple[float, dict]:
    """
    Compute total conviction score using Bayesian weights + meta adjustments.

    Returns:
        (total_score, breakdown_dict)
        breakdown_dict: {signal_name: pts_contributed}

    Layer 1: Bayesian weights (shift from priors based on live win rates)
    Layer 2: Meta-learner adjustments (AI-identified pattern corrections)
    """
    regime = regime or str(market_data.get('regime', 'any') or 'any').lower()
    weights = _load_weights(regime)

    # Meta-learner delta adjustments on top of Bayesian weights
    meta_adj = _load_meta_adjustments(regime)

    signals = market_data_to_signals(market_data)
    breakdown = {}
    total = 0.0

    for sig_name, active in signals.items():
        if not active:
            continue
        base_pts = weights.get(sig_name, float(SIGNAL_PRIOR_PTS.get(sig_name, 0)))
        delta    = meta_adj.get(sig_name, 0.0)
        pts      = max(0.0, base_pts + delta)   # never go negative
        if pts > 0:
            breakdown[sig_name] = round(pts, 1)
            total += pts

    return round(total, 1), breakdown


def get_weights_snapshot(regime: str = 'any') -> dict:
    """
    Return current weights vs priors for dashboard / brain notes.
    Shows which signals have drifted from their hardcoded baseline.
    """
    weights = get_all_weights(regime)
    snapshot = {}
    for sig, current_pts in weights.items():
        prior_pts = float(SIGNAL_PRIOR_PTS.get(sig, 0))
        delta = current_pts - prior_pts
        snapshot[sig] = {
            'current_pts': round(current_pts, 1),
            'prior_pts': prior_pts,
            'delta': round(delta, 1),
            'direction': '↑' if delta > 0.5 else ('↓' if delta < -0.5 else '→'),
        }
    return snapshot


def get_learning_summary() -> dict:
    """High-level stats for dashboard display."""
    from learning.signal_performance import get_signal_report, get_attribution_history
    report = get_signal_report(min_fires=5)
    history = get_attribution_history(limit=100)

    total = len(history)
    wins = sum(1 for t in history if t.get('won'))
    losses = total - wins
    top_signals = sorted(
        [r for r in report if r['fires'] >= 10],
        key=lambda x: x['win_rate'] or 0, reverse=True
    )[:3]
    worst_signals = sorted(
        [r for r in report if r['fires'] >= 10],
        key=lambda x: x['win_rate'] or 1
    )[:3]

    return {
        'attributed_trades': total,
        'wins': wins,
        'losses': losses,
        'win_rate': wins / total if total > 0 else None,
        'signals_tracked': len(report),
        'top_signals': top_signals,
        'worst_signals': worst_signals,
        'weights_diverged': sum(
            1 for s in get_weights_snapshot().values()
            if abs(s['delta']) > 1.0
        ),
    }
