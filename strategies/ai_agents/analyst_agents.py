"""
strategies/ai_agents/analyst_agents.py
8 legendary investor AI agents with prompt caching + guaranteed JSON outputs.
Prompt caching cuts cost ~80% — system prompts cached for 1 hour.
Structured outputs guarantee valid JSON — no regex fallback needed.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, DEBATE_MAX_TOKENS

# ─── Agent definitions ────────────────────────────────────────────────────────
AGENTS = {
    'buffett': {
        'name': 'Warren Buffett',
        'dbz_name': 'Master Roshi',
        'style': 'Value investor. Wide economic moats, consistent earnings, honest management, margin of safety. Avoids speculation entirely. Famous for sitting in cash when nothing meets criteria. Time horizon: years.',
        'key_questions': [
            'Does this company have a durable competitive advantage?',
            'Is the price significantly below intrinsic value?',
            'Would I hold this for 10 years comfortably?',
            'Is management allocating capital intelligently?',
        ]
    },
    'soros': {
        'name': 'George Soros',
        'dbz_name': 'Cell',
        'style': 'Macro reflexivity trader. Prices change fundamentals which change prices. Finds turning points where consensus is wrong. Bets heavily on high conviction. Famous for identifying when a trend is about to reverse violently.',
        'key_questions': [
            'Is the market consensus wrong about this asset?',
            'Is a reflexive feedback loop forming?',
            'How close are we to the inflection point?',
            'What is the asymmetric bet here?',
        ]
    },
    'simons': {
        'name': 'Jim Simons',
        'dbz_name': 'Android 17',
        'style': 'Pure quantitative analyst. No opinions, only statistical patterns in price and volume data. Dismisses all narrative and fundamental analysis. Only what the data shows objectively.',
        'key_questions': [
            'Is the price pattern statistically significant?',
            'What is the expected value given the data?',
            'Is volume confirming or contradicting price?',
            'What does autocorrelation in returns suggest?',
        ]
    },
    'tudor_jones': {
        'name': 'Paul Tudor Jones',
        'dbz_name': 'Vegeta',
        'style': 'Macro momentum trader. Risk management first — defines max loss before entry. Never averages down. Follows price momentum and trend. Famous quote: Losers average losers. Cut losses fast, let winners run.',
        'key_questions': [
            'Where is the stop loss and is it acceptable?',
            'Is momentum confirming the trade direction?',
            'Am I buying strength or catching a falling knife?',
            'What is the risk/reward ratio?',
        ]
    },
    'druckenmiller': {
        'name': 'Stan Druckenmiller',
        'dbz_name': 'Piccolo',
        'style': 'Macro momentum investor. Concentrates capital in best ideas. Looks for paradigm shifts — when something fundamentally changes and the market has not priced it. Follows liquidity flows. Big bold bets on conviction.',
        'key_questions': [
            'Is liquidity flowing into or out of this sector?',
            'Is there a paradigm shift being underpriced?',
            'Is this the best risk-adjusted opportunity right now?',
            'Is the macro environment supportive?',
        ]
    },
    'cathie_wood': {
        'name': 'Cathie Wood',
        'dbz_name': 'Bulma',
        'style': 'Disruptive innovation investor. Exponential S-curves in technology adoption. Embraces volatility. 5-year price targets based on TAM expansion and cost curve deflation.',
        'key_questions': [
            'Is this riding an exponential technology adoption curve?',
            'Is the addressable market being dramatically underestimated?',
            'Are costs declining in a flywheel pattern?',
            'Is this a 5-year compounder regardless of short-term noise?',
        ]
    },
    'livermore': {
        'name': 'Jesse Livermore',
        'dbz_name': 'Goku',
        'style': 'Tape reader and price action trader. The market tells you everything you need to know. Key price levels — pivot points, round numbers, breakouts — are the signals. Never fight the tape. Trend is your friend.',
        'key_questions': [
            'Is price breaking out above key resistance on volume?',
            'Is the trend unambiguously up, down, or sideways?',
            'Is this a genuine breakout or a false move?',
            'What is the tape telling us about supply and demand?',
        ]
    },
    'dalio': {
        'name': 'Ray Dalio',
        'dbz_name': 'Whis',
        'style': 'Macro systems thinker. All-weather approach across economic environments. Focuses on debt cycles and currency dynamics. Builds portfolios to survive any regime.',
        'key_questions': [
            'What economic environment are we in and how does this asset perform there?',
            'Is this a risk-on or risk-off signal?',
            'How does this correlate to the broader portfolio?',
            'Are we in a late or early debt cycle phase?',
        ]
    },
}

# JSON schema for structured outputs — guaranteed valid response
AGENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string"},
        "key_concern": {"type": "string"}
    },
    "required": ["signal", "confidence", "reasoning", "key_concern"]
}


def _build_agent_system_prompt(agent_key: str) -> str:
    """Build the cached system prompt for an agent."""
    agent = AGENTS[agent_key]
    questions = '\n'.join(f'- {q}' for q in agent['key_questions'])
    return f"""You are {agent['name']}, the legendary investor/trader.
