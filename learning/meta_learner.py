"""
learning/meta_learner.py — Self-teaching AI meta-intelligence layer.

Fires after every N trade closes (default: 10). Reads the last 100
trade attributions, analyzes what's working vs failing, and produces
structured weight-adjustment recommendations.

Uses Claude to identify patterns the Bayesian math can't see:
  - "Signal X only works when signal Y is ALSO active"
  - "Agent Z is consistently wrong in volatile regime — discount it"
  - "W%R trades are working but squeeze trades are losing — shift weight"

Stores recommendations to meta_recommendations table.
dynamic_weights.py reads and applies them on top of Bayesian weights.

This is "self-taught learning" — the system teaches itself from its
own track record, not just statistical Bayesian blending.
"""
import json
import os
import sys
import sqlite3
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ANTHROPIC_API_KEY, DB_PATH, CLAUDE_MODEL
from learning.signal_performance import (
    get_signal_report, get_attribution_history, SIGNAL_PRIOR_PTS,
)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _init_tables():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS meta_recommendations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_name   TEXT NOT NULL,
                regime        TEXT NOT NULL DEFAULT 'any',
                weight_delta  REAL NOT NULL DEFAULT 0,
                reasoning     TEXT,
                pattern       TEXT,
                confidence    REAL NOT NULL DEFAULT 0.5,
                applied       INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL,
                expires_at    TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS meta_analysis_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                trades_analyzed  INTEGER NOT NULL DEFAULT 0,
                wins             INTEGER NOT NULL DEFAULT 0,
                losses           INTEGER NOT NULL DEFAULT 0,
                win_rate         REAL,
                key_insight      TEXT,
                patterns_found   TEXT,
                recs_count       INTEGER DEFAULT 0,
                created_at       TEXT NOT NULL
            )
        """)


_init_tables()

# ── DB-backed trigger counter (survives restarts) ──────────────────────────

_TRADES_PER_RUN = 10    # run meta-analysis after every N trade closes


def _trades_since_last_meta_run() -> int:
    """Count live trades closed since the last recorded meta-analysis run."""
    try:
        import sqlite3 as _sq
        from config import MARKET_TIMEZONE as _TZ
        _db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'logs', 'trades.db')
        conn = _sq.connect(_db)
        cur = conn.cursor()
        # Get timestamp of last meta run
        cur.execute("SELECT MAX(ts) FROM meta_analysis_log")
        row = cur.fetchone()
        last_run_ts = row[0] if row and row[0] else '2000-01-01'
        # Count trades since then
        cur.execute(
            "SELECT COUNT(*) FROM trade_attribution WHERE source='live' AND ts > ?",
            (last_run_ts,))
        count = (cur.fetchone() or [0])[0]
        conn.close()
        return int(count)
    except Exception:
        return 0


def maybe_run_meta_analysis(force: bool = False) -> Optional[dict]:
    """
    Call after every trade close. Triggers analysis when N new trades have
    closed since the last run. DB-backed — survives process restarts.

    Returns analysis result dict if it ran, None if skipped.
    """
    if not force and _trades_since_last_meta_run() < _TRADES_PER_RUN:
        return None
    return run_meta_analysis()


# ── Core analysis ──────────────────────────────────────────────────────────

def run_meta_analysis(lookback: int = 100) -> Optional[dict]:
    """
    Run the meta-intelligence AI call. Reads recent trade attributions,
    identifies patterns, stores weight-adjustment recommendations.

    Returns the full analysis dict or None on failure.
    """
    if not ANTHROPIC_API_KEY:
        return None

    history = get_attribution_history(limit=lookback)
    if len(history) < 5:
        print(f"[meta_learner] only {len(history)} trades — need 5+ to analyze")
        return None

    wins   = [t for t in history if t.get('won')]
    losses = [t for t in history if not t.get('won')]
    wr     = len(wins) / len(history)

    # Count signal appearances in wins vs losses
    win_signals: dict[str, int]  = {}
    loss_signals: dict[str, int] = {}
    for trades, bucket in [(wins, win_signals), (losses, loss_signals)]:
        for t in trades:
            try:
                sigs = json.loads(t.get('signals_json') or '{}')
                for s, v in sigs.items():
                    if v:
                        bucket[s] = bucket.get(s, 0) + 1
            except Exception:
                pass

    # Compute win-rate per signal (among trades where it fired)
    all_fired: dict[str, int] = {}
    for s, n in win_signals.items():
        all_fired[s] = n + loss_signals.get(s, 0)
    for s, n in loss_signals.items():
        if s not in all_fired:
            all_fired[s] = n

    signal_wr_lines = []
    for s, total in sorted(all_fired.items(), key=lambda x: -x[1]):
        if total < 3:
            continue
        w = win_signals.get(s, 0)
        sig_wr = w / total
        prior  = SIGNAL_PRIOR_PTS.get(s, 5)
        signal_wr_lines.append(
            f"  {s}: fires={total} wins={w} wr={sig_wr:.0%} prior_pts={prior}"
        )

    # Current Bayesian stats
    signal_report = get_signal_report(min_fires=3)
    bayesian_lines = []
    for r in sorted(signal_report, key=lambda x: -(x['fires'] or 0))[:12]:
        wr_str = f"{r['win_rate']*100:.0f}%" if r.get('win_rate') is not None else "?"
        bp     = f"{r['bayesian_pts']:.1f}" if r.get('bayesian_pts') is not None else "?"
        bayesian_lines.append(
            f"  {r['signal_name']}: fires={r['fires']} wr={wr_str} bayesian_pts={bp}"
        )

    # Recent lessons (auto-generated per trade)
    recent_lessons = [
        t.get('lesson', '')[:180]
        for t in history[:15]
        if t.get('lesson')
    ]

    prompt = f"""You are the self-teaching intelligence layer of an autonomous crypto trading bot.
