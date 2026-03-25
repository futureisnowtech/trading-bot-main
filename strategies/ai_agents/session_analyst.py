"""
strategies/ai_agents/session_analyst.py — AI Strategic Session Analyst.

Fires at three session opens to give the trading system a forward-looking
macro/sentiment bias before any positions are opened that session.

Sessions:
  ASIA   — 8:00 PM ET (20:00) — Tokyo/Singapore opens
  LONDON — 3:00 AM ET (03:00) — Best breakout window; currently underutilized
  NY     — 8:30 AM ET (08:30) — Pre-market + first prints

What it reads:
  - News sentiment from data/news_feed.py
  - Macro snapshot from data/macro_feed.py (DXY, SPY, GLD, VIX, funding rates)
  - Signal leaderboard (top Bayesian-weighted signals)
  - Current session quality / recent brain context

What it outputs (stored in SQLite + memory cache):
  {
    session_bias:                   STRONGLY_BULLISH | BULLISH | NEUTRAL | BEARISH | STRONGLY_BEARISH
    conviction_threshold_multiplier: 0.7 – 1.5 (lower = easier entry, higher = harder)
    signal_weight_overrides:        {signal_name: multiplier}  (bounded 0.3–2.5×)
    strategies_to_favor:            [strategy names to prioritize]
    avoid_flags:                    [signals or strategies to skip]
    session_notes:                  concise analyst briefing for this session
    confidence:                     0.0 – 1.0
  }

The multipliers are applied in job_runner.py conviction scoring:
  effective_threshold = base_threshold × conviction_threshold_multiplier
  -> BULLISH session = lower threshold = more trades
  -> RISK_OFF / high VIX session = higher threshold = fewer trades

Multipliers are bounded [0.3×, 2.5×] to prevent AI from silencing all signals
or opening all the floodgates — the risk manager and debate agents remain authoritative.
"""
import json, os, sys, sqlite3, time
from datetime import datetime, timezone
from typing import Optional
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MARKET_TIMEZONE, DB_PATH, CRYPTO_PAIRS

# ── In-memory cache: keyed by session name, value = context dict ─────────────
_SESSION_CACHE: dict = {}
_SESSION_TTL: int = 4 * 3600   # 4 hours (one full session window)

SESSION_ANALYST_SCHEMA = {
    "type": "object",
    "properties": {
        "session_bias": {
            "type": "string",
            "enum": ["STRONGLY_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "STRONGLY_BEARISH"]
        },
        "conviction_threshold_multiplier": {
            "type": "number", "minimum": 0.7, "maximum": 1.5
        },
        "signal_weight_overrides": {
            "type": "object",
            "description": "signal_name -> multiplier (0.3 to 2.5)"
        },
        "strategies_to_favor": {"type": "array", "items": {"type": "string"}},
        "avoid_flags":         {"type": "array", "items": {"type": "string"}},
        "session_notes":       {"type": "string"},
        "confidence":          {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": [
        "session_bias", "conviction_threshold_multiplier",
        "signal_weight_overrides", "strategies_to_favor", "avoid_flags",
        "session_notes", "confidence"
    ]
}

NEUTRAL_SESSION_CONTEXT = {
    "session_bias": "NEUTRAL",
    "conviction_threshold_multiplier": 1.0,
    "signal_weight_overrides": {},
    "strategies_to_favor": [],
    "avoid_flags": [],
    "session_notes": "No session analysis available — using default conviction thresholds.",
    "confidence": 0.0,
}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_table() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_contexts (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name   TEXT    NOT NULL,
                session_bias   TEXT    NOT NULL,
                cv_multiplier  REAL    NOT NULL DEFAULT 1.0,
                weight_overrides TEXT  NOT NULL DEFAULT '{}',
                strategies_favor TEXT  NOT NULL DEFAULT '[]',
                avoid_flags    TEXT    NOT NULL DEFAULT '[]',
                session_notes  TEXT    NOT NULL DEFAULT '',
                confidence     REAL    NOT NULL DEFAULT 0.0,
                created_ts     TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sc_session ON session_contexts(session_name, created_ts)"
        )
        conn.commit()


def _store_session_context(session_name: str, ctx: dict) -> None:
    _init_table()
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO session_contexts
                (session_name, session_bias, cv_multiplier, weight_overrides,
                 strategies_favor, avoid_flags, session_notes, confidence)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            session_name,
            ctx.get("session_bias", "NEUTRAL"),
            ctx.get("conviction_threshold_multiplier", 1.0),
            json.dumps(ctx.get("signal_weight_overrides", {})),
            json.dumps(ctx.get("strategies_to_favor", [])),
            json.dumps(ctx.get("avoid_flags", [])),
            ctx.get("session_notes", ""),
            ctx.get("confidence", 0.0),
        ))
        conn.commit()


