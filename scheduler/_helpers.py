"""
scheduler/_helpers.py — Shared state and helper functions for scan sub-modules.

Imported by: exit_monitor, equity_scanner, crypto_scanner, perp_scanner, job_runner.
Not imported by any file that any of the above import (no circular deps).
"""
import math
import os
import sys
from datetime import datetime

import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    ANTHROPIC_API_KEY, MARKET_TIMEZONE,
    CRYPTO_CANDLE_GRANULARITY,
)
from data.market_data import (
    get_fear_greed, get_williams_r, get_momentum_score,
    count_pullback_bars,
)
from data.coinbase_feed import get_microstructure_feed
from strategies.crypto_macd import CryptoMACDStrategy
from strategies.futures_scalper import FuturesScalperStrategy

# ── Self-improving intelligence layer ────────────────────────────────────────
try:
    from learning.post_trade_analyzer import analyze_closed_trade
    from learning.dynamic_weights import get_conviction_score, invalidate_cache as _invalidate_weights
    from learning.signal_performance import get_agent_accuracy_context
    from data.price_archive import upsert_candles as _archive_candles
    _LEARNING_AVAILABLE = True
except Exception as _le:
    print(f"[scheduler] Learning layer unavailable: {_le}")
    analyze_closed_trade = None
    _invalidate_weights = lambda: None
    get_conviction_score = None
    get_agent_accuracy_context = None
    _archive_candles = None
    _LEARNING_AVAILABLE = False

# ── Meta-learner ──────────────────────────────────────────────────────────────
try:
    from learning.meta_learner import maybe_run_meta_analysis, get_latest_insight
    _META_LEARNER_AVAILABLE = True
except Exception as _mle:
    print(f"[scheduler] Meta-learner unavailable: {_mle}")
    maybe_run_meta_analysis = lambda: None
    get_latest_insight = lambda: None
    _META_LEARNER_AVAILABLE = False

# ── Live backtest validator ───────────────────────────────────────────────────
try:
    from learning.live_backtest_validator import (
        trigger_background_backtest, get_recent_backtest_context,
    )
    _BACKTEST_VALIDATOR_AVAILABLE = True
except Exception as _bve:
    print(f"[scheduler] Live backtest validator unavailable: {_bve}")
    trigger_background_backtest = lambda: None
    get_recent_backtest_context = lambda s: None
    _BACKTEST_VALIDATOR_AVAILABLE = False

# ── ML signal layer ───────────────────────────────────────────────────────────
try:
    from learning.ml_signal import get_ml_signal, maybe_retrain as _ml_maybe_retrain
    from config import ML_SIGNAL_MIN_PROB
    _ML_AVAILABLE = True
except Exception as _mls:
    print(f"[scheduler] ML signal layer unavailable: {_mls}")
    get_ml_signal = None
    _ml_maybe_retrain = lambda: None
    ML_SIGNAL_MIN_PROB = 0.0
    _ML_AVAILABLE = False

# ── Macro feed ────────────────────────────────────────────────────────────────
try:
    from data.macro_feed import get_macro_snapshot as _get_macro_snapshot
    _MACRO_FEED_AVAILABLE = True
except Exception:
    _get_macro_snapshot = None
    _MACRO_FEED_AVAILABLE = False

# ── Forecast calibrator ────────────────────────────────────────────────────────
try:
    from learning.forecast_calibrator import get_full_calibration_context as _get_calibration_ctx
    _CALIBRATION_AVAILABLE = True
except Exception:
    _get_calibration_ctx = None
    _CALIBRATION_AVAILABLE = False

# ── Options flow signals ──────────────────────────────────────────────────────
try:
    from data.options_flow import get_options_signals as _get_options_signals
    from data.options_flow import format_options_context as _format_options_context
    _OPTIONS_FLOW_AVAILABLE = True
except Exception:
    _get_options_signals = None
    _format_options_context = None
    _OPTIONS_FLOW_AVAILABLE = False

# ── Market context + session analyst ─────────────────────────────────────────
try:
    from data.market_context import get_context_for_debate, should_block_trade
    from strategies.ai_agents.session_analyst import (
        run_session_analysis, get_current_session_context,
        format_session_context_for_debate,
    )
    _CONTEXT_AVAILABLE = True
except Exception as _cte:
    print(f"[scheduler] Market context unavailable: {_cte}")
    get_context_for_debate = None
    should_block_trade = None
    run_session_analysis = None
    get_current_session_context = lambda: {}
    format_session_context_for_debate = lambda ctx: ''
    _CONTEXT_AVAILABLE = False

# ── Strategy singletons ───────────────────────────────────────────────────────
_crypto_strategy = CryptoMACDStrategy(variant='consensus')
_futures_strategy = FuturesScalperStrategy()


# ── Shared helper functions ───────────────────────────────────────────────────