Analyze the recent trade attribution data and produce specific weight-adjustment recommendations.

PERFORMANCE SUMMARY ({len(history)} trades, last {lookback}):
  Wins: {len(wins)} ({wr:.0%})  |  Losses: {len(losses)} ({1-wr:.0%})
  Net P&L: ${sum(t.get('pnl_usd', 0) for t in history):+.2f}

SIGNAL WIN-RATES (this session — from trade attributions):
{chr(10).join(signal_wr_lines) if signal_wr_lines else '  No data yet'}

CURRENT BAYESIAN WEIGHTS (from signal_stats DB):
{chr(10).join(bayesian_lines) if bayesian_lines else '  No data yet'}

RECENT TRADE LESSONS:
{chr(10).join(f'- {l}' for l in recent_lessons[:8]) if recent_lessons else '  None yet'}

Task:
1. Identify signals that are OVERWEIGHTED relative to their actual win-rate.
2. Identify signals that are UNDERWEIGHTED (high win-rate but low bayesian_pts).
3. Note any regime-specific patterns (signal X only works in trending regime, etc.).
4. Produce a key insight sentence (what's the single most important pattern?).

Rules for recommendations:
- Only recommend signals with >= 5 fires in this session.
- weight_delta range: -8.0 to +8.0 (these add to/subtract from current bayesian_pts).
- Only recommend if confidence > 0.65.
- Max 5 recommendations total.
- Valid regime values: any, trending, ranging, volatile.
- Valid signal_name must be an exact key from the signal stats table above.

Respond ONLY with this exact JSON structure (no markdown, no explanation):
{{
  "key_insight": "<one sentence, max 25 words>",
  "patterns_found": "<2-3 sentences summarising what you see>",
  "recommendations": [
    {{
      "signal_name": "<exact name from stats above>",
      "regime": "any|trending|ranging|volatile",
      "weight_delta": <float -8 to +8>,
      "reasoning": "<max 12 words>",
      "confidence": <0.65-1.0>
    }}
  ]
}}"""

    try:
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01',
        }
        body = json.dumps({
            'model': CLAUDE_MODEL,
            'max_tokens': 700,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode()

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=body, headers=headers, method='POST',
        )
        with urllib.request.urlopen(req, timeout=18) as resp:
            data = json.loads(resp.read())

        text = data['content'][0]['text'].strip()
        if text.startswith('```'):
            text = '\n'.join(text.split('\n')[1:])
        if text.endswith('```'):
            text = '\n'.join(text.split('\n')[:-1])
        result = json.loads(text)

    except Exception as e:
        print(f"[meta_learner] AI call failed: {e}")
        return None

    # Persist to DB
    now     = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    recs    = result.get('recommendations', [])

    try:
        with _conn() as c:
            # Log the run
            c.execute("""
                INSERT INTO meta_analysis_log
                    (trades_analyzed, wins, losses, win_rate, key_insight,
                     patterns_found, recs_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (len(history), len(wins), len(losses), wr,
                  result.get('key_insight', ''),
                  result.get('patterns_found', ''),
                  len(recs), now))

            # Replace pending (non-applied) recommendations
            c.execute("DELETE FROM meta_recommendations WHERE applied=0")
            for rec in recs:
                sname = rec.get('signal_name', '').strip()
                if not sname:
                    continue
                delta = float(rec.get('weight_delta', 0))
                delta = max(-8.0, min(8.0, delta))   # safety clamp
                c.execute("""
                    INSERT INTO meta_recommendations
                        (signal_name, regime, weight_delta, reasoning,
                         pattern, confidence, applied, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """, (
                    sname,
                    rec.get('regime', 'any'),
                    delta,
                    rec.get('reasoning', '')[:120],
                    result.get('patterns_found', '')[:300],
                    min(1.0, max(0.0, float(rec.get('confidence', 0.7)))),
                    now, expires,
                ))
    except Exception as e:
        print(f"[meta_learner] DB write error: {e}")
        return None

    insight = result.get('key_insight', '')[:100]
    print(f"[meta_learner] ✅ {len(history)} trades analyzed | insight: {insight}")
    print(f"[meta_learner] {len(recs)} weight adjustments stored (expire 48h)")
    return result


# ── Read helpers ───────────────────────────────────────────────────────────

def get_meta_weight_adjustments(regime: str = 'any') -> dict[str, float]:
    """
    Return {signal_name: delta_pts} from active meta recommendations.
    Applied on top of Bayesian weights inside dynamic_weights.py.

    Only returns recommendations that are:
    - Not yet expired
    - Match the current regime (or are regime='any')
    - Have confidence >= 0.65
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        with _conn() as c:
            rows = c.execute("""
                SELECT signal_name, regime, weight_delta, confidence
                FROM meta_recommendations
                WHERE applied = 0
                  AND expires_at > ?
                  AND (regime = 'any' OR regime = ?)
                  AND confidence >= 0.65
            """, (now, regime)).fetchall()

        adjustments: dict[str, float] = {}
        for r in rows:
            name  = r['signal_name']
            delta = float(r['weight_delta'])
            adjustments[name] = adjustments.get(name, 0.0) + delta
        return adjustments
    except Exception:
        return {}


def get_latest_insight() -> str:
    """One-liner from the most recent meta analysis — for debate context."""
    try:
        with _conn() as c:
            row = c.execute("""
                SELECT key_insight, trades_analyzed, win_rate, created_at
                FROM meta_analysis_log
                ORDER BY created_at DESC LIMIT 1
            """).fetchone()
        if row and row['key_insight']:
            wr_str = f"{row['win_rate']*100:.0f}%" if row['win_rate'] is not None else "?"
            return (
                f"META-LEARNING ({row['trades_analyzed']} trades, {wr_str} WR): "
                f"{row['key_insight']}"
            )
    except Exception:
        pass
    return ""


def get_active_recommendations_brief() -> str:
    """Short table of active meta-recommendations for dashboard/debug."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        with _conn() as c:
            rows = c.execute("""
                SELECT signal_name, regime, weight_delta, reasoning, confidence
                FROM meta_recommendations
                WHERE applied=0 AND expires_at > ?
                ORDER BY ABS(weight_delta) DESC
            """, (now,)).fetchall()
        if not rows:
            return "No active meta-recommendations."
        lines = ["Active meta-recommendations:"]
        for r in rows:
            sign = '+' if r['weight_delta'] >= 0 else ''
            lines.append(
                f"  {r['signal_name']} ({r['regime']}): {sign}{r['weight_delta']:.1f} pts "
                f"— {r['reasoning']} [conf={r['confidence']:.0%}]"
            )
        return '\n'.join(lines)
    except Exception:
        return ""
