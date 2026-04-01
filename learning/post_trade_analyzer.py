"""
learning/post_trade_analyzer.py — Runs immediately after every trade close.

Extracts structured attribution from the closed trade:
  - which signals were active at entry
  - what regime it was in
  - which agents voted what
  - what actually happened

Stores to trade_attribution table.
Updates signal_stats (Bayesian weights shift).
Updates agent_stats.
Writes a 'lesson' string fed back into LanceDB memory.

Called from job_runner._execute_crypto_exit() and _execute_equity_exit().
"""
import os
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from learning.signal_performance import (
    record_trade_attribution,
    record_agent_votes,
    SIGNAL_PRIOR_PTS,
)


# ── Signal extraction ─────────────────────────────────────────────────────────

def extract_signals_from_market_data(market_data: dict) -> dict[str, bool]:
    """
    Map the raw market_data dict (built by job_runner._build_market_data)
    to the canonical signal names used in signal_performance.

    Returns {signal_name: bool} — True if the signal was active at entry.
    """
    md = market_data or {}

    def _b(key, default=False):
        v = md.get(key, default)
        return bool(v) if v is not None else default

    def _f(key, default=0.0):
        try:
            return float(md.get(key) or default)
        except Exception:
            return default

    # ── Engine signals (v9 crypto_engine.py — primary trade triggers) ──────────
    # These are the signals that actually caused the trade to be entered.
    # Stored in market_data['active_signals'] (list) and market_data['signal_type'].
    _active = set(md.get('active_signals') or [])
    _sig_type = str(md.get('signal_type') or '')

    signals = {
        # Engine signals — what actually fired
        'engine_cascade':       'cascade'      in _active or _sig_type == 'cascade',
        'engine_divergence':    'divergence'   in _active or _sig_type == 'divergence',
        'engine_obi':           'obi'          in _active or _sig_type == 'obi',
        'engine_vwap_reclaim':  'vwap_reclaim' in _active or _sig_type == 'vwap_reclaim',
        'engine_macd_fallback': 'macd_fallback' in _active or _sig_type == 'macd_fallback',
        'engine_near_miss':     'near_miss'    in _active or _sig_type == 'near_miss'
                                or any('near_' in s for s in _active),
        # Indicator flags (v3-v8 legacy signals — still populated by add_all_indicators)
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
    return signals


def _generate_lesson(
    symbol: str,
    regime: str,
    signals: dict,
    won: bool,
    pnl_usd: float,
    pnl_pct: float,
    fee_usd: float,
    exit_reason: str,
    hold_minutes: float,
    agent_votes: dict,
) -> str:
    """
    Generate a concise, structured 'why this trade worked/failed' lesson string.
    Stored in trade_attribution.lesson and fed into LanceDB memory.
    """
    outcome = 'WIN' if won else 'LOSS'
    active = [s for s, v in signals.items() if v]
    inactive_key = [s for s, v in signals.items() if not v and s in (
        'supertrend_bullish', 'ichimoku_bullish', 'squeeze_fired', 'rv_expansion'
    )]

    buy_agents  = [a for a, v in agent_votes.items() if str(v).upper() == 'BUY']
    hold_agents = [a for a, v in agent_votes.items() if str(v).upper() == 'HOLD']
    sell_agents = [a for a, v in agent_votes.items() if str(v).upper() == 'SELL']

    net_after_fee = pnl_usd - fee_usd
    fee_pct_of_move = abs(fee_usd / max(abs(pnl_usd), 0.01)) * 100 if pnl_usd != 0 else 0

    lines = [
        f"OUTCOME: {outcome} | {symbol} | {regime} regime | held {hold_minutes:.0f}min",
        f"P&L: ${pnl_usd:+.2f} gross | ${net_after_fee:+.2f} net | fee ${fee_usd:.2f} ({fee_pct_of_move:.0f}% of move)",
        f"ACTIVE SIGNALS ({len(active)}): {', '.join(active) if active else 'none'}",
        f"ABSENT KEY SIGNALS: {', '.join(inactive_key) if inactive_key else 'none'}",
        f"AGENTS: BUY={buy_agents} HOLD={hold_agents} SELL={sell_agents}",
        f"EXIT: {exit_reason[:120]}",
    ]

    # Add interpretation
    if won:
        if len(active) >= 4:
            lines.append(f"PATTERN: Multi-signal confluence in {regime} regime → confirmed edge")
        if 'supertrend_bullish' in active and 'wavetrend_cross' in active:
            lines.append("PATTERN: SuperTrend + WaveTrend combo → strong trend-momentum confluence")
        if 'squeeze_fired' in active and 'rv_expansion' in active:
            lines.append("PATTERN: Squeeze + vol expansion → textbook breakout setup")
    else:
        if fee_pct_of_move > 50:
            lines.append("FAILURE: Fees consumed > 50% of gross move — setup too small to trade")
        if hold_minutes < 5:
            lines.append("FAILURE: Very short hold — likely choppy entry or premature exit")
        if not active:
            lines.append("FAILURE: No signals active at entry — should not have traded")
        if regime == 'ranging' and 'supertrend_bullish' in active:
            lines.append("WARNING: SuperTrend in ranging regime — trend signal unreliable")

    return '\n'.join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze_closed_trade(
    symbol: str,
    strategy: str,
    entry_price: float,
    exit_price: float,
    qty: float,
    fee_usd: float,
    entry_ts: str,
    exit_ts: str,
    exit_reason: str,
    market_data_at_entry: dict,
    agent_votes: Optional[dict] = None,
    source: str = 'live',
    paper: bool = True,
    trade_ref: str = '',
    mae_pct: float = 0,
    mfe_pct: float = 0,
    exit_type: str = 'unknown',
    ml_p_win: float = 0,
    super_score: float = 0,
) -> dict:
    """
    Full post-trade attribution analysis. Call this immediately after every trade close.

    Args:
        market_data_at_entry: The market_data dict from _build_market_data() at entry time.
                              If unavailable, pass the closest available snapshot.
        agent_votes: {'agent_name': 'BUY'|'HOLD'|'SELL'} from debate_result.

    Returns a dict with: won, pnl_usd, net_pnl, lesson, signals, regime
    """
    md = market_data_at_entry or {}
    agent_votes = agent_votes or {}

    # Compute P&L
    pnl_usd = (exit_price - entry_price) * qty
    pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0
    net_pnl = pnl_usd - fee_usd
    won = net_pnl > 0

    # Regime
    regime = str(md.get('regime', 'unknown')).lower()

    # Hold time
    hold_minutes = 0.0
    try:
        t0 = datetime.fromisoformat(entry_ts)
        t1 = datetime.fromisoformat(exit_ts) if exit_ts else datetime.now(timezone.utc)
        if not t0.tzinfo:
            t0 = t0.replace(tzinfo=timezone.utc)
        if not t1.tzinfo:
            t1 = t1.replace(tzinfo=timezone.utc)
        hold_minutes = (t1 - t0).total_seconds() / 60
    except Exception:
        pass

    # Extract signals
    signals = extract_signals_from_market_data(md)

    # Conviction score at entry (if available)
    conviction = float(md.get('conviction_score', 0) or 0)

    # Generate lesson
    lesson = _generate_lesson(
        symbol=symbol, regime=regime, signals=signals,
        won=won, pnl_usd=pnl_usd, pnl_pct=pnl_pct,
        fee_usd=fee_usd, exit_reason=exit_reason,
        hold_minutes=hold_minutes, agent_votes=agent_votes,
    )

    # Record attribution (updates signal_stats + Bayesian weights)
    attr_id = record_trade_attribution(
        symbol=symbol, strategy=strategy, regime=regime,
        signals=signals, won=won,
        pnl_usd=pnl_usd, pnl_pct=pnl_pct, fee_usd=fee_usd,
        conviction=conviction,
        entry_price=entry_price, exit_price=exit_price,
        entry_ts=entry_ts, exit_ts=exit_ts or datetime.now(timezone.utc).isoformat(),
        exit_reason=exit_reason, hold_minutes=hold_minutes,
        source=source, paper=paper, trade_ref=trade_ref,
        lesson=lesson,
        mae_pct=mae_pct, mfe_pct=mfe_pct,
        exit_type=exit_type, ml_p_win=ml_p_win,
        super_score=super_score,
    )

    # Update agent accuracy
    if agent_votes:
        record_agent_votes(agent_votes, regime, won)
        record_agent_votes(agent_votes, 'any', won)  # also update global accuracy

    result = {
        'attr_id': attr_id,
        'won': won,
        'pnl_usd': pnl_usd,
        'net_pnl': net_pnl,
        'pnl_pct': pnl_pct,
        'fee_usd': fee_usd,
        'regime': regime,
        'signals': signals,
        'active_signals': [s for s, v in signals.items() if v],
        'hold_minutes': hold_minutes,
        'lesson': lesson,
        'conviction': conviction,
    }

    print(f"[learning] {'✅' if won else '❌'} {symbol} attributed | "
          f"regime={regime} | {len(result['active_signals'])} signals | "
          f"net ${net_pnl:+.2f} | {exit_reason[:60]}")

    # ── Tax lot tracking ───────────────────────────────────────────────────────
    try:
        from learning.tax_tracker import record_tax_lot
        # Map strategy name to asset class for tax treatment
        asset_class_map = {
            'crypto': 'crypto', 'crypto_macd': 'crypto', 'mean_reversion': 'crypto',
            'equity': 'equity', 'equity_momentum': 'equity',
            'futures': 'futures', 'futures_scalper': 'futures',
            'perp': 'perp',
        }
        strat_lower = strategy.lower()
        if 'perp' in strat_lower:
            asset_class = 'perp'
        elif 'futures' in strat_lower:
            asset_class = 'futures'
        elif 'equity' in strat_lower:
            asset_class = 'equity'
        else:
            asset_class = asset_class_map.get(strat_lower.split('_')[0], 'crypto')
        record_tax_lot(
            symbol=symbol, strategy=strategy, asset_class=asset_class,
            entry_ts=entry_ts,
            exit_ts=exit_ts or datetime.now(timezone.utc).isoformat(),
            entry_price=entry_price, exit_price=exit_price,
            qty=qty, fees_usd=fee_usd, paper=paper,
        )
    except Exception as _te:
        print(f"[tax_tracker] record error: {_te}")

    return result