def get_current_session_context(session_name: Optional[str] = None) -> dict:
    """
    Return the most recent session context within the last 4 hours.
    Falls back to neutral defaults if no analysis has run yet.
    """
    # Check memory cache first
    cache_k = session_name or "latest"
    if cache_k in _SESSION_CACHE:
        entry = _SESSION_CACHE[cache_k]
        if time.time() - entry["ts"] < _SESSION_TTL:
            return entry["ctx"]

    # Try DB
    try:
        _init_table()
        with _get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM session_contexts
                WHERE (? IS NULL OR session_name = ?)
                  AND created_ts >= datetime('now', '-4 hours')
                ORDER BY created_ts DESC LIMIT 1
            """, (session_name, session_name)).fetchone()

        if row:
            ctx = {
                "session_name":                  row["session_name"],
                "session_bias":                  row["session_bias"],
                "conviction_threshold_multiplier": row["cv_multiplier"],
                "signal_weight_overrides":       json.loads(row["weight_overrides"] or "{}"),
                "strategies_to_favor":           json.loads(row["strategies_favor"] or "[]"),
                "avoid_flags":                   json.loads(row["avoid_flags"] or "[]"),
                "session_notes":                 row["session_notes"],
                "confidence":                    row["confidence"],
            }
            _SESSION_CACHE[cache_k] = {"ctx": ctx, "ts": time.time()}
            return ctx
    except Exception:
        pass

    return {**NEUTRAL_SESSION_CONTEXT}


def run_session_analysis(session_name: Optional[str] = None,
                         force: bool = False) -> dict:
    """
    Run the AI Session Analyst and return the session context.

    Args:
        session_name: Override session name (auto-detected from time if None)
        force: Force re-run even if a recent context exists
    """
    if not ANTHROPIC_API_KEY:
        return {**NEUTRAL_SESSION_CONTEXT, "session_notes": "No API key — using default thresholds."}

    # Auto-detect session name from time
    if not session_name:
        from data.market_context import get_current_session
        session_info = get_current_session()
        session_name = session_info["session"]

    # Don't re-run if recent context exists (unless forced)
    if not force:
        existing = get_current_session_context(session_name)
        if existing.get("confidence", 0) > 0:
            return existing

    print(f"\n[session_analyst] Running {session_name} analysis...")

    # ── Gather all context ────────────────────────────────────────────────────
    news_summary = _get_news_summary()
    macro_summary = _get_macro_summary()
    signal_leaderboard = _get_signal_leaderboard()
    session_quality_notes = _get_session_quality(session_name)

    # ── Build prompt ─────────────────────────────────────────────────────────
    system_prompt = """You are an elite crypto/equity trading strategist running a session-open brief.
Your job is to synthesize macro, news, and signal intelligence into a trading posture for the next 4 hours.

You are NOT predicting the market. You are setting CONTEXT that adjusts:
1. How much conviction is required to enter a trade (threshold multiplier 0.7–1.5)
2. Which signals deserve MORE weight right now (e.g. momentum in a RISK_ON session)
3. Which strategies or signals to AVOID (e.g. momentum breakouts in RISK_OFF macro)

