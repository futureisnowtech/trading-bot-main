"""
strategies/ai_agents/exit_review.py

AI-powered exit review using Claude's extended thinking (interleaved thinking).
Runs on every candle close for every open position.

Three exit agents — P&L-aware thresholds:
  Losing position (<+1% P&L): 1/3 agents EXIT → exit fast, don't let losers linger.
  Winning position (≥+1% P&L): 2/3 agents EXIT → protect winners, don't cut early.
  Entering needs 5/8 to agree. Exiting is calibrated to protect both capital and profits.

Agents:
  Tudor Jones: "Is the stop still valid? Is momentum intact?"
  Soros:       "Is the original thesis still intact?"
  Simons:      "Is the statistical pattern still holding?"

Extended thinking shows the reasoning chain — you can read it in Film Room view.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL_EXTENDED,
    EXIT_REVIEW_MAX_TOKENS, MARKET_TIMEZONE
)

EXIT_AGENTS = {
    'tudor_jones': {
        'name': 'Paul Tudor Jones',
        'exit_question': 'Is the stop still valid and is price momentum intact?',
        'exit_philosophy': 'Risk manager first. If momentum is broken or stop is too close, exit. Losers average losers — cut first, ask questions later.',
    },
    'soros': {
        'name': 'George Soros',
        'exit_question': 'Is the original thesis for this trade still intact?',
        'exit_philosophy': 'Reflexivity. If the story that made this a trade has changed — new information, regime shift, volume patterns reversing — exit. The thesis either holds or it does not.',
    },
    'simons': {
        'name': 'Jim Simons',
        'exit_question': 'Is the statistical pattern that justified entry still holding?',
        'exit_philosophy': 'Pure data. If the quantitative signal has reversed, if volume is now contradicting price, if the pattern has broken — exit. Opinions do not matter, only data.',
    },
}

EXIT_SCHEMA = {
    "type": "object",
    "properties": {
        "should_exit": {"type": "boolean"},
        "urgency": {"type": "string", "enum": ["immediate", "next_candle", "monitor", "hold"]},
        "reasoning": {"type": "string"},
        "thinking_summary": {"type": "string"}
    },
    "required": ["should_exit", "urgency", "reasoning", "thinking_summary"]
}


def run_exit_review(
    symbol: str,
    strategy: str,
    entry_price: float,
    current_price: float,
    stop_loss: float,
    take_profit: float,
    entry_reason: str,
    time_in_trade_minutes: int,
    market_data: dict,
    verbose: bool = False,
    entry_ts: str = '',
    asset_class: str = '',
) -> dict:
    """
    Run 3-agent exit review with extended thinking.
    Returns: {'should_exit': bool, 'reason': str, 'urgency': str, 'agent_reviews': list}

    Winners (pnl >= +1%): need 2/3 agents to exit — protect profits.
    Losers (pnl < +1%): need 1/3 agents to exit — cut fast.
    """
    if not ANTHROPIC_API_KEY:
        return {'should_exit': False, 'reason': 'No API key', 'urgency': 'hold', 'agent_reviews': []}

    pnl_pct = (current_price - entry_price) / entry_price * 100
    distance_to_stop_pct = (current_price - stop_loss) / current_price * 100
    distance_to_target_pct = (take_profit - current_price) / current_price * 100

    # ── Tax context injection ─────────────────────────────────────────────────
    tax_note = ''
    try:
        from learning.tax_tracker import get_tax_aware_exit_note
        # Infer asset_class from strategy if not provided
        _ac = asset_class or ('futures' if 'futures' in strategy.lower() or 'mes' in strategy.lower()
                              else 'perp' if 'perp' in strategy.lower()
                              else 'equity' if 'equity' in strategy.lower()
                              else 'crypto')
        _ts = entry_ts or ''
        unrealized = (current_price - entry_price) * (take_profit - entry_price) / max(
            take_profit - entry_price, 0.001)  # rough unrealized in direction
        tax_note = get_tax_aware_exit_note(symbol, strategy, _ac, _ts, pnl_pct * entry_price / 100)
    except Exception:
        pass

    agent_reviews = []
    exit_votes = []

    for agent_key, agent in EXIT_AGENTS.items():
        review = _run_exit_agent(
            agent_key=agent_key,
            agent=agent,
            symbol=symbol,
            entry_price=entry_price,
            current_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            pnl_pct=pnl_pct,
            distance_to_stop_pct=distance_to_stop_pct,
            distance_to_target_pct=distance_to_target_pct,
            entry_reason=entry_reason,
            time_in_trade_minutes=time_in_trade_minutes,
            market_data=market_data,
            tax_note=tax_note,
        )
        agent_reviews.append({**review, 'agent': agent['name'], 'agent_key': agent_key})

        if review.get('should_exit', False):
            exit_votes.append(review)

        if verbose:
            status = '🔴 EXIT' if review.get('should_exit') else '🟢 HOLD'
            print(f"  [exit] {agent['name']:22} → {status} | {review.get('reasoning','')[:60]}")

    total_agents = len(EXIT_AGENTS)
    exit_count = len(exit_votes)

    # P&L-aware exit threshold:
    # Winning position (>+1%): require 2/3 agents to agree — don't cut winners short
    # Losing position (<+1%): require only 1/3 — cut losers fast
    is_winner = pnl_pct >= 1.0
    votes_required = 2 if is_winner else 1

    if exit_count >= votes_required:
        urgencies = [v.get('urgency', 'monitor') for v in exit_votes]
        urgency = 'immediate' if 'immediate' in urgencies else 'next_candle' if 'next_candle' in urgencies else 'monitor'
        reasons = ' | '.join(set(v.get('reasoning', '') for v in exit_votes))
        agents_exiting = [r['agent'] for r in agent_reviews if r.get('should_exit')]
        threshold_note = f'{exit_count}/{total_agents} agree'
        return {
            'should_exit': True,
            'reason': f"[{threshold_note}: {', '.join(agents_exiting)} EXIT] {reasons}",
            'urgency': urgency,
            'agent_reviews': agent_reviews,
        }

    return {
        'should_exit': False,
        'reason': f'Exit threshold not met ({exit_count}/{total_agents}, need {votes_required})',
        'urgency': 'hold',
        'agent_reviews': agent_reviews,
    }


def _run_exit_agent(agent_key, agent, symbol, entry_price, current_price,
                    stop_loss, take_profit, pnl_pct, distance_to_stop_pct,
                    distance_to_target_pct, entry_reason, time_in_trade_minutes,
                    market_data, tax_note: str = '') -> dict:
    """Run one exit agent with extended thinking."""
    rsi = market_data.get('rsi', 50)
    macd_hist = market_data.get('macd_hist', 0)
    vwap = market_data.get('vwap', current_price)
    adx = market_data.get('adx', 25)
    vol_spike = market_data.get('vol_spike', 1)

    system_prompt = f"""You are {agent['name']}, reviewing an OPEN POSITION for potential exit.

