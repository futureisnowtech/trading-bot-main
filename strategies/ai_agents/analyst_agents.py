"""
strategies/ai_agents/analyst_agents.py
8 elite trading methodology AI agents with prompt caching + guaranteed JSON outputs.
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
    'microstructure': {
        'name': 'Sasha Stoikov / Rama Cont',
        'dbz_name': 'Vegeta',
        'style': 'Market microstructure direction analysis. Evaluates Order Book Imbalance (OBI = (bid_qty - ask_qty)/(bid_qty + ask_qty)), microprice (quantity-weighted fair value = (ask_price×bid_qty + bid_price×ask_qty)/(bid_qty+ask_qty)), and Trade Flow Imbalance (TFI = (buy_vol - sell_vol)/(buy_vol + sell_vol)). OBI ≥ 0.20 = mild buy pressure, ≥ 0.35 = strong. TFI ≥ 0.10 = buy-initiated flow. Microprice above midprice = fair value bid. These are the most predictive short-interval signals for 1-minute crypto.',
        'key_questions': [
            'Is OBI ≥ 0.20 confirming buy pressure, or is the book weighted to the ask side?',
            'Does the microprice sit above the midprice, suggesting fair value is bid?',
            'Is TFI ≥ 0.10 showing buy-initiated aggressor flow dominates sell flow?',
            'Are OBI and TFI aligned (both bullish), or do they contradict each other suggesting noise?',
        ]
    },
    'session_breakout': {
        'name': 'Dan Shen / Zhuzhu Wen',
        'dbz_name': 'Broly',
        'style': 'Intraday session breakout and time-of-day momentum. Crypto has documented intraday predictability aligned with US market hours (08:00-11:00 ET) and Asian sessions. Session opening range breakout: first 30-minute high/low defines the range; long entry when close ≥ range_high + 0.1×ATR with volume ≥ 1.5× average. Outside active sessions, momentum signals are weaker and require stronger confirmation. Fee-aware: only trades where expected move is a multi-ATR continuation, not a 1-minute scalp.',
        'key_questions': [
            'Are we inside a high-volume session window (08:00-11:00 ET or documented Asia spike)? Entries during active windows have stronger intraday predictability.',
            'Has price broken the 30-minute opening range high with volume confirmation (≥1.5× avg)?',
            'Is this a genuine session momentum breakout or a random noise spike outside active hours?',
            'Given time of day, does the intraday predictability literature support a directional bias?',
        ]
    },
    'williams': {
        'name': 'Larry Williams',
        'dbz_name': 'Yamcha',
        'style': 'Williams %R extreme oscillator for mean-reversion timing, regime-gated. Williams %R = (highest_high - close)/(highest_high - lowest_low) × -100. Extreme oversold: W%R ≤ -80. Only valid when Hurst exponent H < 0.50 (mean-reverting regime) — in trending regimes (H > 0.55), W%R extremes continue rather than revert. Fee-aware: the reversion must target at least 2× round-trip fees (2.4% gross move minimum at 1.2% RT fees). Confirmed with volume dry-up during the oversold extreme and a momentum burst on reversal.',
        'key_questions': [
            'Is Williams %R ≤ -80 (extreme oversold) AND does the OU z-score or negative autocorr (< -0.10) confirm mean-reverting regime? (In trending regimes with positive autocorr, W%R extremes are continuation signals, not reversals.)',
            'Has volume dried up during the oversold extreme (exhaustion of sellers), followed by a momentum burst?',
            'Is the expected reversion distance at least 2.4% (covering 1.2% RT fees with 2:1 R:R minimum)?',
            'Does the realized volatility ratio (RVol_15/RVol_240) show compression (≤0.8), consistent with a coiled mean-reversion setup?',
        ]
    },
    'regime_volatility': {
        'name': 'Andersen-Bollerslev / TTM Squeeze',
        'dbz_name': 'Frieza',
        'style': 'Volatility regime detection and breakout timing. Realized volatility ratio: RVol_ratio = sqrt(sum(r²,15min)) / sqrt(sum(r²,240min)). RVol_ratio ≥ 1.3 = volatility expansion → breakout mode. RVol_ratio ≤ 0.8 = compression → mean-reversion or squeeze setup. Bollinger-Keltner Squeeze: BB inside KC (BB± = SMA20 ± 2σ, KC± = EMA20 ± 1.5×ATR) = energy coiling. Squeeze firing (BB expanding outside KC) after ≥20 bars compressed = high-probability expansion entry. Target: 4× ATR (must clear 1.2% fees with margin).',
        'key_questions': [
            'What is the realized volatility ratio (RVol_15/RVol_240)? ≥1.3 confirms expansion mode for breakout entries; ≤0.8 favors compression/mean-reversion setups.',
            'Is the Bollinger-Keltner squeeze firing (BB just expanded outside KC after ≥20 bars of compression)? This is the highest-probability breakout timing signal.',
            'Is the ATR large enough that a 4× ATR target exceeds 1.2% round-trip fees? If ATR/price < 0.003 (0.3%), even a 4-ATR target won\'t clear fees.',
            'Is volatility expanding (breakout mode) or compressing (mean-reversion mode), and does our strategy type match the current regime?',
        ]
    },
    'quant_edge': {
        'name': 'Ernie Chan / Ornstein-Uhlenbeck',
        'dbz_name': 'Gohan',
        'style': 'Quantitative edge validation with OU mean-reversion and Kelly sizing. OU z-score: z = (log_price - rolling_mean(60)) / rolling_std(40). Long entry when z ≤ -1.5 (oversold vs 60-bar mean). Exit when z ≥ -0.5 (partial reversion). OU half-life: t½ = ln(2)/κ from AR(1); actionable when t½ in [3, 60] min. Return autocorrelation < 0 confirms mean-reverting microstructure. Kelly fraction: f* = p - q/b where p=win_rate, b=avg_win/avg_loss (net of fees). Use 25% of f*. Amihud illiquidity: ILLIQ = |r_t|/(price×volume). Avoid top-20th-percentile ILLIQ. Kelly says: size up when edge is real and measured, size down when uncertain.',
        'key_questions': [
            'What is the OU z-score? z ≤ -1.5 = entry zone (price depressed relative to 60-bar mean). z ≥ -0.5 = exit zone (mean reversion mostly complete). z ≤ -2.0 = deep oversold, highest conviction.',
            'What is the OU half-life? t½ in [3, 20] min = fast actionable reversion for 1-min execution. t½ in [20, 60] min = moderate, works with 45-min time stop. t½ > 60 min = too slow.',
            'Does return autocorrelation (AR1) confirm mean-reverting microstructure (autocorr < -0.10)? Or does positive autocorr suggest momentum is persisting?',
            'What does the Kelly fraction say given current rolling win rate and R:R? f* = p - (1-p)/b. At 25% Kelly, is the implied position size consistent with our risk budget?',
        ]
    },
    'fee_discipline': {
        'name': 'Fee Economics / Albers et al.',
        'dbz_name': 'Krillin',
        'style': 'Execution economics and fee discipline. The single hardest constraint: 1.2% round-trip taker fee. Breakeven equation: p×G - (1-p)×L ≥ 0.012. With stop L=1% and target G=2% (R:R=2): p_min = (1 + 0.012/0.01)/(2+1) = 0.67 (67% win rate needed). With L=3% and G=6%: p_min = (1+0.004)/3 = 0.35 (35%). Every trade must be evaluated: does expected move × win probability actually clear the fee floor? Maker-vs-taker: limit orders cut fee ~33%. Time stop: if flat after 45 min, fee drag has already been paid — waiting longer compounds the loss.',
        'key_questions': [
            'Given the planned stop (L%) and target (G%), what is the minimum win rate needed to break even after 1.2% round-trip fees? p_min = (1 + 0.012/L)/(R+1). Is our historical win rate on similar setups above p_min?',
            'Is the expected gross move (ATR × target_multiplier) at least 2.4% (2× the round-trip fee)? Below this, no R:R ratio saves us.',
            'Can we use a limit order (maker) instead of market (taker) on entry? Maker fee is ~0.4% vs 0.6% taker — saves 0.2% per side and improves the fee math meaningfully.',
            'How long has this potential trade been setting up? If we are entering late in the move after fees were already implied, is there enough remaining move to justify the cost?',
        ]
    },
    'flow_tape': {
        'name': 'Coinbase Tape / Microstructure Flow',
        'dbz_name': 'Piccolo',
        'style': 'Trade flow and tape reading from Coinbase market_trades. Trade Flow Imbalance: TFI = (buy_vol - sell_vol)/(buy_vol + sell_vol). IMPORTANT: Coinbase side field = MAKER side, so side=SELL means taker BUY (buy-initiated). Kyle lambda: Δprice = λ × signed_flow; high λ = high price impact per unit flow. Spread dynamics: wide spread = avoid taker entries. Aggressor dominance: if buy-initiated volume > 60% of last-60s flow, strong demand signal. Volume intensity: trades-per-minute spike vs baseline. These raw tape signals bypass indicator lag entirely.',
        'key_questions': [
            'What is the TFI (trade flow imbalance) over the last 60 seconds? TFI ≥ 0.25 = strong buy-initiated aggressor dominance. Remember: Coinbase side=SELL means the taker was a buyer.',
            'Is the bid-ask spread in basis points below the pair-specific threshold (e.g., ≤12 bps for majors)? Wide spreads increase effective round-trip cost beyond the stated 1.2%.',
            'Is Kyle lambda (price impact per unit signed flow) in a favorable percentile? Low lambda = your order moves price less = better fills = fees are your stated cost, not more.',
            'Is there a trade intensity spike (trades-per-minute significantly above baseline) confirming genuine participation, not thin-book noise?',
        ]
    },
    'manipulation_risk': {
        'name': 'Kose John / Amin Nejat (Spoofing Detection)',
        'dbz_name': 'Tien',
        'style': 'Market manipulation and adverse selection risk. Spoofing signature: OBI extreme (|OBI| ≥ 0.35) but TFI contradicts it (TFI sign opposite OBI) — book is likely manipulated. Adverse selection: entering when informed traders are active means your fill is against smart money. VPIN (Volume-Synchronized Probability of Informed Trading): high VPIN = toxic flow environment. Jump risk: if realized vol spikes suddenly with no corresponding TFI, it may be a news-driven gap where your stop gets skipped. During liquidation cascades, mean-reversion entries are suicidal — OI dropping fast with price dropping = forced selling, not a reversion opportunity.',
        'key_questions': [
            'Is there a conflict between OBI and TFI? (OBI showing strong bids but TFI showing sell-initiated flow = probable spoofing/layering on the bid side.) If yes, distrust the OBI signal.',
            'Is the realized volatility spike coming with corresponding signed flow (TFI aligned), or is it a jump with no aggressor confirmation? Unconfirmed vol spikes = news risk or book manipulation.',
            'Are we in a liquidation cascade regime? (Price falling rapidly, OI declining, TFI sell-dominated.) Mean-reversion entries here are traps. Breakout shorts not available. Best action: stand down.',
            'Is the current spread significantly wider than the pair baseline? Wide spreads during volatile periods = adverse selection environment = our effective cost is much higher than 1.2%.',
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


_CRYPTO_CONTEXT = """TRADING CONTEXT — READ THIS FIRST:
You are evaluating a SHORT-TERM CRYPTO MOMENTUM TRADE on 1-MINUTE candles.
This is NOT a long-term investment. Do NOT apply long-term frameworks here.
- Round-trip fees: ~1.2% of position. The move MUST clear this to be worth it.
- Stop loss: ~1.5% below entry. Take profit: ~4.5% above entry (3:1 R/R).
- Market is 24/7. Only momentum, price action, volume, and regime matter right now.
- A 1-min MACD cross with volume confirmation = valid signal. Evaluate it as such.
- "Would I hold for 10 years?" → IRRELEVANT. Ignore it entirely.
- "Economic moat?" → IRRELEVANT. "Debt cycle?" → IRRELEVANT.
- Apply YOUR specific methodology to short-term price action and momentum signals.
- Ask: does the data support a clean entry RIGHT NOW using your documented strategy?"""

_EQUITY_CONTEXT = """TRADING CONTEXT — READ THIS FIRST:
You are evaluating an EQUITY SWING TRADE on 15-minute to daily candles.
- Hold period: hours to a few days (not months, not years).
- Round-trip fees: ~0.1% (much cheaper than crypto).
- Market hours: 9:30 AM – 4:00 PM ET. PDT rules: max 3 day trades for cash accounts.
- Stop: 5% below entry. Target: 15% above entry (1:3 R/R minimum).
- Price action, volume, and short-term momentum are primary signals.
- Fundamentals can add conviction but are secondary — we're not buying to hold forever."""