def _debate_available():
    """Return debate engine dict or None if AI unavailable."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        from strategies.ai_agents.debate_engine import run_debate, run_quick_debate
        from strategies.ai_agents.risk_synthesizer import synthesize_final_decision, should_use_full_debate
        from strategies.ai_agents.exit_review import run_exit_review
        return {
            'debate': run_debate, 'quick': run_quick_debate,
            'synthesize': synthesize_final_decision, 'full_check': should_use_full_debate,
            'exit': run_exit_review,
        }
    except Exception as e:
        print(f"[scheduler] Debate engine unavailable: {e}")
        return None


def _get_microstructure(symbol: str) -> dict:
    """Fetch live OBI/TFI/microprice/spread from WebSocket feed. Returns Nones if unavailable."""
    try:
        feed = get_microstructure_feed()
        return feed.get_microstructure(symbol)
    except Exception:
        return {'obi': None, 'tfi': None, 'microprice_premium_bps': None, 'spread_bps': None}


def _build_market_data(symbol, price, df_ind, change_pct=0, regime='ranging') -> dict:
    """Assemble the full market_data dict passed to AI agents."""
    last = df_ind.iloc[-1]
    fg = get_fear_greed()
    williams_r = get_williams_r(df_ind)
    momentum_sc = get_momentum_score(df_ind)

    ema200 = float(last.get('ema200', 0) or 0)
    above_200d = (price > ema200) if ema200 > 0 else None
    vol_spike = float(last.get('vol_spike', 1) or 1)
    vol_20d_pct_above_avg = (vol_spike - 1) * 100 if vol_spike > 1 else 0
    pullback = count_pullback_bars(df_ind)

    def _safe(col, default=None):
        v = last.get(col, default)
        if v is None:
            return default
        try:
            fv = float(v)
            if math.isnan(fv):
                return default
            return fv
        except Exception:
            return default

    rv_ratio          = _safe('rv_ratio')
    avwap_utc         = _safe('avwap_utc', price)
    avwap_dev         = _safe('avwap_dev', 0.0)
    autocorr_ret      = _safe('autocorr_ret')
    ou_halflife_minutes = _safe('ou_halflife_minutes')
    ou_zscore         = _safe('ou_zscore', 0.0)
    amihud_pct        = _safe('amihud_pct')
    kyle_lambda_pct   = _safe('kyle_lambda_pct')
    squeeze_on        = bool(last.get('squeeze_on', False))
    squeeze_fired     = bool(last.get('squeeze_fired', False))
    squeeze_bars      = int(_safe('squeeze_bars', 0) or 0)
    squeeze_direction = int(_safe('squeeze_direction', 0) or 0)
    kalman_price      = _safe('kalman_price', price)
    kalman_dev        = _safe('kalman_dev', 0.0)
    session_active    = bool(last.get('session_active', True))
    # v4.3 indicators
    supertrend_bullish  = bool(last.get('supertrend_bullish', False))
    cloud_bullish       = bool(last.get('cloud_bullish', False))
    cloud_bearish       = bool(last.get('cloud_bearish', False))
    wae_bullish         = bool(last.get('wae_bullish', False))
    wae_exploding       = bool(last.get('wae_exploding', False))
    fisher_cross_up     = bool(last.get('fisher_cross_up', False))
    fisher_val          = _safe('fisher', 0.0)
    chop_val            = _safe('chop', 50.0)
    chop_trending       = bool(last.get('chop_trending', False))
    chop_ranging        = bool(last.get('chop_ranging', False))
    wt1_val             = _safe('wt1', 0.0)
    wt_oversold_cross   = bool(last.get('wt_oversold_cross', False))
    lrsi_val            = _safe('lrsi', 0.5)
    lrsi_oversold       = lrsi_val is not None and float(lrsi_val) < 0.15
    # v8.1 high-WR signals
    stochrsi_k          = _safe('stochrsi_k', 50.0)
    stochrsi_d          = _safe('stochrsi_d', 50.0)
    stochrsi_cross_up   = bool(last.get('stochrsi_cross_up', False))
    cvd_bull_div        = bool(last.get('cvd_bull_div', False))
    cvd_bear_div        = bool(last.get('cvd_bear_div', False))
    vwap_lower_touch    = bool(last.get('vwap_lower_touch', False))
    vwap_upper_touch    = bool(last.get('vwap_upper_touch', False))
    ema_golden_cross    = bool(last.get('ema_golden_cross', False))
    ema9_above_21       = bool(last.get('ema9_above_21', False))

    md = {
        'price': price,
        'change_pct': change_pct,
        'vol_spike': vol_spike,
        'rsi': float(last.get('rsi', 50) or 50),
        'macd_hist': float(last.get('macd_std_hist', 0) or last.get('macd1_hist', 0) or 0),
        'vwap': float(last.get('vwap', price) or price),
        'atr': float(last.get('atr', price * 0.01) or price * 0.01),
        'adx': float(last.get('adx', 25) or 25),
        'trend_20d': 'bullish' if float(last.get('ema20', 0) or 0) > float(last.get('ema50', 0) or 0) else 'bearish',
        'dollar_volume': price * float(last.get('volume', 0) or 0),
        'regime': regime,
        'williams_r': williams_r,
        'fear_greed_score': fg.get('score', 50),
        'fear_greed_label': fg.get('label', 'Neutral'),
        'momentum_score': momentum_sc,
        'above_200d_ma': above_200d,
        'vol_20d_pct_above_avg': vol_20d_pct_above_avg,
        'pullback_bars': pullback['pullback_bars'],
        'pullback_trend': pullback['trend'],
        'is_valid_pullback': pullback['is_valid_pullback'],
        'rv_ratio': rv_ratio,
        'avwap_utc': avwap_utc,
        'avwap_dev': avwap_dev,
        'autocorr_ret': autocorr_ret,
        'ou_halflife_minutes': ou_halflife_minutes,
        'ou_zscore': ou_zscore,
        'amihud_pct': amihud_pct,
        'kyle_lambda_pct': kyle_lambda_pct,
        'squeeze_on': squeeze_on,
        'squeeze_fired': squeeze_fired,
        'squeeze_bars': squeeze_bars,
        'squeeze_direction': squeeze_direction,
        'kalman_price': kalman_price,
        'kalman_dev': kalman_dev,
        'session_active': session_active,
        'supertrend_bullish': supertrend_bullish,
        'cloud_bullish': cloud_bullish,
        'cloud_bearish': cloud_bearish,
        'wae_bullish': wae_bullish,
        'wae_exploding': wae_exploding,
        'fisher_cross_up': fisher_cross_up,
        'fisher': fisher_val,
        'chop': chop_val,
        'chop_trending': chop_trending,
        'chop_ranging': chop_ranging,
        'wt1': wt1_val,
        'wt_oversold_cross': wt_oversold_cross,
        'lrsi': lrsi_val,
        'stochrsi_k': stochrsi_k,
        'stochrsi_d': stochrsi_d,
        'stochrsi_cross_up': stochrsi_cross_up,
        'cvd_bull_div': cvd_bull_div,
        'cvd_bear_div': cvd_bear_div,
        'vwap_lower_touch': vwap_lower_touch,
        'vwap_upper_touch': vwap_upper_touch,
        'ema_golden_cross': ema_golden_cross,
        'ema9_above_21': ema9_above_21,
        'atr_pct': float(last.get('atr', price * 0.01) or price * 0.01) / price * 100,
        **_get_microstructure(symbol),
    }

    # ── Momentum acceleration (d²price/dt² proxy via MACD histogram delta) ────
    # Positive = momentum building (buy-side accelerating)
    # Negative = momentum decelerating or reversing
    try:
        macd_col = 'macd_std_hist' if 'macd_std_hist' in df_ind.columns else 'macd1_hist'
        if macd_col in df_ind.columns and len(df_ind) >= 5:
            hist_series = df_ind[macd_col].dropna()
            if len(hist_series) >= 5:
                # 3-bar EMA of the delta to smooth noise
                delta = hist_series.diff()
                smoothed = delta.ewm(span=3, adjust=False).mean()
                accel = float(smoothed.iloc[-1])
                md['macd_acceleration'] = round(accel, 6)
                md['macd_accel_direction'] = 'ACCELERATING' if accel > 0 else 'DECELERATING'
    except Exception:
        md['macd_acceleration'] = 0.0
        md['macd_accel_direction'] = 'UNKNOWN'

    # ── Options flow signals (30-min cache, fail-silent) ─────────────────────
    if _OPTIONS_FLOW_AVAILABLE and _get_options_signals:
        try:
            opt = _get_options_signals()
            md['iv_rank']            = opt.get('iv_rank', 0.50)
            md['iv_regime']          = opt.get('iv_regime', 'NORMAL_IV')
            md['vix_level']          = opt.get('vix_level')
            md['term_structure']     = opt.get('term_structure', 'FLAT')
            md['contango_ratio']     = opt.get('contango_ratio', 1.0)
            md['panic_signal']       = opt.get('panic_signal', False)
            md['tail_risk_elevated'] = opt.get('tail_risk_elevated', False)
            md['options_regime']     = opt.get('options_regime', '')
        except Exception:
            pass

    # ── Inject calibration context (how well conviction scores predict wins) ──
    if _CALIBRATION_AVAILABLE and _get_calibration_ctx:
        try:
            from config import PAPER_TRADING
            cal_ctx = _get_calibration_ctx(paper=PAPER_TRADING)
            if cal_ctx:
                md['calibration_context'] = cal_ctx
        except Exception:
            pass

    return md
