"""
strategies/ai_agents/debate_engine.py

Simplified 3-agent debate. Majority vote. No moderator round. No Goku.

Old flow: 9 agents → moderator → Goku = 11 API calls, ~45-90s latency, ~$0.08/debate
New flow: 3 agents → majority vote = 3 API calls, ~10-20s latency, ~$0.02/debate

Decision rule: 2+ BUY votes = BUY (at avg confidence). Anything else = HOLD.
"""
import json
import os
import sys
import time
from datetime import datetime
from typing import Optional
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from strategies.ai_agents.analyst_agents import (
    run_agent, get_all_agents, call_claude_structured, AGENTS, AGENT_RESPONSE_SCHEMA
)
from strategies.ai_agents.regime_detector import detect_regime, get_regime_brief
from config import (MARKET_TIMEZONE, CLAUDE_MODEL,
                    QUICK_DEBATE_AGENTS, FULL_DEBATE_AGENTS, FULL_DEBATE_MIN_AGREEMENT)


class DebateResult:
    def __init__(self, symbol, individual_signals, synthesized_signal,
                 synthesized_confidence, unified_reasoning, bull_case,
                 bear_case, key_risk, vote_breakdown, timestamp, regime='',
                 goku_verdict='SKIPPED', goku_conviction_adjustment=0,
                 goku_reasoning='', goku_insight=''):
        self.symbol = symbol
        self.individual_signals = individual_signals
        self.synthesized_signal = synthesized_signal
        self.synthesized_confidence = synthesized_confidence
        self.unified_reasoning = unified_reasoning
        self.bull_case = bull_case
        self.bear_case = bear_case
        self.key_risk = key_risk
        self.vote_breakdown = vote_breakdown
        self.timestamp = timestamp
        self.regime = regime
        # Kept for backward compatibility — always SKIPPED now
        self.goku_verdict = goku_verdict
        self.goku_conviction_adjustment = goku_conviction_adjustment
        self.goku_reasoning = goku_reasoning
        self.goku_insight = goku_insight

    def to_dict(self) -> dict:
        return {
            'symbol':                  self.symbol,
            'synthesized_signal':      self.synthesized_signal,
            'synthesized_confidence':  self.synthesized_confidence,
            'unified_reasoning':       self.unified_reasoning,
            'bull_case':               self.bull_case,
            'bear_case':               self.bear_case,
            'key_risk':                self.key_risk,
            'vote_breakdown':          self.vote_breakdown,
            'individual_signals':      self.individual_signals,
            'timestamp':               self.timestamp,
            'regime':                  self.regime,
            'goku_verdict':            self.goku_verdict,
            'goku_conviction_adjustment': self.goku_conviction_adjustment,
            'goku_reasoning':          self.goku_reasoning,
            'goku_insight':            self.goku_insight,
        }

    def __repr__(self):
        b = self.vote_breakdown
        e = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪'}.get(self.synthesized_signal, '⚪')
        lines = [
            f"\n{'═'*60}",
            f"  DEBATE: {self.symbol} | Regime: {self.regime}",
            f"{'═'*60}",
            f"  Votes: {b.get('BUY',0)} BUY | {b.get('HOLD',0)} HOLD | {b.get('SELL',0)} SELL",
            f"  Decision: {e} {self.synthesized_signal} ({self.synthesized_confidence:.0%})",
            f"  Reason: {self.unified_reasoning}",
        ]
        for s in self.individual_signals:
            e2 = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪'}.get(s.get('signal','HOLD'), '⚪')
            lines.append(f"    {s.get('agent','?'):32} {e2} {s.get('signal','?'):4} "
                         f"({s.get('confidence',0):.0%}) — {s.get('reasoning','')[:55]}")
        lines.append('═'*60)
        return '\n'.join(lines)