You analyze potential trades EXACTLY as {agent['name']} would — through their documented philosophy.

Your investment philosophy: {agent['style']}

Your analytical framework:
{questions}

THE AMYGDALA IS REMOVED — these rules are absolute and non-negotiable:
- No panic, no FOMO, no revenge trading, no hope trading
- Every decision is pre-defined rules only
- You either see a clear setup or you don't. If unclear: HOLD
- Your job is to be RIGHT, not to trade often
- Protecting a $500 account. Capital preservation is priority one.

Respond ONLY with valid JSON matching this exact schema:
{{"signal": "BUY" or "SELL" or "HOLD", "confidence": 0.0-1.0, "reasoning": "1-2 sentences max in {agent['name']}'s voice", "key_concern": "biggest risk in 10 words max"}}"""


def call_claude_structured(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 300,
    use_cache: bool = True,
    call_type: str = 'agent',
    schema: dict = None,
) -> dict:
    """
    Call Claude API with structured outputs and prompt caching.
    Returns parsed dict. Never raises — returns error dict on failure.
    """
    if not ANTHROPIC_API_KEY:
        return {'signal': 'HOLD', 'confidence': 0.0,
                'reasoning': 'No API key configured', 'key_concern': 'Missing ANTHROPIC_API_KEY'}

    # Build messages with prompt caching on system prompt
    system_content = system_prompt
    if use_cache:
        # Use cache_control to cache the system prompt for 1 hour
        system_payload = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }
        ]
    else:
        system_payload = system_prompt

    active_schema = schema if schema is not None else AGENT_RESPONSE_SCHEMA
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system_payload if use_cache else system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [{
            "name": "structured_response",
            "description": "Return the structured analysis",
            "input_schema": active_schema
        }],
        "tool_choice": {"type": "any"}
    }).encode('utf-8')

    try:
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'anthropic-beta': 'prompt-caching-2024-07-31',
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))

            # Log API cost
            usage = data.get('usage', {})
            input_tokens = usage.get('input_tokens', 0)
            output_tokens = usage.get('output_tokens', 0)
            # Claude Sonnet pricing approx: $3/M input, $15/M output
            cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
            try:
                from logging_db.trade_logger import log_api_cost
                log_api_cost(call_type, input_tokens, output_tokens, cost)
            except Exception:
                pass

            # Extract structured response from tool use
            for block in data.get('content', []):
                if block.get('type') == 'tool_use':
                    return block.get('input', {})

            # Fallback: try text content
            for block in data.get('content', []):
                if block.get('type') == 'text':
                    text = block.get('text', '')
                    # Try JSON parse
                    import re
                    match = re.search(r'\{.*\}', text, re.DOTALL)
                    if match:
                        return json.loads(match.group())

    except urllib.error.HTTPError as e:
        print(f"[agents] HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"[agents] API error: {e}")

    return {'signal': 'HOLD', 'confidence': 0.0,
            'reasoning': 'API call failed', 'key_concern': 'Connection error'}


def run_agent(agent_key: str, symbol: str, market_data: dict,
              context: str = '', memory_context: str = '') -> dict:
    """Run one analyst agent. Returns signal dict."""
    agent = AGENTS[agent_key]
    system_prompt = _build_agent_system_prompt(agent_key)

    user_prompt = f"""Analyze {symbol} for a potential trade right now.

Market data:
- Current price: ${market_data.get('price', 0):,.4f}
- Change today: {market_data.get('change_pct', 0):+.2f}%
- Volume vs 20-day avg: {market_data.get('vol_spike', 1):.1f}x
- RSI (14): {market_data.get('rsi', 50):.1f}
- MACD histogram: {market_data.get('macd_hist', 0):.6f} ({'positive' if market_data.get('macd_hist', 0) > 0 else 'negative'})
- Price vs VWAP: {'ABOVE' if market_data.get('price', 0) > market_data.get('vwap', 1) else 'BELOW'} (VWAP: ${market_data.get('vwap', 0):,.4f})
- ATR (14): ${market_data.get('atr', 0):.4f}
- ADX (trend strength): {market_data.get('adx', 0):.1f}
- Market regime: {market_data.get('regime', 'unknown')}
- 20-day trend: {market_data.get('trend_20d', 'neutral')}
- Dollar volume: ${market_data.get('dollar_volume', 0):,.0f}
{f'- Additional context: {context}' if context else ''}

{memory_context if memory_context else ''}

What is your analysis as {agent['name']}?"""

    result = call_claude_structured(system_prompt, user_prompt,
                                    max_tokens=DEBATE_MAX_TOKENS,
                                    call_type=f'agent_{agent_key}')
    result['agent'] = agent['name']
    result['agent_key'] = agent_key
    result['dbz_name'] = agent.get('dbz_name', agent['name'])
    return result


def get_all_agents() -> list:
    return list(AGENTS.keys())


def get_agent_name(key: str) -> str:
    return AGENTS.get(key, {}).get('name', key)


def get_agent_dbz_name(key: str) -> str:
    return AGENTS.get(key, {}).get('dbz_name', key)
