"""
strategies/ai_agents/regime_detector.py
Classifies the current market regime before each debate.
Agents are briefed on the regime — they vote differently in different regimes.
Regimes: trending_up, trending_down, ranging, volatile, low_liquidity
"""
import pandas as pd
import numpy as np
import os, sys
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from data.indicators import add_all_indicators
from data.market_data import get_daily_bars


REGIMES = {
    'trending_up':     'Strong upward trend. Momentum strategies have edge. Bias toward longs.',
    'trending_down':   'Strong downward trend. Caution on longs. Shorts or cash preferred.',
    'ranging':         'Price oscillating in a range. Mean-reversion setups work. Avoid breakout trades.',
    'volatile':        'High volatility, wide swings. Reduce position sizes. Tighter stops required.',
    'low_liquidity':   'Thin volume. Spreads are wide. Skip most trades — slippage risk is high.',
}


def detect_regime(df: Optional[pd.DataFrame] = None, symbol: str = 'SPY') -> dict:
    """
    Classify the current market regime using SPY (market proxy) or provided df.
    Returns: {'regime': str, 'description': str, 'adx': float, 'vix_proxy': float}
    """
    try:
        if df is None:
            df = get_daily_bars(symbol, period='3mo')
        if df is None or len(df) < 20:
            return _default_regime()

        df = add_all_indicators(df)
        last = df.iloc[-1]

        adx = float(last.get('adx', 25) or 25)
        rsi = float(last.get('rsi', 50) or 50)
        bb_width = float(last.get('bb_width', 0.02) or 0.02)
        vol_spike = float(last.get('vol_spike', 1.0) or 1.0)
        ema20 = float(last.get('ema20', last['close']) or last['close'])
        ema50 = float(last.get('ema50', last['close']) or last['close'])
        close = float(last['close'])
        volume = float(last.get('volume', 0) or 0)
        vol_ma = float(last.get('vol_ma20', volume) or volume)

        # Volatility proxy from BB width (replaces VIX)
        vix_proxy = bb_width * 100

        # Classification logic
        if volume > 0 and vol_ma > 0 and (volume / vol_ma) < 0.5:
            regime = 'low_liquidity'
        elif bb_width > 0.08 or vix_proxy > 8:
            regime = 'volatile'
        elif adx > 25 and close > ema20 > ema50:
            regime = 'trending_up'
        elif adx > 25 and close < ema20 < ema50:
            regime = 'trending_down'
        else:
            regime = 'ranging'

        return {
            'regime': regime,
            'description': REGIMES[regime],
            'adx': adx,
            'vix_proxy': round(vix_proxy, 2),
            'bb_width': round(bb_width, 4),
            'trend_direction': 'up' if close > ema50 else 'down',
            'vol_spike': round(vol_spike, 2),
        }

    except Exception as e:
        print(f"[regime_detector] Error: {e}")
        return _default_regime()


def _default_regime() -> dict:
    return {
        'regime': 'ranging',
        'description': REGIMES['ranging'],
        'adx': 20.0,
        'vix_proxy': 3.0,
        'bb_width': 0.03,
        'trend_direction': 'neutral',
        'vol_spike': 1.0,
    }


def get_regime_brief(regime_data: dict) -> str:
    """Format regime data for agent briefing."""
    regime = regime_data.get('regime', 'ranging')
    desc = regime_data.get('description', '')
    adx = regime_data.get('adx', 20)
    vix = regime_data.get('vix_proxy', 3)
    return (
        f"CURRENT MARKET REGIME: {regime.upper().replace('_', ' ')}\n"
        f"  {desc}\n"
        f"  ADX: {adx:.1f} | Volatility: {vix:.1f}% | "
        f"Trend: {regime_data.get('trend_direction','neutral').upper()}"
    )