def run_debate(symbol: str, market_data: dict, context: str = '',
               agents_to_use: Optional[list] = None, verbose: bool = True,
               memory_context: str = '', asset_class: str = 'crypto') -> DebateResult:
    """
    3-agent debate. Majority vote (2/3 BUY = BUY). No moderator. No Goku.
    Same interface as before — callers don't need to change.
    """
    tz = pytz.timezone(MARKET_TIMEZONE)
    timestamp = datetime.now(tz).isoformat()

    if agents_to_use is None:
        agents_to_use = FULL_DEBATE_AGENTS  # defaults to 3-agent set

    # Regime detection (keep same logic — used as context)
    if market_data.get('regime') and market_data['regime'] != 'ranging':
        regime = market_data['regime']
        regime_data = {'regime': regime, 'description': '', 'adx': market_data.get('adx', 25),
                       'vix_proxy': 3.0, 'trend_direction': 'neutral', 'vol_spike': 1.0}
        regime_brief = get_regime_brief(regime_data)
    else:
        regime_data = detect_regime()
        regime = regime_data.get('regime', 'ranging')
        regime_brief = get_regime_brief(regime_data)
        market_data['regime'] = regime

    enhanced_context = f"{regime_brief}\n\n{context}" if context else regime_brief

    if verbose:
        print(f"\n🏛️  DEBATE: {symbol} | {regime.upper()} | {len(agents_to_use)} analysts")

    # ── Run each agent ──────────────────────────────────────────────────────────
    individual_signals = []
    for agent_key in agents_to_use:
        if verbose:
            name = AGENTS[agent_key]['name']
            print(f"  📊 {name}...", end=' ', flush=True)

        result = run_agent(agent_key, symbol, market_data,
                           context=enhanced_context, memory_context=memory_context,
                           asset_class=asset_class)
        individual_signals.append(result)
        time.sleep(0.1)

        if verbose:
            sig  = result.get('signal', 'HOLD')
            conf = result.get('confidence', 0)
            e    = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪'}.get(sig, '⚪')
            print(f"{e} {sig} ({conf:.0%}) — {result.get('reasoning','')[:60]}")

    # ── Majority vote ───────────────────────────────────────────────────────────
    vote_breakdown = {'BUY': 0, 'SELL': 0, 'HOLD': 0}
    buy_confidences = []
    concerns = []

    for s in individual_signals:
        v = s.get('signal', 'HOLD')
        vote_breakdown[v] = vote_breakdown.get(v, 0) + 1
        if v == 'BUY':
            buy_confidences.append(s.get('confidence', 0.5))
        if s.get('key_concern'):
            concerns.append(f"{s.get('agent_key','?')}: {s.get('key_concern','')[:60]}")

    total   = sum(vote_breakdown.values())
    buy_pct = vote_breakdown['BUY'] / total if total > 0 else 0

    # 2/3 agents must agree for BUY (FULL_DEBATE_MIN_AGREEMENT is the fraction threshold)
    # With 3 agents: 2/3 = 0.67. Allow config override.
    min_agreement = max(FULL_DEBATE_MIN_AGREEMENT, 2 / max(total, 1))
    if vote_breakdown['BUY'] >= 2 and buy_pct >= min_agreement:
        final_signal     = 'BUY'
        final_confidence = round(sum(buy_confidences) / len(buy_confidences), 3) if buy_confidences else 0.5
        bull_agents      = [s for s in individual_signals if s.get('signal') == 'BUY']
        bear_agents      = [s for s in individual_signals if s.get('signal') != 'BUY']
        bull_case        = ' | '.join(s.get('reasoning','')[:55] for s in bull_agents)
        bear_case        = ' | '.join(s.get('reasoning','')[:55] for s in bear_agents) or 'No dissent'
        unified_reasoning = f"{vote_breakdown['BUY']}/{total} agents BUY. " + bull_case[:120]
        key_risk          = ' | '.join(concerns[:2]) or 'None flagged'
    else:
        final_signal      = 'HOLD'
        final_confidence  = 0.0
        buy_agents        = [s for s in individual_signals if s.get('signal') == 'BUY']
        hold_agents       = [s for s in individual_signals if s.get('signal') == 'HOLD']
        bull_case         = ' | '.join(s.get('reasoning','')[:55] for s in buy_agents) or 'No bulls'
        bear_case         = ' | '.join(s.get('reasoning','')[:55] for s in hold_agents) or 'All held'
        unified_reasoning = (
            f"Insufficient consensus: {vote_breakdown['BUY']}/{total} BUY "
            f"(need {ceil_2_of(total)}/{total}). {bear_case[:100]}"
        )
        key_risk = ' | '.join(concerns[:2]) or 'Consensus missing'

    if verbose:
        print(f"\n  Votes: {vote_breakdown['BUY']} BUY | {vote_breakdown['HOLD']} HOLD | {vote_breakdown['SELL']} SELL")

    result = DebateResult(
        symbol=symbol,
        individual_signals=individual_signals,
        synthesized_signal=final_signal,
        synthesized_confidence=final_confidence,
        unified_reasoning=unified_reasoning,
        bull_case=bull_case,
        bear_case=bear_case,
        key_risk=key_risk,
        vote_breakdown=vote_breakdown,
        timestamp=timestamp,
        regime=regime,
        # Goku fields — always SKIPPED (removed from system)
        goku_verdict='SKIPPED',
        goku_conviction_adjustment=0,
        goku_reasoning='',
        goku_insight='',
    )

    if verbose:
        print(result)

    # Log to database
    try:
        from logging_db.trade_logger import log_debate
        log_debate(
            symbol=symbol,
            buy_votes=vote_breakdown.get('BUY', 0),
            hold_votes=vote_breakdown.get('HOLD', 0),
            sell_votes=vote_breakdown.get('SELL', 0),
            final_signal=result.synthesized_signal,
            confidence=result.synthesized_confidence,
            reasoning=result.unified_reasoning,
            bull_case=result.bull_case,
            bear_case=result.bear_case,
            key_risk=result.key_risk,
            agent_details=individual_signals,
            regime=regime,
        )
    except Exception:
        pass

    return result


def run_quick_debate(symbol: str, market_data: dict, context: str = '',
                     verbose: bool = False, memory_context: str = '',
                     asset_class: str = 'crypto') -> DebateResult:
    """Same as run_debate — all debates are the same speed now."""
    return run_debate(symbol=symbol, market_data=market_data,
                      context=context, agents_to_use=QUICK_DEBATE_AGENTS,
                      verbose=verbose, memory_context=memory_context,
                      asset_class=asset_class)


def ceil_2_of(total: int) -> int:
    """Minimum agents needed for majority (≥2/3)."""
    return max(2, round(total * 0.67))