Your exit philosophy: {agent['exit_philosophy']}

Your key question: {agent['exit_question']}

THE AMYGDALA IS REMOVED:
- No holding a loser hoping it comes back
- No exiting a winner early out of fear
- No revenge holding after the thesis broke
- Be honest. If the setup has deteriorated, say so clearly.

Think through this carefully before deciding. Show your reasoning process."""

    user_prompt = f"""Open position review for {symbol}:

POSITION STATUS:
- Entry price: ${entry_price:.4f}
- Current price: ${current_price:.4f}
- P&L: {pnl_pct:+.2f}%
- Stop loss: ${stop_loss:.4f} ({distance_to_stop_pct:.1f}% away)
- Take profit: ${take_profit:.4f} ({distance_to_target_pct:.1f}% away)
- Time in trade: {time_in_trade_minutes} minutes
- Original entry reason: {entry_reason}

CURRENT MARKET DATA:
- RSI: {rsi:.1f}
- MACD histogram: {macd_hist:.6f} ({'positive' if macd_hist > 0 else 'NEGATIVE — momentum may be fading'})
- Price vs VWAP: {'ABOVE' if current_price > vwap else 'BELOW — possible trend break'} (VWAP: ${vwap:.4f})
- ADX: {adx:.1f}
- Volume spike: {vol_spike:.1f}x
{f'{chr(10)}{tax_note}' if tax_note else ''}

Should we EXIT this position now?"""

    # Use extended thinking — Claude reasons before answering
    if not ANTHROPIC_API_KEY:
        return {'should_exit': False, 'urgency': 'hold',
                'reasoning': 'No API key', 'thinking_summary': ''}

    try:
        payload = json.dumps({
            "model": CLAUDE_MODEL_EXTENDED,
            "max_tokens": EXIT_REVIEW_MAX_TOKENS,
            "thinking": {
                "type": "enabled",
                "budget_tokens": 500
            },
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            "messages": [{"role": "user", "content": user_prompt}],
            "tools": [{
                "name": "exit_decision",
                "description": "Submit exit analysis",
                "input_schema": EXIT_SCHEMA
            }],
            "tool_choice": {"type": "tool", "name": "exit_decision"}
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'anthropic-beta': 'interleaved-thinking-2025-05-14,prompt-caching-2024-07-31',
            },
            method='POST'
        )

        thinking_text = ''
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode('utf-8'))

            # Extract thinking blocks
            for block in data.get('content', []):
                if block.get('type') == 'thinking':
                    thinking_text = block.get('thinking', '')

            # Extract structured tool response
            for block in data.get('content', []):
                if block.get('type') == 'tool_use':
                    result = block.get('input', {})
                    result['thinking_summary'] = thinking_text[:300] if thinking_text else ''

                    # Log cost
                    usage = data.get('usage', {})
                    cost = (usage.get('input_tokens', 0) * 3 +
                            usage.get('output_tokens', 0) * 15) / 1_000_000
                    try:
                        from logging_db.trade_logger import log_api_cost
                        log_api_cost(f'exit_{agent_key}', usage.get('input_tokens', 0),
                                     usage.get('output_tokens', 0), cost, symbol)
                    except Exception:
                        pass

                    return result

    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print(f"[exit_review] HTTP {e.code} for {agent_key}: {body}")
        # Fallback to non-thinking call
        return _exit_agent_fallback(agent_key, agent, system_prompt, user_prompt)
    except Exception as e:
        print(f"[exit_review] Error {agent_key}: {e}")

    return {'should_exit': False, 'urgency': 'monitor',
            'reasoning': 'API error — holding conservatively', 'thinking_summary': ''}


def _exit_agent_fallback(agent_key, agent, system_prompt, user_prompt) -> dict:
    """Non-extended-thinking fallback if beta header fails."""
    from strategies.ai_agents.analyst_agents import call_claude_structured
    result = call_claude_structured(
        system_prompt=system_prompt,
        user_prompt=user_prompt + "\n\nRespond with should_exit (true/false), urgency, reasoning, thinking_summary.",
        max_tokens=200,
        call_type=f'exit_fallback_{agent_key}'
    )
    # Map agent signal to exit decision
    signal = result.get('signal', 'HOLD')
    return {
        'should_exit': signal == 'SELL',
        'urgency': 'next_candle' if signal == 'SELL' else 'hold',
        'reasoning': result.get('reasoning', ''),
        'thinking_summary': result.get('key_concern', ''),
    }