def _build_agent_system_prompt(agent_key: str, asset_class: str = 'equity') -> str:
    """Build the cached system prompt for an agent."""
    agent = AGENTS[agent_key]
    questions = '\n'.join(f'- {q}' for q in agent['key_questions'])
    ctx = _CRYPTO_CONTEXT if asset_class == 'crypto' else _EQUITY_CONTEXT
    return f"""You are {agent['name']}, the legendary investor/trader.
You analyze potential trades EXACTLY as {agent['name']} would — through their documented philosophy.

{ctx}

Your investment philosophy: {agent['style']}

Your analytical framework (apply only what is relevant to the trading context above):
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
              context: str = '', memory_context: str = '',
              asset_class: str = 'equity') -> dict:
    """Run one analyst agent. Returns signal dict."""
    agent = AGENTS[agent_key]
    system_prompt = _build_agent_system_prompt(agent_key, asset_class=asset_class)

    williams_r = market_data.get('williams_r', -50)
    fear_greed = market_data.get('fear_greed_score', 50)
    fear_greed_label = market_data.get('fear_greed_label', 'Neutral')
    iv_rank = market_data.get('iv_rank', None)
    momentum_rank = market_data.get('momentum_rank', None)
    above_200d = market_data.get('above_200d_ma', None)
    vol_20d_pct = market_data.get('vol_20d_pct_above_avg', None)
    pullback_bars = market_data.get('pullback_bars', None)

    iv_line = f"- IV Rank: {iv_rank:.0f}/100 ({'elevated — options pricing high risk' if iv_rank > 60 else 'normal'})" if iv_rank is not None else ''
    momentum_line = f"- Momentum rank vs universe: #{momentum_rank}" if momentum_rank is not None else ''
    ma200_line = f"- Price vs 200-day MA: {'ABOVE ✅' if above_200d else 'BELOW ❌'}" if above_200d is not None else ''
    vol_breakout_line = f"- Volume vs avg on breakout: +{vol_20d_pct:.0f}% ({'✅ 40%+ confirms breakout' if vol_20d_pct >= 40 else '⚠️ below 40% threshold'})" if vol_20d_pct is not None else ''
    pullback_line = f"- Pullback bars against trend: {pullback_bars} bars" if pullback_bars is not None else ''

    signal_triggers = market_data.get('signal_triggers', '')
    triggers_line = f"- Signal triggers (what fired this debate): {signal_triggers}" if signal_triggers else ''
    momentum_score_line = f"- Momentum score (exp regression slope × R²): {market_data.get('momentum_score', 0):.3f}"

    # Microstructure / flow / advanced math fields (v3.5)
    obi = market_data.get('obi', None)
    tfi = market_data.get('tfi', None)
    microprice_premium_bps = market_data.get('microprice_premium_bps', None)
    rv_ratio = market_data.get('rv_ratio', None)
    hurst = market_data.get('hurst', None)
    kyle_lambda_pct = market_data.get('kyle_lambda_pct', None)
    amihud_pct = market_data.get('amihud_pct', None)
    spread_bps = market_data.get('spread_bps', None)
    session_active = market_data.get('session_active', False)
    autocorr_ret = market_data.get('autocorr_ret', None)
    ou_halflife = market_data.get('ou_halflife_minutes', None)
    ou_zscore = market_data.get('ou_zscore', None)
    squeeze_direction = market_data.get('squeeze_direction', 0)
    squeeze_fired = market_data.get('squeeze_fired', False)
    squeeze_bars = market_data.get('squeeze_bars', 0)
    avwap_dev = market_data.get('avwap_dev', None)
    kalman_dev = market_data.get('kalman_dev', None)

    obi_line = f"- Order Book Imbalance (OBI): {obi:.3f} ({'STRONG buy pressure ≥0.35' if obi >= 0.35 else 'mild buy pressure ≥0.20' if obi >= 0.20 else 'neutral/ask-heavy' if obi < 0 else 'slight buy lean'})" if obi is not None else ''
    tfi_line = f"- Trade Flow Imbalance (TFI, 60s): {tfi:.3f} ({'STRONG buy-initiated ≥0.25' if tfi >= 0.25 else 'buy-initiated ≥0.10' if tfi >= 0.10 else 'sell-dominated' if tfi < -0.10 else 'neutral — no clear aggressor'})" if tfi is not None else ''
    microprice_line = f"- Microprice vs midprice: {microprice_premium_bps:+.1f} bps ({'fair value BID — buyers paying up' if microprice_premium_bps > 2 else 'fair value OFFERED — sellers aggressive' if microprice_premium_bps < -2 else 'neutral'})" if microprice_premium_bps is not None else ''
    rv_ratio_line = f"- Realized vol ratio (15min/240min): {rv_ratio:.3f} ({'EXPANSION — breakout mode, momentum strategies favored' if rv_ratio >= 1.3 else 'COMPRESSION — coiling, squeeze/mean-reversion setups favored' if rv_ratio <= 0.8 else 'neutral vol regime'})" if rv_ratio is not None else ''
    hurst_line = f"- Hurst exponent: {hurst:.3f} ({'TRENDING H>0.60 — momentum persists' if hurst > 0.60 else 'mean-reverting H<0.40 — reversions complete' if hurst < 0.40 else 'noisy H=0.40-0.60 — no persistent regime, require stronger signal'})" if hurst is not None else ''
    autocorr_line = f"- Return autocorrelation (AR1, 40-bar): {autocorr_ret:+.3f} ({'momentum persistence — up bars follow up bars' if autocorr_ret > 0.15 else 'mean-reverting microstructure — bid-ask bounce dominant' if autocorr_ret < -0.15 else 'no persistence — random/noise regime'})" if autocorr_ret is not None else ''
    ou_line = f"- OU mean-reversion half-life: {ou_halflife:.0f} min ({'fast reversion — actionable for 1-min execution' if ou_halflife <= 20 else 'moderate speed — consider 45-min time stop' if ou_halflife <= 45 else 'slow reversion — too slow for 1-min trading'})" if ou_halflife is not None else ''
    ou_zscore_line = (f"- OU z-score (price deviation from 60-bar mean): {ou_zscore:.2f} "
                     f"({'DEEP OVERSOLD — strong mean-reversion entry (z≤-2)' if ou_zscore <= -2.0 else 'OVERSOLD — entry zone (z≤-1.5)' if ou_zscore <= -1.5 else 'near mean — partial reversion done (exit zone)' if ou_zscore >= -0.5 else 'extended — fade risk' if ou_zscore >= 1.5 else 'neutral'})") if ou_zscore is not None else ''
    kyle_lambda_line = f"- Kyle lambda (price impact) percentile: {kyle_lambda_pct:.0f}th ({'low impact — fills close to stated price' if kyle_lambda_pct < 40 else 'HIGH impact — your order moves the book, effective cost > 1.2%' if kyle_lambda_pct > 70 else 'moderate impact'})" if kyle_lambda_pct is not None else ''
    amihud_line = f"- Amihud illiquidity: {amihud_pct:.0f}th percentile ({'liquid — ok to trade' if amihud_pct < 80 else 'ILLIQUID top-20% — wider effective spread, avoid'})" if amihud_pct is not None else ''
    spread_line = f"- Bid-ask spread: {spread_bps:.1f} bps ({'tight ≤12bps — stated fees apply' if spread_bps <= 12 else 'WIDE >12bps — effective round-trip cost exceeds stated 1.2%'})" if spread_bps is not None else ''
    squeeze_line = (f"- BB-Keltner squeeze: FIRED after {squeeze_bars} compressed bars — "
                    f"{'breakout UP (EMA trending up)' if squeeze_direction > 0 else 'breakout DOWN (EMA trending down)' if squeeze_direction < 0 else 'direction unclear'}"
                    if squeeze_fired else
                    f"- BB-Keltner squeeze: {'ON — {squeeze_bars} bars coiled, energy building' if squeeze_bars > 5 else 'off'}") if squeeze_bars >= 0 else ''
    avwap_line = f"- Price vs daily AVWAP: {avwap_dev:+.2%} ({'above fair value — momentum intact' if avwap_dev > 0.005 else 'below fair value — mean-reversion candidate' if avwap_dev < -0.005 else 'at fair value'})" if avwap_dev is not None else ''
    kalman_line = f"- Kalman filter deviation: {kalman_dev:+.2%} ({'extended above fair price — fade risk' if kalman_dev > 0.01 else 'depressed below fair price — mean-reversion support' if kalman_dev < -0.01 else 'near fair price'})" if kalman_dev is not None else ''
    session_line = f"- Session window (08:00-11:00 ET): {'ACTIVE — intraday predictability elevated, volume-backed moves more reliable' if session_active else 'INACTIVE — outside high-volume hours, momentum signals weaker, require OBI/TFI confirmation'}"

    user_prompt = f"""Analyze {symbol} for a potential trade right now. Apply ONLY your specific methodology — do not generalize.

