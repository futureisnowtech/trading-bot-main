"""
learning/ai_prescreener.py — AI raw-signal pre-screener (AI-first gate).

Runs BEFORE the full 5-agent debate. Analyzes compressed indicator
summaries for ALL candidate symbols in ONE batch Claude Haiku call.

This is "AI as early as possible" — instead of math gates alone deciding
who gets a debate, AI now cross-compares all symbols and filters noise
before any expensive debate API calls are made.

Key insight: when BTC, ETH, and SOL all hit Williams %R = -80 at the
same time, that's market-wide noise. An AI can spot that. Math gates
can't — they'd approve all three for full debate.

Model:  claude-haiku-4-5-20251001 (fast, ~$0.001 for 8 symbols)
Tokens: ~600 in / ~200 out per batch call
Fails open: if API errors, all symbols pass with score=5.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ANTHROPIC_API_KEY

_HAIKU_MODEL = 'claude-haiku-4-5-20251001'
PRESCORE_THRESHOLD = 4          # symbols scoring below this skip the debate
_CACHE: dict = {}               # {symbol: (score, reason, ts)}
_CACHE_TTL = 290                # refresh just before the 5-min scan cycle


def _market_data_summary(symbol: str, md: dict) -> str:
    """Compact indicator snapshot for the prescreener prompt."""
    regime   = md.get('regime', 'unknown')
    rsi      = md.get('rsi', 50)
    adx      = md.get('adx', 25)
    macd     = '↑' if md.get('macd_hist', 0) > 0 else '↓'
    wr       = md.get('williams_r', -50)
    vol      = md.get('vol_spike', 1.0)
    obi      = md.get('obi')
    tfi      = md.get('tfi')
    lrsi     = md.get('lrsi', 0.5)
    chop     = md.get('chop', 50)
    sqz      = md.get('squeeze_fired', False)
    rv       = md.get('rv_ratio', 1.0)
    kal_dev  = md.get('kalman_dev', 0.0)
    conv     = md.get('conviction_score', 0)
    sigs     = md.get('active_signals', [])

    obi_str = f" OBI={obi:+.2f}" if obi is not None else ""
    tfi_str = f" TFI={tfi:+.2f}" if tfi is not None else ""

    return (
        f"{symbol}: regime={regime} RSI={rsi:.0f} ADX={adx:.0f} MACD={macd} "
        f"W%R={wr:.0f} vol={vol:.1f}x LRSI={lrsi:.2f} CHOP={chop:.0f} "
        f"RV={rv:.2f} Kal={kal_dev:.2f}% sqz={'Y' if sqz else 'N'}"
        f"{obi_str}{tfi_str} "
        f"conviction={conv} signals=[{','.join(sigs[:4])}]"
    )


def prescreener_batch(
    candidates: list[tuple[str, dict]],
) -> dict[str, dict]:
    """
    Screen all candidate symbols in ONE Claude Haiku call.

    Args:
        candidates: [(symbol, market_data_dict), ...]
                    Only pass symbols that already cleared the fast gates
                    (conviction > 0, ATR floor, macro block, cooldown).

    Returns:
        {symbol: {'score': int 0-10, 'reason': str, 'should_analyze': bool}}
    """
    if not ANTHROPIC_API_KEY or not candidates:
        return {sym: {'score': 5, 'reason': 'no_api_key', 'should_analyze': True}
                for sym, _ in candidates}

    now = time.time()
    all_syms = [sym for sym, _ in candidates]

    # Check cache — if all symbols are fresh, return cached scores
    cached_all = {sym: _CACHE[sym] for sym in all_syms if sym in _CACHE}
    if len(cached_all) == len(all_syms) and all(now - ts < _CACHE_TTL for _, _, ts in cached_all.values()):
        return {
            sym: {'score': sc, 'reason': r, 'should_analyze': sc >= PRESCORE_THRESHOLD}
            for sym, (sc, r, _) in cached_all.items()
        }

    lines = [_market_data_summary(sym, md) for sym, md in candidates]
    data_block = '\n'.join(lines)

    prompt = (
        "You are a rapid pre-screening filter for a crypto trading bot.\n"
        "Below are compressed indicator snapshots for candidate symbols.\n"
        "Your job: rate each symbol's SHORT-TERM BUY OPPORTUNITY (next 1-5 candles) 0-10.\n\n"
        "Score guide:\n"
        "8-10 — Clear setup: oversold + vol spike + supportive regime, genuine edge\n"
        "5-7  — Interesting: mixed signals, developing setup, deserves full analysis\n"
        "2-4  — Weak: one minor signal, no confluence, likely noise\n"
        "0-1  — Skip: trending down hard, or same signal on ALL symbols (market noise)\n\n"
        "CROSS-SYMBOL CHECK: if 3+ symbols show identical signals simultaneously, "
        "that is market-wide noise — score them all 0-2 unless one has clear differentiation "
        "(unique volume spike, stronger oversold, confirmed microstructure flow).\n\n"
        f"SYMBOLS TO SCORE:\n{data_block}\n\n"
        "Respond ONLY with valid JSON:\n"
        '{"scores": {'
        + ', '.join(f'"{s}": {{"score": <int>, "reason": "<12 words max>"}}' for s in all_syms)
        + '}}'
    )

    try:
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01',
        }
        body = json.dumps({
            'model': _HAIKU_MODEL,
            'max_tokens': 350,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode()

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=body, headers=headers, method='POST',
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        text = data['content'][0]['text'].strip()
        # Strip any markdown code fences
        if text.startswith('```'):
            text = '\n'.join(text.split('\n')[1:])
        if text.endswith('```'):
            text = '\n'.join(text.split('\n')[:-1])
        parsed = json.loads(text)
        scores_raw = parsed.get('scores', {})

        results = {}
        for sym in all_syms:
            s = scores_raw.get(sym, {})
            score = min(10, max(0, int(s.get('score', 5))))
            reason = str(s.get('reason', ''))[:80]
            results[sym] = {
                'score': score,
                'reason': reason,
                'should_analyze': score >= PRESCORE_THRESHOLD,
            }
            _CACHE[sym] = (score, reason, now)

        passed = sum(1 for r in results.values() if r['should_analyze'])
        print(f"[prescreener] {len(all_syms)} symbols scored → {passed} passed "
              f"(threshold={PRESCORE_THRESHOLD})")
        return results

    except Exception as e:
        print(f"[prescreener] batch call failed ({e}) — defaulting all to pass")
        return {sym: {'score': 5, 'reason': 'api_error', 'should_analyze': True}
                for sym in all_syms}


def get_prescreener_context(symbol: str, prescore: dict) -> str:
    """Return a one-line string for injection into the debate context."""
    if not prescore:
        return ""
    score = prescore.get('score', 5)
    reason = prescore.get('reason', '')
    bar = '█' * score + '░' * (10 - score)
    return f"AI PRE-SCREEN: {score}/10 [{bar}] — {reason}"
