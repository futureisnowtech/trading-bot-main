"""
indicators/vwap_mtf.py — Multi-timeframe VWAP with standard deviation bands.

Three VWAP anchors: session (midnight UTC), weekly (Monday UTC), monthly.
Each has ±1σ, ±2σ, ±3σ bands.

VWAP reclaim signal: price breaks below VWAP then reclaims on increasing volume.

Outputs:
  session_vwap          : session VWAP price
  session_vwap_dist_pct : % distance from session VWAP
  weekly_vwap           : weekly VWAP price
  weekly_vwap_dist_pct  : % distance from weekly VWAP
  vwap_band_position    : which σ band price is in (-3 to +3)
  vwap_reclaim_signal   : True if price reclaimed session VWAP on volume
  vwap_confluence_bull  : True if above all 3 VWAPs simultaneously
  vwap_confluence_bear  : True if below all 3 VWAPs simultaneously
  session_std1_upper/lower, session_std2_upper/lower : band prices
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def _vwap_from_anchor(df: pd.DataFrame, anchor_idx: int) -> tuple:
    """
    Compute VWAP and std bands from anchor index to end of DataFrame.
    Returns (vwap_series, std_series).
    """
    sub = df.iloc[anchor_idx:].copy()
    if sub.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    typical = (sub['high'] + sub['low'] + sub['close']) / 3
    cum_tp_vol = (typical * sub['volume']).cumsum()
    cum_vol = sub['volume'].cumsum()
    vwap = cum_tp_vol / (cum_vol + 1e-9)

    # Rolling variance: std of typical price weighted by volume
    cum_tp2_vol = (typical ** 2 * sub['volume']).cumsum()
    variance = (cum_tp2_vol / (cum_vol + 1e-9)) - (vwap ** 2)
    variance = variance.clip(lower=0)
    std = np.sqrt(variance)

    return vwap, std


def compute_vwap_mtf(df: pd.DataFrame) -> dict:
    """
    Compute multi-timeframe VWAP from OHLCV DataFrame with DatetimeIndex.

    Args:
        df: DataFrame with DatetimeIndex (UTC), open/high/low/close/volume columns

    Returns:
        dict with all VWAP fields
    """
    neutral = {
        'session_vwap': 0.0,
        'session_vwap_dist_pct': 0.0,
        'weekly_vwap': 0.0,
        'weekly_vwap_dist_pct': 0.0,
        'monthly_vwap': 0.0,
        'monthly_vwap_dist_pct': 0.0,
        'vwap_band_position': 0,
        'vwap_reclaim_signal': False,
        'vwap_confluence_bull': False,
        'vwap_confluence_bear': False,
        'session_std1_upper': 0.0,
        'session_std1_lower': 0.0,
        'session_std2_upper': 0.0,
        'session_std2_lower': 0.0,
        'poc_price': 0.0,
    }

    if df is None or len(df) < 10:
        return neutral

    try:
        current_price = float(df['close'].iloc[-1])
        idx = df.index

        # Determine anchor indices
        # Session: find last midnight UTC
        if hasattr(idx[0], 'tz') and idx[0].tz is not None:
            now = idx[-1]
        else:
            now = pd.Timestamp(idx[-1], tz='UTC')

        midnight_utc = now.normalize()
        monday_utc = midnight_utc - timedelta(days=midnight_utc.weekday())
        month_start = midnight_utc.replace(day=1)

        def _find_anchor(anchor_ts):
            try:
                mask = idx >= anchor_ts
                anchors = np.where(mask)[0]
                return int(anchors[0]) if len(anchors) > 0 else 0
            except Exception:
                return 0

        s_anchor = _find_anchor(midnight_utc)
        w_anchor = _find_anchor(monday_utc)
        m_anchor = _find_anchor(month_start)

        # Session VWAP
        s_vwap, s_std = _vwap_from_anchor(df, s_anchor)
        session_vwap = float(s_vwap.iloc[-1]) if not s_vwap.empty else 0.0
        session_std = float(s_std.iloc[-1]) if not s_std.empty else 0.0

        # Weekly VWAP
        w_vwap, _ = _vwap_from_anchor(df, w_anchor)
        weekly_vwap = float(w_vwap.iloc[-1]) if not w_vwap.empty else session_vwap

        # Monthly VWAP
        m_vwap, _ = _vwap_from_anchor(df, m_anchor)
        monthly_vwap = float(m_vwap.iloc[-1]) if not m_vwap.empty else weekly_vwap

        # Distance %
        def _dist_pct(price, vwap):
            return ((price - vwap) / vwap * 100) if vwap > 0 else 0.0

        s_dist = _dist_pct(current_price, session_vwap)
        w_dist = _dist_pct(current_price, weekly_vwap)
        m_dist = _dist_pct(current_price, monthly_vwap)

        # Band position (-3 to +3, which σ band)
        band_pos = 0
        if session_vwap > 0 and session_std > 0:
            distance_stds = (current_price - session_vwap) / session_std
            band_pos = int(np.clip(round(distance_stds), -3, 3))

        # Band prices
        s1u = session_vwap + session_std
        s1l = session_vwap - session_std
        s2u = session_vwap + 2 * session_std
        s2l = session_vwap - 2 * session_std

        # VWAP reclaim signal: price was below session VWAP, now above it with volume > avg
        reclaim = False
        if len(df) >= 3 and not s_vwap.empty:
            recent_close = df['close'].tail(5).values
            recent_vol = df['volume'].tail(5).values
            avg_vol = float(df['volume'].tail(20).mean())
            vwap_vals = s_vwap.tail(5).values

            was_below = recent_close[0] < vwap_vals[0] if len(vwap_vals) > 0 else False
            now_above = current_price > session_vwap
            vol_spike = float(recent_vol[-1]) > avg_vol * 1.2

            reclaim = was_below and now_above and vol_spike

        # Confluence flags
        above_all = (current_price > session_vwap and
                     current_price > weekly_vwap and
                     current_price > monthly_vwap)
        below_all = (current_price < session_vwap and
                     current_price < weekly_vwap and
                     current_price < monthly_vwap)

        # Point of Control (highest volume price)
        poc_price = 0.0
        if len(df) >= 20:
            recent_20 = df.tail(20)
            vol_by_price = {}
            for _, row in recent_20.iterrows():
                price_level = round(float(row['close']), 2)
                vol_by_price[price_level] = vol_by_price.get(price_level, 0) + float(row['volume'])
            if vol_by_price:
                poc_price = max(vol_by_price, key=vol_by_price.get)

        return {
            'session_vwap': round(session_vwap, 4),
            'session_vwap_dist_pct': round(s_dist, 4),
            'weekly_vwap': round(weekly_vwap, 4),
            'weekly_vwap_dist_pct': round(w_dist, 4),
            'monthly_vwap': round(monthly_vwap, 4),
            'monthly_vwap_dist_pct': round(m_dist, 4),
            'vwap_band_position': band_pos,
            'vwap_reclaim_signal': reclaim,
            'vwap_confluence_bull': above_all,
            'vwap_confluence_bear': below_all,
            'session_std1_upper': round(s1u, 4),
            'session_std1_lower': round(s1l, 4),
            'session_std2_upper': round(s2u, 4),
            'session_std2_lower': round(s2l, 4),
            'poc_price': round(poc_price, 4),
        }

    except Exception as e:
        logger.debug(f'[vwap_mtf] Error: {e}')
        return neutral