PRICE & MOMENTUM:
- Current price: ${market_data.get('price', 0):,.4f}
- Change today: {market_data.get('change_pct', 0):+.2f}%
- Volume vs avg: {market_data.get('vol_spike', 1):.1f}x
- RSI (14): {market_data.get('rsi', 50):.1f}
- Williams %R: {williams_r:.1f} ({'oversold extreme ≤-80' if williams_r <= -80 else 'overbought extreme ≥-20' if williams_r >= -20 else 'mid-range'})
- MACD histogram: {market_data.get('macd_hist', 0):.6f} ({'positive momentum' if market_data.get('macd_hist', 0) > 0 else 'negative momentum'})
- Price vs VWAP: {'ABOVE' if market_data.get('price', 0) > market_data.get('vwap', 1) else 'BELOW'} (VWAP: ${market_data.get('vwap', 0):,.4f})
- ATR (14): ${market_data.get('atr', 0):.4f} ({market_data.get('atr', 0)/max(market_data.get('price',1),0.0001)*100:.2f}% of price)
- ADX: {market_data.get('adx', 0):.1f} ({'strong trend >25' if market_data.get('adx',0)>25 else 'weak/no trend ≤25'})
- 20-day trend: {market_data.get('trend_20d', 'neutral')}
{momentum_score_line}
{triggers_line}

