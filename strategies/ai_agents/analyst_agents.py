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
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_DEBATE_MODEL, DEBATE_MAX_TOKENS

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

    # ── MES Futures Agents (Sprint 5) ─────────────────────────────────────────
    # Different market, different domains. Same debate mechanics (2/3 BUY = BUY).
    'mes_momentum_risk': {
        'name': 'Momentum & Risk Manager',
        'dbz_name': 'Tudor Jones',
        'style': (
            'MES futures momentum and risk specialist. Read the tape: is momentum real or fading? '
            'Opening Range Breakout context: price broke the first 5-min high/low, then pulled back. '
            'The pullback must respect the breakout level — if it breaks back through, this setup is invalid. '
            'ADX > 20 = real momentum. ADX < 18 = chop, HOLD. '
            'HTF (30-min) bias must align: LONG setup needs BULLISH or NEUTRAL HTF, not BEARISH. '
            'Risk rules for $500 futures account: 1 MES contract max. Daily stop = -5 pts (-$25). '
            'Commission: $0.59/side × 2 = $1.18 round-trip. Trade must be worth the commission. '
            'Target minimum: 1.5× stop (e.g. stop 3 pts → target ≥ 4.5 pts). '
            'RULE: If HTF misaligns, or ADX < 18, or the setup looks like a failed breakout → HOLD.'
        ),
        'key_questions': [
            'Is the ORB pullback respecting the breakout level (price bounced, not broke through)?',
            'Is ADX > 20 confirming real momentum?',
            'Does 30-min HTF bias align with the trade direction?',
            'Is the R:R at least 1.5:1 after Tradovate commission ($1.18 round-trip)?',
        ]
    },
    'mes_quant': {
        'name': 'Quantitative Pattern Analyst',
        'dbz_name': 'Jim Simons',
        'style': (
            'Quantitative edge and pattern quality analyst for MES futures. '
            'Opening Range Breakout pullback patterns work best when: '
            '(1) Volume on breakout bar was above average, '
            '(2) Pullback volume is LOWER than breakout volume (conviction stays with breakout), '
            '(3) VIX < 20 (calm markets = predictable ORB patterns), '
            '(4) ES has a clear trend intraday, not a range day. '
            'Pattern degraders: VIX > 20 (adds noise), gap-open days (skip — opening range unreliable), '
            'tight pre-market range < 5 pts (ORB pattern fails on low-energy days). '
            'Statistics: ORB pullback patterns historically win ~58-62% when volume confirms. '
            'Without volume confirmation they win ~42%. '
            'RULE: If volume pattern is wrong (pullback higher volume than breakout) → HOLD. '
            'If VIX > 25 → HOLD (engine already blocks this, but confirm). '
            'If conditions confirm the pattern, BUY.'
        ),
        'key_questions': [
            'Was breakout bar volume above the 10-bar average? (confirmation of genuine breakout)',
            'Is pullback volume LOWER than breakout bar volume? (key ORB confirmation signal)',
            'Is VIX < 20? If elevated (20-25), discount confidence. If > 25, HOLD.',
            'Is today a trend day (directional) or range day (skip ORB)? What does the pre-market range suggest?',
        ]
    },
    'mes_market_structure': {
        'name': 'Market Structure & Tape Reader',
        'dbz_name': 'Jesse Livermore',
        'style': (
            'Market structure and entry timing specialist for MES futures. '
            'Read the structure: where are the key levels? Is the pullback touching a significant level? '
            'ORB pullback entries are strongest when the breakout level also coincides with: VWAP, '
            'a prior swing high/low, or a round number (e.g., 5800, 5825). '
            'Check if price is above or below VWAP after the pullback: '
            'LONG setup should have price above VWAP after pulling back to breakout level. '
            'Pre-market accumulation signal: if lower wicks > upper wicks in pre-market candles → '
            'institutional buying bias — supports LONG. '
            'Timing: 9:35-10:30am ET is the ORB window. '
            'After 10:30am, skip new ORB entries (breakout energy dissipates). '
            'Close Auction (3:00-3:30pm): trade with last-hour trend. '
            'RULE: If price is below VWAP on a long entry → HOLD (wrong side of structure). '
            'If the pullback overshoots the level significantly → HOLD (failed breakout pattern).'
        ),
        'key_questions': [
            'Does the breakout level coincide with VWAP, a swing level, or a round number?',
            'Is price above VWAP (for LONG) or below VWAP (for SHORT) after the pullback?',
            'Did the pre-market show accumulation (lower wick dominance)?',
            'Is the pullback touching the level cleanly, or has it overshot (potential failed breakout)?',
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
        "model":      CLAUDE_DEBATE_MODEL,
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
    prior_reasoning: str = '',   # Sprint 5: state chaining — prior agents' conclusions
) -> dict:
    """Run one analyst agent. Returns signal dict with agent_key added.

    prior_reasoning: accumulated output from agents that ran before this one.
    Passed in the user prompt so the system prompt (persona) stays cached.
    """
    agent = AGENTS.get(agent_key)
    if not agent:
        return {'signal': 'HOLD', 'confidence': 0.0, 'reasoning': f'Unknown agent: {agent_key}',
                'key_concern': '', 'agent': agent_key, 'agent_key': agent_key}

    account_note = 'This is a $500 futures account (1 MES contract max).' if asset_class == 'mes' \
                   else 'This is a $500 crypto account. Capital preservation matters.'

    system_prompt = f"""You are {agent['name']} ({agent['dbz_name']}), an elite trading analyst.

Your specialty: {agent['style']}

Your key questions to answer:
{chr(10).join(f'- {q}' for q in agent['key_questions'])}

ABSOLUTE RULES:
- Be direct and decisive. No hedging.
- Give a clear BUY, SELL, or HOLD with a confidence score (0.0–1.0).
- One sentence reasoning max. One sentence key concern max.
- {account_note}
- Focus on your domain. You MAY consider prior analysts' views, but your primary lens is your specialty."""

    market_brief = _format_market_data(symbol, market_data)
    ctx_block = f"\nCONTEXT:\n{context}" if context else ''
    mem_block  = f"\nMEMORY:\n{memory_context[:400]}" if memory_context else ''
    prior_block = f"\nPRIOR ANALYST VIEWS (for awareness only):\n{prior_reasoning}" if prior_reasoning else ''

    user_prompt = f"""{market_brief}{ctx_block}{mem_block}{prior_block}

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
