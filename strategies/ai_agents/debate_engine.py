"""
strategies/ai_agents/debate_engine.py
The debate chamber. 8 agents analyze independently, moderator synthesizes into one decision.
Uses structured outputs — guaranteed valid JSON, zero parse failures.
Full debate: 8 agents (equity). Quick debate: 3 agents (crypto, futures).
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
from config import MARKET_TIMEZONE, CLAUDE_MODEL, QUICK_DEBATE_AGENTS, FULL_DEBATE_MIN_AGREEMENT

MODERATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string"},
        "bull_case": {"type": "string"},
        "bear_case": {"type": "string"},
        "key_risk": {"type": "string"}
    },
    "required": ["signal", "confidence", "reasoning", "bull_case", "bear_case", "key_risk"]
}


class DebateResult:
    def __init__(self, symbol, individual_signals, synthesized_signal,
                 synthesized_confidence, unified_reasoning, bull_case,
                 bear_case, key_risk, vote_breakdown, timestamp, regime=''):
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

    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol,
            'synthesized_signal': self.synthesized_signal,
            'synthesized_confidence': self.synthesized_confidence,
            'unified_reasoning': self.unified_reasoning,
            'bull_case': self.bull_case,
            'bear_case': self.bear_case,
            'key_risk': self.key_risk,
            'vote_breakdown': self.vote_breakdown,
            'individual_signals': self.individual_signals,
            'timestamp': self.timestamp,
            'regime': self.regime,
        }

    def __repr__(self):
        b = self.vote_breakdown
        e = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪'}.get(self.synthesized_signal, '⚪')
        return (
            f"\n{'═'*60}\n  DEBATE: {self.symbol} | Regime: {self.regime}\n{'═'*60}\n"
            f"  Votes: {b.get('BUY',0)} BUY | {b.get('HOLD',0)} HOLD | {b.get('SELL',0)} SELL\n"
            f"  Decision: {e} {self.synthesized_signal} ({self.synthesized_confidence:.0%})\n"
            f"  Reason: {self.unified_reasoning}\n"
            f"  Bull: {self.bull_case}\n  Bear: {self.bear_case}\n"
            f"  Risk: {self.key_risk}\n{'═'*60}"
        )


def run_debate(symbol: str, market_data: dict, context: str = '',
               agents_to_use: Optional[list] = None, verbose: bool = True,
               memory_context: str = '') -> DebateResult:
    """
    Full debate — all 8 agents (or specified subset).
    Includes regime detection and memory context.
    """
    tz = pytz.timezone(MARKET_TIMEZONE)
    timestamp = datetime.now(tz).isoformat()

    if agents_to_use is None:
        agents_to_use = get_all_agents()

    # Detect regime and add to market_data
    regime_data = detect_regime()
    regime = regime_data.get('regime', 'ranging')
    regime_brief = get_regime_brief(regime_data)
    market_data['regime'] = regime
    enhanced_context = f"{regime_brief}\n\n{context}" if context else regime_brief

    if verbose:
        print(f"\n🏛️  DEBATE: {symbol} | {regime.upper()} regime | {len(agents_to_use)} analysts")

    individual_signals = []
    for agent_key in agents_to_use:
        if verbose:
            name = AGENTS[agent_key]['name']
            print(f"  📊 {name}...", end=' ', flush=True)

        result = run_agent(agent_key, symbol, market_data,
                           context=enhanced_context, memory_context=memory_context)
        individual_signals.append(result)
        time.sleep(0.2)

        if verbose:
            sig = result.get('signal', 'HOLD')
            conf = result.get('confidence', 0)
            e = {'BUY': '🟢', 'SELL': '🔴', 'HOLD': '⚪'}.get(sig, '⚪')
            print(f"{e} {sig} ({conf:.0%}) — {result.get('reasoning','')[:55]}")

    vote_breakdown = {'BUY': 0, 'SELL': 0, 'HOLD': 0}
    for s in individual_signals:
        v = s.get('signal', 'HOLD')
        vote_breakdown[v] = vote_breakdown.get(v, 0) + 1

    if verbose:
        print(f"\n  Votes: {vote_breakdown['BUY']} BUY | {vote_breakdown['HOLD']} HOLD | {vote_breakdown['SELL']} SELL")

    synthesis = _run_moderator(symbol, market_data, individual_signals,
                                vote_breakdown, regime_brief)

    result = DebateResult(
        symbol=symbol,
        individual_signals=individual_signals,
        synthesized_signal=synthesis.get('signal', 'HOLD'),
        synthesized_confidence=synthesis.get('confidence', 0.0),
        unified_reasoning=synthesis.get('reasoning', ''),
        bull_case=synthesis.get('bull_case', ''),
        bear_case=synthesis.get('bear_case', ''),
        key_risk=synthesis.get('key_risk', ''),
        vote_breakdown=vote_breakdown,
        timestamp=timestamp,
        regime=regime,
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
                     verbose: bool = False, memory_context: str = '') -> DebateResult:
    """3-agent quick debate for crypto and futures (lower cost, faster)."""
    return run_debate(symbol=symbol, market_data=market_data,
                      context=context, agents_to_use=QUICK_DEBATE_AGENTS,
                      verbose=verbose, memory_context=memory_context)


def _run_moderator(symbol, market_data, individual_signals,
                   vote_breakdown, regime_brief) -> dict:
    """CIO moderator synthesizes all agent views into one final decision."""
    total = sum(vote_breakdown.values())
    buy_pct = vote_breakdown.get('BUY', 0) / total if total > 0 else 0

    debate_lines = []
    for s in individual_signals:
        debate_lines.append(
            f"  {s.get('agent','?'):22} → {s.get('signal','?'):4} "
            f"({s.get('confidence',0):.0%}) | {s.get('reasoning','')[:70]} "
            f"| Risk: {s.get('key_concern','')[:35]}"
        )

    system_prompt = """You are the Chief Investment Officer moderating a debate between legendary investors.
Synthesize their conflicting views into ONE actionable trading decision.

THE AMYGDALA IS REMOVED — ABSOLUTE RULES:
- No emotional reasoning. No hope. No fear. No FOMO.
- Split vote (< 60% agreement) → HOLD is correct.
- Any analyst flagging catastrophic risk → veto the trade.
- Protecting a $500 account. Capital preservation is priority one.
- A skipped trade loses nothing. A bad trade can end the account."""

    user_prompt = f"""Symbol: {symbol}
Price: ${market_data.get('price', 0):,.4f}
Volume spike: {market_data.get('vol_spike', 1):.1f}x
Votes: {vote_breakdown.get('BUY',0)} BUY | {vote_breakdown.get('HOLD',0)} HOLD | {vote_breakdown.get('SELL',0)} SELL
Agreement: {buy_pct:.0%}

{regime_brief}

ANALYST DEBATE:
{chr(10).join(debate_lines)}

Synthesize into one final trading decision. Need {FULL_DEBATE_MIN_AGREEMENT:.0%} BUY agreement to recommend BUY."""

    result = call_claude_structured(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=400,
        call_type='moderator',
        schema=MODERATOR_SCHEMA,
    )

    # Override with HOLD if agreement below threshold
    if (result.get('signal') == 'BUY' and
            buy_pct < FULL_DEBATE_MIN_AGREEMENT):
        result['signal'] = 'HOLD'
        result['reasoning'] = (
            f"Insufficient consensus ({buy_pct:.0%} < {FULL_DEBATE_MIN_AGREEMENT:.0%} required). "
            + result.get('reasoning', '')
        )

    return result