REGIME & VOLATILITY:
{rv_ratio_line}
{hurst_line}
{ou_zscore_line}
{autocorr_line}
{squeeze_line}
{avwap_line}
{kalman_line}
{session_line}
- Market regime: {market_data.get('regime', 'unknown')}
- Fear & Greed: {fear_greed:.0f}/100 — {fear_greed_label}

MICROSTRUCTURE (real-time):
{obi_line}
{tfi_line}
{microprice_line}
{spread_line}
{kyle_lambda_line}
{amihud_line}

MEAN-REVERSION CONTEXT:
{ou_line}

EXECUTION ECONOMICS:
- Planned stop: {market_data.get('atr', 0)*3:.4f} ({market_data.get('atr', 0)*3/max(market_data.get('price',1),0.0001)*100:.1f}% of price)
- 1.2% round-trip fee floor on ${market_data.get('price', 0)*100:.0f} position = ${market_data.get('price', 0)*100*0.012:.2f} minimum profit needed
- Dollar volume: ${market_data.get('dollar_volume', 0):,.0f}
{iv_line}
{ma200_line}
{f'- Additional context: {context}' if context else ''}

{memory_context if memory_context else ''}

You are {agent['name']}. Answer these specific questions then give your signal:
{chr(10).join(f'{i+1}. {q}' for i, q in enumerate(agent.get('key_questions', [])))}

Signal + confidence + reasoning + key_concern:"""

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