BOUNDS — hard rules you cannot violate:
- conviction_threshold_multiplier: min 0.7, max 1.5
- signal_weight_overrides values: min 0.3, max 2.5
- If you have low confidence in your assessment, set multiplier = 1.0 (neutral — don't guess)
- "STRONGLY_BULLISH" or "STRONGLY_BEARISH" requires VIX extreme, major news, AND macro alignment
- Default: NEUTRAL with multiplier 1.0

The risk manager, debate agents, and Goku still have final say on every trade.
You are providing CONTEXT, not PERMISSION."""

    user_prompt = f"""Session: {session_name} | {datetime.now(pytz.timezone(MARKET_TIMEZONE)).strftime('%Y-%m-%d %H:%M ET')}

{session_quality_notes}

MACRO SNAPSHOT:
{macro_summary}

NEWS SENTIMENT:
{news_summary}

TOP SIGNALS BY BAYESIAN WEIGHT:
{signal_leaderboard}

Based on this, provide your session analysis:
- session_bias: overall directional lean for this session
- conviction_threshold_multiplier: 1.0 = unchanged. < 1.0 = lower the bar (RISK_ON, high news confidence). > 1.0 = raise the bar (RISK_OFF, choppy, high VIX).
- signal_weight_overrides: only override signals you have specific reason to weight differently. Empty dict if no strong view.
- strategies_to_favor: empty list if no specific preference
- avoid_flags: any signals or strategy patterns to skip this session
- session_notes: 2-3 sentence briefing that will be injected into every debate agent prompt this session
- confidence: how confident you are in this analysis (low confidence → set multiplier = 1.0)"""

    # ── Call Claude ───────────────────────────────────────────────────────────
    try:
        from strategies.ai_agents.analyst_agents import call_claude_structured
        result = call_claude_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=800,
            use_cache=True,
            call_type='session_analyst',
            schema=SESSION_ANALYST_SCHEMA,
        )

        if not result or 'session_bias' not in result:
            raise ValueError(f"Bad response: {result}")

        # Clamp multiplier to safe range
        result['conviction_threshold_multiplier'] = max(
            0.7, min(1.5, float(result.get('conviction_threshold_multiplier', 1.0)))
        )

        # Clamp signal weight overrides
        overrides = result.get('signal_weight_overrides', {})
        result['signal_weight_overrides'] = {
            k: max(0.3, min(2.5, float(v))) for k, v in overrides.items()
        }

        result['session_name'] = session_name
        result['generated_at'] = datetime.now(timezone.utc).isoformat()

        # Store to DB and cache
        _store_session_context(session_name, result)
        _SESSION_CACHE[session_name] = {"ctx": result, "ts": time.time()}
        _SESSION_CACHE["latest"]     = {"ctx": result, "ts": time.time()}

        bias = result['session_bias']
        mult = result['conviction_threshold_multiplier']
        notes = result.get('session_notes', '')[:80]
        print(f"[session_analyst] {session_name}: {bias} | threshold×{mult:.2f} | {notes}")

        return result

    except Exception as e:
        print(f"[session_analyst] Error: {e}")
        return {**NEUTRAL_SESSION_CONTEXT, "session_notes": f"Analysis failed: {e}"}


# ── Data gathering helpers ────────────────────────────────────────────────────

def _get_news_summary() -> str:
    try:
        from data.news_feed import get_general_market_news
        news = get_general_market_news()
        lines = [
            f"Sentiment score: {news['sentiment_score']:+.2f} | Risk: {news['news_risk']} | Source: {news['source']}",
        ]
        if news.get('warning_flags'):
            lines.append(f"Risk flags: {', '.join(news['warning_flags'])}")
        if news.get('headlines'):
            lines.append("Top headlines:")
            for h in news['headlines'][:5]:
                lines.append(f"  - {h}")
        return '\n'.join(lines)
    except Exception as e:
        return f"News unavailable: {e}"


def _get_macro_summary() -> str:
    try:
        from data.macro_feed import get_macro_snapshot
        macro = get_macro_snapshot(symbols_of_interest=CRYPTO_PAIRS[:4])
        lines = [
            f"Risk regime: {macro['risk_regime']} (score={macro.get('macro_score', 0):+d})",
            f"VIX: {macro.get('vix', 'N/A')} ({macro.get('vix_regime', '?')}) | SPY: {macro.get('spy_change', 0):+.2f}% | DXY: {macro.get('dxy_change', 0):+.2f}%",
            f"Gold: {macro.get('gold_change', 0):+.2f}% | BTC: {macro.get('btc_change', 0):+.2f}%",
        ]
        if macro.get('macro_notes'):
            lines.append("Notes: " + " | ".join(macro['macro_notes'][:4]))
        # Funding rates
        fr_lines = []
        for sym, fr in list(macro.get('funding_rates', {}).items())[:4]:
            if fr.get('rate_pct') is not None:
                fr_lines.append(f"{sym}={fr['rate_pct']:.4f}%/8h({fr['signal']})")
        if fr_lines:
            lines.append("Funding: " + " | ".join(fr_lines))
        return '\n'.join(lines)
    except Exception as e:
        return f"Macro unavailable: {e}"


def _get_signal_leaderboard() -> str:
    try:
        from learning.signal_performance import get_signal_report
        report = get_signal_report(min_fires=5)
        if not report:
            return "No signal data yet (system needs more trades to populate)"
        lines = [f"Top signals by Bayesian weight (≥5 fires):"]
        top = sorted(report, key=lambda x: x.get('bayesian_pts') or 0, reverse=True)[:10]
        for s in top:
            wr  = f"{s['win_rate']*100:.0f}%" if s['win_rate'] else "N/A"
            bp  = f"{s['bayesian_pts']:.1f}" if s['bayesian_pts'] else "N/A"
            lines.append(f"  {s['signal_name']:<28} fires={s['fires']} wr={wr} bayes={bp}")
        return '\n'.join(lines)
    except Exception as e:
        return f"Signal leaderboard unavailable: {e}"


def _get_session_quality(session_name: str) -> str:
    try:
        from data.market_context import get_current_session
        info = get_current_session()
        return (
            f"Session quality: {info['session_quality']} | Hour ET: {info['hour_et']:.1f}\n"
            f"Session notes: {info['notes']}"
        )
    except Exception:
        return f"Session: {session_name}"


def format_session_context_for_debate(ctx: Optional[dict] = None) -> str:
    """
    Return a concise session briefing string for injection into debate prompts.
    Called once per scan cycle and passed as `context` to run_debate().
    """
    ctx = ctx or get_current_session_context()
    if not ctx or ctx.get("confidence", 0) == 0:
        return ""

    bias  = ctx.get("session_bias", "NEUTRAL")
    mult  = ctx.get("conviction_threshold_multiplier", 1.0)
    notes = ctx.get("session_notes", "")
    avoid = ctx.get("avoid_flags", [])

    lines = [f"[SESSION ANALYST] Bias: {bias} | Threshold×{mult:.2f}"]
    if notes:
        lines.append(f"  Briefing: {notes}")
    if avoid:
        lines.append(f"  Avoid flags: {', '.join(avoid[:3])}")
    return '\n'.join(lines)
