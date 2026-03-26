"""
strategies/ai_agents/analyst_agents.py

3 focused agents replace the old 9. Each owns exactly one non-overlapping domain.
Same external API — run_agent(), call_claude_structured(), AGENTS, AGENT_RESPONSE_SCHEMA.

Old system: 9 agents + moderator + Goku = up to 11 API calls per decision.
New system: 3 agents, majority vote, done = 3 API calls. 3.5× cheaper and faster.

Agents:
  funding_regime   — Crypto-native macro: funding rate, OI trend, cross-asset regime
  momentum_structure — Technical setup quality: ADX, squeeze, WAE, WaveTrend, MACD
  risk_economics   — Trade economics: ATR vs fees, volume, stop placement
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, DEBATE_MAX_TOKENS

# ── Agent definitions ──────────────────────────────────────────────────────────
AGENTS = {
    'funding_regime': {
        'name': 'Macro & Funding Intelligence',
        'dbz_name': 'Bardock',
        'style': (
            'Crypto-native macro analyst. Primary signal: perpetual funding rates. '
            'Funding > 0.05%/8h = longs are overloaded, squeeze risk is HIGH → lean HOLD or SELL. '
            'Funding 0.01-0.05%/8h = mild bullish bias, acceptable for longs. '
            'Funding near zero or slightly negative = market not crowded → best entry window for longs. '
            'Also reads: macro_score (-5 to +5), VIX regime (fear/neutral/complacent), '
            'DXY direction (rising DXY = crypto headwind), SPY trend (risk-on/off), '
            'and BTC 24h change as the crypto market pulse. '
            'RULE: If funding is overheated AND macro is risk-off, HOLD regardless of chart. '
            'If funding is neutral AND macro is risk-on, BUY bias. '
            'Give a clean directional read on whether the MACRO + FUNDING environment supports a long entry.'
        ),
        'key_questions': [
            'What is the current funding rate? Overheated (>0.05%/8h), normal (0.01-0.05%), or favorable (≤0.01%)?',
            'Is the macro environment risk-on or risk-off? (macro_score, VIX, DXY, SPY)',
            'Does OI trend confirm or contradict the price move?',
            'Would you be comfortable holding a long position right now given these macro + funding conditions?',
        ]
    },
    'momentum_structure': {
        'name': 'Technical Momentum & Structure',
        'dbz_name': 'Vegeta',
        'style': (
            'Technical setup specialist. Focus: is this chart a CLEAN setup or noise? '
            'ADX > 25 = real trend exists (momentum trades valid). ADX < 20 = ranging (mean-reversion only). '
            'BB-Keltner squeeze after ≥20 bars compression then firing = highest conviction breakout signal. '
            'WAE bullish + exploding = momentum is genuinely erupting (not faking). '
            'WaveTrend oversold cross from below -53 = oversold bounce setup. '
            'SuperTrend bullish = trend direction confirmed. '
            'MACD consensus (3 variants aligned) = momentum confirmation, not primary signal. '
            'RULE: require at least 2 of these to be green. One signal alone = HOLD. '
            'Two aligned signals = cautious BUY. Three+ = strong BUY. '
            'Be HARSH — marginal setups are not worth the fees. Only recommend BUY when the setup is genuinely clean.'
        ),
        'key_questions': [
            'What is ADX? Is there a real trend (>25) or is this choppy (<20)?',
            'Did the squeeze fire with direction? After how many compressed bars?',
            'Is WAE bullish AND exploding (full momentum), or just bullish (partial)?',
            'How many of these are green: WAE, squeeze, WaveTrend, SuperTrend, MACD consensus?',
        ]
    },
    'risk_economics': {
        'name': 'Trade Economics & Risk',
        'dbz_name': 'Krillin',
        'style': (
            'Trade economics specialist and kill switch. NO trade passes without clearing the fee math. '
            'Coinbase round-trip cost: ~1.2% (0.6% × 2 sides). '
            'Minimum gross move needed: 2.4% (2× round-trip, R:R ≥ 1:1). '
            'ATR-based check: if ATR/price < 0.004 (0.4%), a 4×ATR target cannot clear fees — HARD HOLD. '
            'Volume: vol_spike < 0.3 = thin book, fills will be bad — HOLD. '
            'Time of day: 2am-5am ET = dead zone, spread is wide, HOLD unless very strong setup. '
            'Position size check: with ATR-based stop at 2×ATR, is the max loss on this trade ≤ 1% of account? '
            'RULE: If fee math fails OR volume is too thin OR time is dead zone → HOLD regardless of chart. '
            'If economics are clean, BUY (defer to other agents for direction). '
            'You are not a direction predictor — you are the gate that ensures every trade is economically viable.'
        ),
        'key_questions': [
            'Is ATR/price ≥ 0.4%? If not, fees cannot be cleared — HOLD.',
            'Is volume spike ≥ 0.3× baseline? If not, the book is too thin.',
            'Is the current time a favorable trading window? (Avoid 2am-5am ET)',
            'Does the ATR-based stop (2×ATR below entry) keep max loss ≤ 1% of $500?',
        ]
    },
}

AGENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "signal":     {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning":  {"type": "string"},
        "key_concern": {"type": "string"},
    },
    "required": ["signal", "confidence", "reasoning", "key_concern"],
}


def get_all_agents() -> list:
    return list(AGENTS.keys())


# ── Core API caller ────────────────────────────────────────────────────────────

def call_claude_structured(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 700,
    call_type: str = 'agent',
    schema: Optional[dict] = None,
    cache_system: bool = True,
) -> dict:
    """
    Single API call to Claude with structured JSON output.
    Returns parsed dict. Never raises — returns safe HOLD default on any error.
    """
    if not ANTHROPIC_API_KEY:
        return {'signal': 'HOLD', 'confidence': 0.0, 'reasoning': 'No API key.', 'key_concern': 'No API key.'}

    resp_schema = schema or AGENT_RESPONSE_SCHEMA

    system_content = (
        [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        if cache_system else system_prompt
    )

    payload = {
        "model":      CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system":     system_content,
        "messages":   [{"role": "user", "content": user_prompt}],
        "tools": [{
            "name":        "trade_decision",
            "description": "Return the trading decision",
            "input_schema": resp_schema,
        }],
        "tool_choice": {"type": "tool", "name": "trade_decision"},
    }

    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
                "anthropic-beta":    "prompt-caching-2024-07-31",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())

        for block in body.get("content", []):
            if block.get("type") == "tool_use":
                return block.get("input", {})

        return {'signal': 'HOLD', 'confidence': 0.0,
                'reasoning': 'No tool_use block in response.', 'key_concern': 'parse_error'}

    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        print(f"[analyst_agents] HTTP {e.code} ({call_type}): {err}")
        return {'signal': 'HOLD', 'confidence': 0.0, 'reasoning': f'HTTP {e.code}', 'key_concern': err}
    except Exception as e:
        print(f"[analyst_agents] error ({call_type}): {e}")
        return {'signal': 'HOLD', 'confidence': 0.0, 'reasoning': str(e), 'key_concern': str(e)}


# ── Market data formatter ──────────────────────────────────────────────────────

def _format_market_data(symbol: str, md: dict) -> str:
    """Compact market data block for agent prompts. Tuned per agent type."""
    def _f(k, default='?', fmt='.4f'):
        v = md.get(k)
        return format(float(v), fmt) if v is not None else str(default)

    lines = [
        f"Symbol: {symbol} | Price: ${_f('price', fmt=',.4f')} | Regime: {md.get('regime', '?')}",
        f"Funding: {_f('funding_rate_pct', '?', '.5f')}%/8h ({md.get('funding_signal', '?')}) | "
        f"OI change: {_f('oi_change_pct', '?', '.2f')}%",
        f"Macro score: {md.get('macro_score', '?')} | VIX regime: {md.get('vix_regime', '?')} | "
        f"DXY chg: {_f('dxy_change', '?', '.2f')}% | SPY chg: {_f('spy_change', '?', '.2f')}%",
        f"ADX: {_f('adx', fmt='.1f')} | ATR: {_f('atr', fmt='.6f')} | "
        f"ATR/price: {_f('atr_pct', '?', '.3f')}% | Vol spike: {_f('vol_spike', fmt='.2f')}x",
        f"Squeeze fired: {md.get('squeeze_fired', False)} ({_f('squeeze_bars', '0', '.0f')} bars) | "
        f"RV ratio: {_f('rv_ratio', '?', '.2f')}",
        f"WAE bullish: {md.get('wae_bullish', False)} | WAE exploding: {md.get('wae_exploding', False)} | "
        f"WaveTrend cross: {md.get('wt_oversold_cross', False)}",
        f"SuperTrend bullish: {md.get('supertrend_bullish', False)} | "
        f"MACD consensus: {md.get('macd_consensus', False)} | "
        f"Ichimoku bullish: {md.get('cloud_bullish', False)}",
        f"Conviction score: {md.get('conviction_score', '?')} | "
        f"Active signals: {md.get('signal_triggers', 'none')}",
    ]

    # ML signal if available
    if md.get('ml_p_win') is not None:
        lines.append(f"ML P(win): {float(md['ml_p_win']):.1%} | ML confidence: {md.get('ml_confidence', '?')}")

    # Rolling backtest context if available
    if md.get('backtest_context'):
        lines.append(f"Backtest: {md['backtest_context']}")

    # Bayesian signal stats if available
    if md.get('signal_stats_brief'):
        lines.append(f"Signal stats: {md['signal_stats_brief']}")

    return '\n'.join(lines)


# ── Agent runner ───────────────────────────────────────────────────────────────

def run_agent(
    agent_key: str,
    symbol: str,
    market_data: dict,
    context: str = '',
    memory_context: str = '',
    asset_class: str = 'crypto',
) -> dict:
    """Run one analyst agent. Returns signal dict with agent_key added."""
    agent = AGENTS.get(agent_key)
    if not agent:
        return {'signal': 'HOLD', 'confidence': 0.0, 'reasoning': f'Unknown agent: {agent_key}',
                'key_concern': '', 'agent': agent.get('name', agent_key), 'agent_key': agent_key}

    system_prompt = f"""You are {agent['name']} ({agent['dbz_name']}), an elite trading analyst.

Your specialty: {agent['style']}

Your key questions to answer:
{chr(10).join(f'- {q}' for q in agent['key_questions'])}

ABSOLUTE RULES:
- Be direct and decisive. No hedging.
- Give a clear BUY, SELL, or HOLD with a confidence score (0.0–1.0).
- One sentence reasoning max. One sentence key concern max.
- This is a $500 crypto account. Capital preservation matters — when in doubt, HOLD.
- You are NOT asked to consider the other analysts' views. Focus only on your domain."""

    market_brief = _format_market_data(symbol, market_data)
    ctx_block = f"\nCONTEXT:\n{context}" if context else ''
    mem_block  = f"\nMEMORY:\n{memory_context[:400]}" if memory_context else ''

    user_prompt = f"""{market_brief}{ctx_block}{mem_block}

Based on your specialty ({agent['name']}), what is your trading decision for {symbol}?"""

    result = call_claude_structured(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=DEBATE_MAX_TOKENS,
        call_type=f'agent_{agent_key}',
    )
    result['agent'] = agent['name']
    result['agent_key'] = agent_key
    return result
