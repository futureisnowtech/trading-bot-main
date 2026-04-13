"""
learning/signal_performance.py — Signal × regime attribution engine.

Maintains a running record of how every signal performs in every regime.
Powers the Bayesian conviction weight system that replaces hardcoded points.

Tables (in trades.db):
  signal_stats     — per-signal × regime win rates + Bayesian weights
  trade_attribution — structured record of every closed trade + which signals fired
  agent_stats      — per-agent vote accuracy

Bayesian blend logic:
  We treat the hardcoded conviction points as a prior.
  As evidence accumulates, the posterior shifts toward observed win rates.
  Prior confidence = 20 "phantom trades" worth of belief in the original design.
  At N=20 live fires, weight is 50/50 prior/observed.
  At N=100 live fires, weight is ~17% prior / ~83% observed.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

# ── Hardcoded priors (original conviction points, normalised 0-1) ─────────────
# These ARE the v4.3 hardcoded values. They serve as the Bayesian prior.
# Formula: prior_p = points / MAX_SINGLE_SIGNAL_POINTS (12 pts = 1.0)
MAX_PRIOR_PTS = 12.0
SIGNAL_PRIORS: dict[str, float] = {
    # Tier 1
    "macd_consensus": 25 / MAX_PRIOR_PTS,  # capped at 1.0 → treated as strong prior
    "williams_r": 20 / MAX_PRIOR_PTS,
    "momentum_volume": 15 / MAX_PRIOR_PTS,
    # Tier 2a
    "squeeze_fired": 20 / MAX_PRIOR_PTS,
    "rv_expansion": 15 / MAX_PRIOR_PTS,
    "kalman_deviation": 10 / MAX_PRIOR_PTS,
    "avwap_deviation": 10 / MAX_PRIOR_PTS,
    "ou_halflife": 5 / MAX_PRIOR_PTS,
    "kyle_lambda": 5 / MAX_PRIOR_PTS,
    # Tier 2b
    "supertrend_bullish": 12 / MAX_PRIOR_PTS,
    "wavetrend_cross": 12 / MAX_PRIOR_PTS,
    "ichimoku_bullish": 8 / MAX_PRIOR_PTS,
    "fisher_cross_up": 8 / MAX_PRIOR_PTS,
    "lrsi_oversold": 8 / MAX_PRIOR_PTS,
    "wae_bullish_exploding": 10 / MAX_PRIOR_PTS,
    "wae_bullish": 5 / MAX_PRIOR_PTS,
    "chop_trending": 5 / MAX_PRIOR_PTS,
    "lrsi_mild_oversold": 4 / MAX_PRIOR_PTS,
    # Tier 3
    "tradingview_signal": 20 / MAX_PRIOR_PTS,
    # Mean reversion strategy signals
    "bb_proximity": 12 / MAX_PRIOR_PTS,
    "autocorr_negative": 8 / MAX_PRIOR_PTS,
    "mean_rev_kalman": 12 / MAX_PRIOR_PTS,
    # Futures / ORB signals
    "orb_breakout_long": 18 / MAX_PRIOR_PTS,
    "orb_breakout_short": 18 / MAX_PRIOR_PTS,
    "htf_bullish_bias": 10 / MAX_PRIOR_PTS,
    "htf_bearish_bias": 10 / MAX_PRIOR_PTS,
    "futures_adx_trend": 8 / MAX_PRIOR_PTS,
    # Perpetual futures signals
    "perp_long_breakout": 15 / MAX_PRIOR_PTS,
    "perp_short_breakout": 15 / MAX_PRIOR_PTS,
    "rsi_bullish_momentum": 8 / MAX_PRIOR_PTS,
    "rsi_bearish_momentum": 8 / MAX_PRIOR_PTS,
    "funding_rate_favorable": 7 / MAX_PRIOR_PTS,
    # Equity momentum signals
    "equity_macd_positive": 12 / MAX_PRIOR_PTS,
    "equity_kst_cross": 10 / MAX_PRIOR_PTS,
    "equity_vwap_above": 8 / MAX_PRIOR_PTS,
    "equity_vol_spike": 8 / MAX_PRIOR_PTS,
    "equity_rsi_range": 6 / MAX_PRIOR_PTS,
}
# Hardcoded conviction POINTS that dynamic_weights will output (same scale as job_runner):
SIGNAL_PRIOR_PTS: dict[str, int] = {
    "macd_consensus": 25,
    "williams_r": 20,
    "momentum_volume": 15,
    "squeeze_fired": 20,
    "rv_expansion": 15,
    "kalman_deviation": 10,
    "avwap_deviation": 10,
    "ou_halflife": 5,
    "kyle_lambda": 5,
    "supertrend_bullish": 12,
    "wavetrend_cross": 12,
    "ichimoku_bullish": 8,
    "fisher_cross_up": 8,
    "lrsi_oversold": 8,
    "wae_bullish_exploding": 10,
    "wae_bullish": 5,
    "chop_trending": 5,
    "lrsi_mild_oversold": 4,
    "tradingview_signal": 20,
    # Mean reversion strategy signals
    "bb_proximity": 12,
    "autocorr_negative": 8,
    "mean_rev_kalman": 12,
    # Futures / ORB signals
    "orb_breakout_long": 18,
    "orb_breakout_short": 18,
    "htf_bullish_bias": 10,
    "htf_bearish_bias": 10,
    "futures_adx_trend": 8,
    # Perpetual futures signals
    "perp_long_breakout": 15,
    "perp_short_breakout": 15,
    "rsi_bullish_momentum": 8,
    "rsi_bearish_momentum": 8,
    "funding_rate_favorable": 7,
    # Equity momentum signals
    "equity_macd_positive": 12,
    "equity_kst_cross": 10,
    "equity_vwap_above": 8,
    "equity_vol_spike": 8,
    "equity_rsi_range": 6,
    # ── v10 Tier 1 setup names (added v13.4 — replaces v9 taxonomy) ──────────
    # Priors calibrated to v10 paper period 68.8% WR baseline.
    # These are the actual signal names fired by signal_engine.detect_primary_setup().
    "wt_reversal": 12,
    "squeeze_breakout": 15,
    "wae_explosion": 12,
    "tv_confirmed_long": 15,
    "tv_confirmed_short": 15,
    "supertrend_cross_long": 12,
    "supertrend_cross_short": 12,
    "kst_cross_long": 10,
    "kst_cross_short": 10,
    "ichimoku_cloud_breakout_long": 12,
    "ichimoku_cloud_breakout_short": 12,
    "ranging_mr_long": 8,
    "ranging_mr_short": 8,
    "wt_overbought_reversal": 12,
    "squeeze_breakout_short": 15,
    "wae_explosion_short": 12,
}
# Bayesian prior weight — how many "phantom trades" of confidence in the prior
PRIOR_N = 20
# Min fires before we start shifting away from prior
MIN_FIRES_TO_LEARN = 10


# ── DB helpers ────────────────────────────────────────────────────────────────


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_learning_tables():
    """Create all learning tables if they don't exist. Safe to call repeatedly."""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS trade_attribution (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_ref     TEXT,
                symbol        TEXT NOT NULL,
                strategy      TEXT NOT NULL,
                regime        TEXT NOT NULL DEFAULT 'unknown',
                source        TEXT NOT NULL DEFAULT 'live',
                entry_ts      TEXT,
                exit_ts       TEXT,
                entry_price   REAL,
                exit_price    REAL,
                pnl_usd       REAL NOT NULL DEFAULT 0,
                pnl_pct       REAL NOT NULL DEFAULT 0,
                fee_usd       REAL NOT NULL DEFAULT 0,
                won           INTEGER NOT NULL DEFAULT 0,
                signals_json  TEXT,
                conviction    REAL DEFAULT 0,
                exit_reason   TEXT,
                hold_minutes  REAL DEFAULT 0,
                paper         INTEGER DEFAULT 1,
                lesson        TEXT,
                created_at    TEXT,
                mae_pct       REAL DEFAULT 0,
                mfe_pct       REAL DEFAULT 0,
                exit_type     TEXT DEFAULT 'unknown',
                is_fee_trap   INTEGER DEFAULT 0,
                ml_p_win      REAL DEFAULT 0,
                super_score   REAL DEFAULT 0,
                composite_score REAL DEFAULT 0
            )
        """)
        # Add composite_score column if it doesn't exist (safe to call on existing DBs)
        try:
            c.execute(
                "ALTER TABLE trade_attribution ADD COLUMN composite_score REAL DEFAULT 0"
            )
        except Exception:
            pass  # column already exists
        c.execute("""
            CREATE TABLE IF NOT EXISTS signal_stats (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_name   TEXT NOT NULL,
                regime        TEXT NOT NULL DEFAULT 'any',
                source        TEXT NOT NULL DEFAULT 'combined',
                fires         INTEGER NOT NULL DEFAULT 0,
                wins          INTEGER NOT NULL DEFAULT 0,
                losses        INTEGER NOT NULL DEFAULT 0,
                total_pnl     REAL NOT NULL DEFAULT 0,
                avg_pnl       REAL NOT NULL DEFAULT 0,
                win_rate      REAL,
                bayesian_pts  REAL,
                prior_pts     REAL,
                last_updated  TEXT,
                UNIQUE(signal_name, regime, source)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS agent_stats (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name    TEXT NOT NULL,
                regime        TEXT NOT NULL DEFAULT 'any',
                votes_buy     INTEGER DEFAULT 0,
                votes_hold    INTEGER DEFAULT 0,
                votes_sell    INTEGER DEFAULT 0,
                correct_buy   INTEGER DEFAULT 0,
                incorrect_buy INTEGER DEFAULT 0,
                total_assessed INTEGER DEFAULT 0,
                accuracy      REAL,
                last_updated  TEXT,
                UNIQUE(agent_name, regime)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name   TEXT NOT NULL,
                variant         TEXT,
                symbol          TEXT NOT NULL,
                timeframe       TEXT,
                period_start    TEXT,
                period_end      TEXT,
                param_hash      TEXT,
                params_json     TEXT,
                total_trades    INTEGER DEFAULT 0,
                win_rate        REAL,
                total_pnl       REAL,
                sharpe          REAL,
                max_drawdown    REAL,
                avg_pnl         REAL,
                profit_factor   REAL,
                passed          INTEGER DEFAULT 0,
                archived_at     TEXT,
                notes           TEXT
            )
        """)


init_learning_tables()


# ── Core attribution write ────────────────────────────────────────────────────


def record_trade_attribution(
    symbol: str,
    strategy: str,
    regime: str,
    signals: dict,  # {signal_name: bool} — which signals were active at entry
    won: bool,
    pnl_usd: float,
    pnl_pct: float,
    fee_usd: float = 0,
    conviction: float = 0,
    entry_price: float = 0,
    exit_price: float = 0,
    entry_ts: str = "",
    exit_ts: str = "",
    exit_reason: str = "",
    hold_minutes: float = 0,
    source: str = "live",
    paper: bool = True,
    trade_ref: str = "",
    lesson: str = "",
    mae_pct: float = 0,
    mfe_pct: float = 0,
    exit_type: str = "unknown",
    ml_p_win: float = 0,
    super_score: float = 0,
    composite_score: float = 0,
) -> int:
    """
    Record the full attribution for one closed trade.
    Updates signal_stats for every active signal.
    Returns the inserted attribution ID.
    """
    now = datetime.now(timezone.utc).isoformat()
    signals_json = json.dumps({k: bool(v) for k, v in signals.items()})
    is_fee_trap = int(fee_usd > 0 and abs(pnl_usd) > 0 and fee_usd > abs(pnl_usd) * 0.5)

    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO trade_attribution
                (trade_ref, symbol, strategy, regime, source,
                 entry_ts, exit_ts, entry_price, exit_price,
                 pnl_usd, pnl_pct, fee_usd, won,
                 signals_json, conviction, exit_reason,
                 hold_minutes, paper, lesson, created_at,
                 mae_pct, mfe_pct, exit_type, is_fee_trap, ml_p_win, super_score,
                 composite_score)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                trade_ref,
                symbol,
                strategy,
                regime,
                source,
                entry_ts,
                exit_ts,
                entry_price,
                exit_price,
                pnl_usd,
                pnl_pct,
                fee_usd,
                int(won),
                signals_json,
                conviction,
                exit_reason,
                hold_minutes,
                int(paper),
                lesson,
                now,
                mae_pct,
                mfe_pct,
                exit_type,
                is_fee_trap,
                ml_p_win,
                float(super_score or 0),
                float(composite_score or 0),
            ),
        )
        attr_id = cur.lastrowid

    # Update signal_stats for every active signal
    active_signals = [k for k, v in signals.items() if v]
    for sig in active_signals:
        _update_signal_stat(sig, regime, won, pnl_usd, source)
        _update_signal_stat(sig, "any", won, pnl_usd, source)  # regime-agnostic row

    return attr_id


def _update_signal_stat(
    signal_name: str, regime: str, won: bool, pnl_usd: float, source: str
):
    """Upsert one row in signal_stats and recompute Bayesian weight."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        # Upsert the stats row
        c.execute(
            """
            INSERT INTO signal_stats (signal_name, regime, source, fires, wins, losses, total_pnl, last_updated)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(signal_name, regime, source) DO UPDATE SET
                fires       = fires + 1,
                wins        = wins + ?,
                losses      = losses + ?,
                total_pnl   = total_pnl + ?,
                last_updated = ?
        """,
            (
                signal_name,
                regime,
                source,
                int(won),
                int(not won),
                pnl_usd,
                now,  # INSERT values (7 params for 7 ?s)
                int(won),
                int(not won),
                pnl_usd,
                now,  # UPDATE deltas (4 params)
            ),
        )

        # Recompute derived fields
        row = c.execute(
            """
            SELECT fires, wins, total_pnl FROM signal_stats
            WHERE signal_name=? AND regime=? AND source=?
        """,
            (signal_name, regime, source),
        ).fetchone()

        if row and row["fires"] > 0:
            obs_win_rate = row["wins"] / row["fires"]
            avg_pnl = row["total_pnl"] / row["fires"]

            # Bayesian blend
            prior_p = SIGNAL_PRIORS.get(signal_name, 0.5)
            prior_pts = SIGNAL_PRIOR_PTS.get(signal_name, 5)
            n = row["fires"]

            if n >= MIN_FIRES_TO_LEARN:
                # Clamp prior_p to [0, 1] — SIGNAL_PRIORS stores pts/12, not win rates,
                # so values > 1.0 are possible and would break the Bayesian formula
                prior_p_wr = min(max(prior_p, 0.01), 1.0)
                # posterior win rate = weighted average of prior and observed
                posterior_wr = (prior_p_wr * PRIOR_N + obs_win_rate * n) / (PRIOR_N + n)
                # Scale to conviction points: 0.5 wr → prior_pts, 1.0 wr → 2×prior_pts, 0.0 → 0
                bayesian_pts = prior_pts * (posterior_wr / prior_p_wr)
                bayesian_pts = max(0, min(bayesian_pts, prior_pts * 2.5))  # cap at 2.5×
            else:
                bayesian_pts = float(prior_pts)  # not enough data — use prior

            c.execute(
                """
                UPDATE signal_stats
                SET win_rate=?, avg_pnl=?, bayesian_pts=?, prior_pts=?
                WHERE signal_name=? AND regime=? AND source=?
            """,
                (
                    obs_win_rate,
                    avg_pnl,
                    bayesian_pts,
                    float(prior_pts),
                    signal_name,
                    regime,
                    source,
                ),
            )


# ── Agent accuracy ────────────────────────────────────────────────────────────


def record_agent_votes(
    agent_votes: dict,  # {agent_name: 'BUY'|'HOLD'|'SELL'}
    regime: str,
    won: bool,
):
    """Update agent accuracy stats after a trade closes."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        for agent, vote in agent_votes.items():
            vote = str(vote).upper()
            correct_buy = int(vote == "BUY" and won)
            incorrect_buy = int(vote == "BUY" and not won)
            c.execute(
                """
                INSERT INTO agent_stats
                    (agent_name, regime, votes_buy, votes_hold, votes_sell,
                     correct_buy, incorrect_buy, total_assessed, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(agent_name, regime) DO UPDATE SET
                    votes_buy     = votes_buy     + ?,
                    votes_hold    = votes_hold    + ?,
                    votes_sell    = votes_sell    + ?,
                    correct_buy   = correct_buy   + ?,
                    incorrect_buy = incorrect_buy + ?,
                    total_assessed = total_assessed + 1,
                    last_updated  = ?
            """,
                (
                    agent,
                    regime,
                    int(vote == "BUY"),
                    int(vote == "HOLD"),
                    int(vote == "SELL"),
                    correct_buy,
                    incorrect_buy,
                    now,
                    # UPDATE deltas:
                    int(vote == "BUY"),
                    int(vote == "HOLD"),
                    int(vote == "SELL"),
                    correct_buy,
                    incorrect_buy,
                    now,
                ),
            )
            # Recompute accuracy
            row = c.execute(
                """
                SELECT correct_buy, total_assessed, votes_buy
                FROM agent_stats WHERE agent_name=? AND regime=?
            """,
                (agent, regime),
            ).fetchone()
            if row and row["votes_buy"] > 0:
                acc = row["correct_buy"] / row["votes_buy"]
                c.execute(
                    """
                    UPDATE agent_stats SET accuracy=?
                    WHERE agent_name=? AND regime=?
                """,
                    (acc, agent, regime),
                )


# ── Read helpers ──────────────────────────────────────────────────────────────


def get_signal_bayesian_pts(signal_name: str, regime: str = "any") -> float:
    """Return current Bayesian conviction points for a signal in a regime."""
    prior = float(SIGNAL_PRIOR_PTS.get(signal_name, 5))
    try:
        with _conn() as c:
            row = c.execute(
                """
                SELECT bayesian_pts, fires FROM signal_stats
                WHERE signal_name=? AND regime=? AND source='combined'
                ORDER BY fires DESC LIMIT 1
            """,
                (signal_name, regime),
            ).fetchone()
            if (
                row
                and row["fires"] >= MIN_FIRES_TO_LEARN
                and row["bayesian_pts"] is not None
            ):
                return float(row["bayesian_pts"])
            # Try 'any' if specific regime has no data
            if regime != "any":
                row2 = c.execute(
                    """
                    SELECT bayesian_pts, fires FROM signal_stats
                    WHERE signal_name=? AND regime='any' AND source='combined'
                    ORDER BY fires DESC LIMIT 1
                """,
                    (signal_name,),
                ).fetchone()
                if (
                    row2
                    and row2["fires"] >= MIN_FIRES_TO_LEARN
                    and row2["bayesian_pts"] is not None
                ):
                    return float(row2["bayesian_pts"])
    except Exception:
        pass
    return prior


def get_all_weights(regime: str = "any") -> dict[str, float]:
    """Return {signal_name: bayesian_pts} for all signals, falling back to priors."""
    return {sig: get_signal_bayesian_pts(sig, regime) for sig in SIGNAL_PRIOR_PTS}


def get_active_signal_stats_brief(active_signals: list, regime: str = "any") -> str:
    """
    Returns a compact table of Bayesian win rates for the signals that fired.
    Injected into every agent's user prompt so AI can calibrate per-signal confidence.
    Falls back to prior pts when live data is insufficient (< MIN_FIRES_TO_LEARN).
    """
    if not active_signals:
        return ""
    try:
        with _conn() as c:
            lines = [
                f"SIGNAL QUALITY (Bayesian evidence — {regime} regime):",
                f"  {'Signal':<26} {'Fires':>5} {'Win%':>6} {'AvgP&L':>8} {'BayesPts':>9} {'PriorPts':>9}",
                f"  {'-' * 26} {'-' * 5} {'-' * 6} {'-' * 8} {'-' * 9} {'-' * 9}",
            ]
            any_live_data = False
            for sig_name in active_signals:
                prior = SIGNAL_PRIOR_PTS.get(sig_name, 0)
                # Prefer regime-specific row, fall back to 'any'
                row = c.execute(
                    """
                    SELECT fires, win_rate, avg_pnl, bayesian_pts, prior_pts
                    FROM signal_stats
                    WHERE signal_name=? AND regime=? AND source='combined'
                    LIMIT 1
                """,
                    (sig_name, regime),
                ).fetchone()
                if not row and regime != "any":
                    row = c.execute(
                        """
                        SELECT fires, win_rate, avg_pnl, bayesian_pts, prior_pts
                        FROM signal_stats
                        WHERE signal_name=? AND regime='any' AND source='combined'
                        LIMIT 1
                    """,
                        (sig_name,),
                    ).fetchone()

                if row and (row["fires"] or 0) >= MIN_FIRES_TO_LEARN:
                    any_live_data = True
                    wr = (
                        f"{row['win_rate'] * 100:.0f}%"
                        if row["win_rate"] is not None
                        else "  ?"
                    )
                    ap = (
                        f"${row['avg_pnl']:+.3f}"
                        if row["avg_pnl"] is not None
                        else "     ?"
                    )
                    bp = (
                        f"{row['bayesian_pts']:.1f}"
                        if row["bayesian_pts"] is not None
                        else "  ?"
                    )
                    pp = f"{row['prior_pts'] or prior:.1f}"
                    fires = row["fires"] or 0
                    lines.append(
                        f"  {sig_name:<26} {fires:>5} {wr:>6} {ap:>8} {bp:>9} {pp:>9}"
                    )
                else:
                    fires = (row["fires"] or 0) if row else 0
                    lines.append(
                        f"  {sig_name:<26} {fires:>5} {'prior':>6} {'N/A':>8} "
                        f"{'N/A':>9} {prior:>9.0f}"
                    )

            if not any_live_data:
                return (
                    f"Active signals: {', '.join(active_signals)} "
                    f"— no live win-rate data yet, using priors only."
                )
            return "\n".join(lines)
    except Exception:
        return f"Active signals fired: {', '.join(active_signals)} (win-rate DB unavailable)"


def get_agent_self_accuracy(agent_name: str, regime: str = "any") -> str:
    """
    Returns a one-liner for an agent's own historical accuracy.
    Injected into the USER prompt (not system — keeps caching intact).
    """
    try:
        with _conn() as c:
            # Prefer regime-specific, fall back to 'any'
            row = c.execute(
                """
                SELECT votes_buy, correct_buy, accuracy
                FROM agent_stats
                WHERE agent_name=? AND regime=?
                LIMIT 1
            """,
                (agent_name, regime),
            ).fetchone()
            if not row or not row["votes_buy"] or row["votes_buy"] < 5:
                row = c.execute(
                    """
                    SELECT votes_buy, correct_buy, accuracy
                    FROM agent_stats
                    WHERE agent_name=? AND regime='any'
                    LIMIT 1
                """,
                    (agent_name,),
                ).fetchone()
            if row and row["votes_buy"] and row["votes_buy"] >= 5:
                acc = row["accuracy"] or (row["correct_buy"] / max(row["votes_buy"], 1))
                return (
                    f"YOUR PAST ACCURACY ({regime} regime): "
                    f"{row['correct_buy']}/{row['votes_buy']} BUY calls correct ({acc:.0%}). "
                    f"Use this to calibrate how confident you need to be before saying BUY."
                )
        return ""
    except Exception:
        return ""


def get_agent_accuracy_context(regime: str = "any") -> str:
    """Return a formatted string injected into agent debate prompts."""
    try:
        with _conn() as c:
            rows = c.execute(
                """
                SELECT agent_name, votes_buy, correct_buy, incorrect_buy, accuracy
                FROM agent_stats
                WHERE regime IN (?, 'any')
                ORDER BY agent_name
            """,
                (regime,),
            ).fetchall()

        if not rows:
            return ""

        lines = ["AGENT HISTORICAL ACCURACY (this regime):"]
        for r in rows:
            if r["votes_buy"] < 5:
                lines.append(
                    f"  {r['agent_name']}: < 5 BUY votes — no track record yet"
                )
            else:
                acc = r["accuracy"] or 0
                lines.append(
                    f"  {r['agent_name']}: {r['correct_buy']}/{r['votes_buy']} BUY calls correct "
                    f"({acc:.0%} accuracy)"
                )
        return "\n".join(lines)
    except Exception:
        return ""


def get_signal_report(min_fires: int = 5) -> list[dict]:
    """Return signal stats for dashboard / daily summary."""
    try:
        with _conn() as c:
            rows = c.execute(
                """
                SELECT signal_name, regime, fires, wins, win_rate,
                       avg_pnl, bayesian_pts, prior_pts, last_updated
                FROM signal_stats
                WHERE fires >= ? AND regime='any' AND source='combined'
                ORDER BY fires DESC
            """,
                (min_fires,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_top_signals(regime: str = "any", top_n: int = 5) -> list[dict]:
    """Return top-N performing signals by win_rate (min 10 fires)."""
    try:
        with _conn() as c:
            rows = c.execute(
                """
                SELECT signal_name, fires, win_rate, avg_pnl, bayesian_pts
                FROM signal_stats
                WHERE fires >= 10 AND regime=? AND source='combined'
                ORDER BY win_rate DESC LIMIT ?
            """,
                (regime, top_n),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_attribution_history(symbol: str = None, limit: int = 50) -> list[dict]:
    """Fetch recent trade attributions for analysis."""
    try:
        with _conn() as c:
            if symbol:
                rows = c.execute(
                    """
                    SELECT * FROM trade_attribution WHERE symbol=?
                    ORDER BY created_at DESC LIMIT ?
                """,
                    (symbol, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    """
                    SELECT * FROM trade_attribution
                    ORDER BY created_at DESC LIMIT ?
                """,
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
