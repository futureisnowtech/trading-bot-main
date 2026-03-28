"""
learning/super_score.py — Unified SUPER SCORE: 0-100 composite intelligence.

Synthesizes every data source the system has access to:
  - ML model P(win)           [25%]  — forward-looking, empirically trained
  - Bayesian signal confluence [20%]  — historically calibrated signal weights
  - Agent consensus strength   [20%]  — AI debate conviction
  - Market context             [20%]  — macro, regime, funding, Fear&Greed, TV signal
  - Microstructure quality     [15%]  — order flow, volume, liquidation data

Score interpretation:
  0-39   ABORT       — do not trade, multiple headwinds
  40-54  WEAK        — marginal setup, 50% size if at all
  55-64  MODEST      — decent setup, 75% size
  65-74  NORMAL      — good setup, full size
  75-84  STRONG      — high conviction, 125% size
  85-100 EXCEPTIONAL — maximum conviction, 150% size (Kelly cap)
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- Config imports with fallbacks ---
try:
    from config import ML_SIGNAL_MIN_PROB, PAPER_TRADING
except Exception:
    ML_SIGNAL_MIN_PROB = 0.08
    PAPER_TRADING = True


def _ml_component(ml_p_win: float) -> float:
    """Compute ML sub-score 0-100 from P(win)."""
    threshold = ML_SIGNAL_MIN_PROB
    if ml_p_win <= 0 or ml_p_win < threshold * 0.5:
        return 30.0  # no model or very low confidence
    if ml_p_win < threshold:
        return 30.0 + (ml_p_win / threshold) * 20.0
    return 50.0 + ((ml_p_win - threshold) / (1.0 - threshold)) * 50.0


def _signal_component(market_data: dict) -> float:
    """Compute Bayesian signal confluence sub-score 0-100."""
    try:
        from learning.dynamic_weights import get_conviction_score
        conv_pts, _ = get_conviction_score(market_data)
    except Exception:
        return 50.0
    # Normalize: 0pts→0, 30pts→37.5 (old gate), 80pts→100
    return (conv_pts / 80.0) * 100.0


def _agent_component(debate_result) -> float:
    """Compute agent consensus sub-score 0-100 from DebateResult."""
    if debate_result is None:
        return 50.0  # no debate yet, neutral

    try:
        vote_breakdown = debate_result.vote_breakdown or {}
        buy_votes = sum(
            1 for v in vote_breakdown.values()
            if str(v).upper() == 'BUY'
        )
        avg_conf = float(debate_result.confidence or 0.0)
    except Exception:
        return 50.0

    if buy_votes == 3:
        score = 70.0 + avg_conf * 30.0    # 70-100
    elif buy_votes == 2:
        score = 45.0 + avg_conf * 25.0    # 45-70
    elif buy_votes == 1:
        score = 20.0 + avg_conf * 15.0    # 20-35
    else:
        score = 0.0

    return score


def _context_component(market_data: dict) -> float:
    """Compute market context sub-score 0-100."""
    score = 50.0  # base

    # Macro score (-10 to +10 → -35 to +35)
    macro = float(market_data.get('macro_score', 0) or 0)
    score += macro * 3.5

    # Funding rate signal
    funding_signal = str(market_data.get('funding_signal', '') or '').lower()
    if 'favorable' in funding_signal or 'negative' in funding_signal:
        score += 10.0
    elif 'overheated' in funding_signal or 'positive' in funding_signal:
        score -= 15.0

    # Regime
    regime = str(market_data.get('regime', '') or '').lower()
    if 'trending_up' in regime or (
        regime == 'trending' and market_data.get('macd_consensus')
    ):
        score += 10.0
    elif 'trending_down' in regime:
        score -= 25.0
    elif 'ranging' in regime:
        score -= 5.0
    elif 'volatile' in regime:
        score -= 3.0

    # Fear & Greed (contrarian: extreme fear = buy opportunity)
    fg = float(market_data.get('fear_greed_value', 50) or 50)
    if fg <= 20:
        score += 10.0    # extreme fear = contrarian buy
    elif fg <= 35:
        score += 4.0
    elif fg >= 80:
        score -= 10.0    # extreme greed = crowded
    elif fg >= 65:
        score -= 4.0

    # TradingView external signal confirmation
    if market_data.get('tv_signal_active'):
        score += 12.0

    # Liquidation cascade avoid
    if market_data.get('liq_avoid_long'):
        score -= 25.0

    return score


def _micro_component(market_data: dict) -> float:
    """Compute microstructure quality sub-score 0-100."""
    score = 50.0  # base

    # Order Book Imbalance
    obi = market_data.get('obi')
    if obi is not None:
        obi = float(obi)
        if obi > 0.25:
            score += 18.0
        elif obi > 0.10:
            score += 9.0
        elif obi < -0.25:
            score -= 18.0
        elif obi < -0.10:
            score -= 9.0

    # Trade Flow Imbalance
    tfi = market_data.get('tfi')
    if tfi is not None:
        tfi = float(tfi)
        if tfi > 0.15:
            score += 12.0
        elif tfi > 0.05:
            score += 6.0
        elif tfi < -0.15:
            score -= 12.0
        elif tfi < -0.05:
            score -= 6.0

    # Volume spike
    vol_spike = float(market_data.get('vol_spike', 1.0) or 1.0)
    if vol_spike > 2.0:
        score += 15.0
    elif vol_spike > 1.5:
        score += 8.0
    elif vol_spike > 1.3:
        score += 4.0
    elif vol_spike < 0.7:
        score -= 8.0

    # Squeeze fired (BB-Keltner breakout)
    if market_data.get('squeeze_fired') and float(
        market_data.get('squeeze_bars', 0) or 0
    ) >= 20:
        score += 12.0

    # Kyle lambda (low = smart money, high = noise)
    kyle = float(market_data.get('kyle_lambda_pct', 50) or 50)
    if kyle <= 20:
        score += 10.0
    elif kyle <= 30:
        score += 5.0
    elif kyle >= 70:
        score -= 5.0

    return score


def _clamp(v: float) -> float:
    return min(100.0, max(0.0, v))


def compute_super_score(
    market_data: dict,
    debate_result=None,
    ml_p_win: float = 0.0,
    symbol: str = '',
) -> dict:
    """
    Compute the unified SUPER SCORE (0-100).

    Parameters
    ----------
    market_data : dict
        Full market_data dict from _build_market_data / crypto_scanner.
    debate_result : DebateResult | None
        Output of debate_engine.run_debate(); None if debate hasn't run yet.
    ml_p_win : float
        P(win) from ml_signal.get_ml_signal(); 0.0 if model not available.
    symbol : str
        Ticker symbol (informational only, used for logging).

    Returns
    -------
    dict with keys: score, label, size_multiplier, components, top_boosts, top_drags
    """
    # --- Compute each sub-component and clamp to [0, 100] ---
    ml_score      = _clamp(_ml_component(ml_p_win))
    signal_score  = _clamp(_signal_component(market_data))
    agent_score   = _clamp(_agent_component(debate_result))
    context_score = _clamp(_context_component(market_data))
    micro_score   = _clamp(_micro_component(market_data))

    # --- Weighted composite ---
    score = (
        0.25 * ml_score
        + 0.20 * signal_score
        + 0.20 * agent_score
        + 0.20 * context_score
        + 0.15 * micro_score
    )
    score = round(_clamp(score), 2)

    # --- Label and size multiplier ---
    label, mult = _score_to_label_mult(score)

    # --- Top boosts and drags (components furthest from neutral 50) ---
    component_names = {
        'ML model':        ml_score,
        'Signal confluence': signal_score,
        'Agent consensus': agent_score,
        'Market context':  context_score,
        'Microstructure':  micro_score,
    }

    deviations = {name: val - 50.0 for name, val in component_names.items()}
    sorted_devs = sorted(deviations.items(), key=lambda x: x[1], reverse=True)

    top_boosts = [
        f"{name} ({val:+.0f})"
        for name, val in sorted_devs
        if val > 0
    ][:3]

    top_drags = [
        f"{name} ({val:+.0f})"
        for name, val in sorted(deviations.items(), key=lambda x: x[1])
        if val < 0
    ][:3]

    return {
        'score':           score,
        'label':           label,
        'size_multiplier': mult,
        'components': {
            'ml':      round(ml_score, 1),
            'signals': round(signal_score, 1),
            'agents':  round(agent_score, 1),
            'context': round(context_score, 1),
            'micro':   round(micro_score, 1),
        },
        'top_boosts': top_boosts,
        'top_drags':  top_drags,
    }


def _score_to_label_mult(score: float) -> tuple[str, float]:
    """Map raw score to (label, size_multiplier) pair."""
    if score < 40:
        return 'ABORT',       0.0
    elif score < 55:
        return 'WEAK',        0.5
    elif score < 65:
        return 'MODEST',      0.75
    elif score < 75:
        return 'NORMAL',      1.0
    elif score < 85:
        return 'STRONG',      1.25
    else:
        return 'EXCEPTIONAL', 1.5


def get_score_label(score: float) -> str:
    """Return the label for a given score value."""
    label, _ = _score_to_label_mult(score)
    return label


def get_size_multiplier(score: float) -> float:
    """Return the position size multiplier for a given score value."""
    _, mult = _score_to_label_mult(score)
    return mult
