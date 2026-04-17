"""
learning/entry_priors.py — Bayesian local win-rate priors for economics gate (v16).

Uses entered-candidate outcomes to estimate per-bucket win-rate priors,
replacing the crude hardcoded 0.54 / linear estimates in v10_runner.

Win label: hit_1r=1 AND hit_stop=0 (clean 1R winner, not stopped out).
Data source: scan_candidates JOIN candidate_outcomes, decision='entered',
             label_status='complete', source IN ('clean_paper_v10','live_v10').

Fallback hierarchy (most to least specific):
  1. exchange + primary_setup + regime + direction -> 'exchange_setup_regime_direction'
  2. primary_setup + regime + direction            -> 'setup_regime_direction'
  3. primary_setup + regime                        -> 'setup_regime'
  4. regime + direction                            -> 'regime_direction'
  5. global                                        -> 'global'

Bayesian smoothing:
  prior_p = 0.52
  prior_n = 20
  posterior = (prior_n * prior_p + wins) / (prior_n + n)
  clipped to [0.40, 0.80]
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_PRIOR_P = 0.52
_PRIOR_N = 20
_CLIP_LOW = 0.40
_CLIP_HIGH = 0.80

_VALID_SOURCES = ("clean_paper_v10", "live_v10")


def _db_path() -> str:
    try:
        from config import DB_PATH

        return DB_PATH
    except Exception:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "logs",
            "trades.db",
        )


def _fetch_bucket(
    exchange: str = "",
    primary_setup: str = "",
    regime: str = "",
    direction: str = "",
) -> tuple[int, int]:
    """Return (wins, n) for the given bucket from the live DB. Returns (0,0) on any error."""
    import sqlite3

    conditions = [
        "sc.decision = 'entered'",
        "co.label_status = 'complete'",
        f"sc.source IN ({','.join('?' for _ in _VALID_SOURCES)})",
        "co.hit_1r IS NOT NULL",
        "co.hit_stop IS NOT NULL",
    ]
    params: list = list(_VALID_SOURCES)

    if exchange:
        conditions.append("sc.exchange = ?")
        params.append(exchange)
    if primary_setup:
        conditions.append("sc.primary_setup = ?")
        params.append(primary_setup)
    if regime:
        conditions.append("sc.regime = ?")
        params.append(regime)
    if direction:
        conditions.append("sc.direction = ?")
        params.append(direction)

    sql = f"""
        SELECT
            SUM(CASE WHEN co.hit_1r = 1 AND co.hit_stop = 0 THEN 1 ELSE 0 END) AS wins,
            COUNT(*) AS n
        FROM scan_candidates sc
        JOIN candidate_outcomes co ON co.candidate_id = sc.id
        WHERE {" AND ".join(conditions)}
    """
    try:
        db = _db_path()
        with sqlite3.connect(db, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return 0, 0
            return int(row["wins"] or 0), int(row["n"] or 0)
    except Exception as e:
        logger.debug(f"[entry_priors] _fetch_bucket error: {e}")
        return 0, 0


def _bayesian_posterior(wins: int, n: int) -> float:
    """Apply Bayesian smoothing and clip to [0.40, 0.80]."""
    posterior = (_PRIOR_N * _PRIOR_P + wins) / (_PRIOR_N + n)
    return max(_CLIP_LOW, min(_CLIP_HIGH, posterior))


def estimate_candidate_win_rate(
    exchange: str = "",
    primary_setup: str = "",
    regime: str = "",
    direction: str = "",
) -> dict:
    """
    Estimate win-rate prior for the given candidate attributes.

    Returns:
        {
            "win_rate_estimate": float,  # Bayesian posterior, clipped [0.40, 0.80]
            "sample_n": int,             # sample size used (0 = fallback to global)
            "bucket_used": str,          # bucket label
        }
    """
    # Hierarchy: most specific -> global
    buckets = [
        (
            dict(
                exchange=exchange,
                primary_setup=primary_setup,
                regime=regime,
                direction=direction,
            ),
            "exchange_setup_regime_direction",
        ),
        (
            dict(primary_setup=primary_setup, regime=regime, direction=direction),
            "setup_regime_direction",
        ),
        (
            dict(primary_setup=primary_setup, regime=regime),
            "setup_regime",
        ),
        (
            dict(regime=regime, direction=direction),
            "regime_direction",
        ),
        (
            {},
            "global",
        ),
    ]

    for kwargs, bucket_label in buckets:
        wins, n = _fetch_bucket(**kwargs)
        if n >= 1:
            wr = _bayesian_posterior(wins, n)
            return {
                "win_rate_estimate": round(wr, 4),
                "sample_n": n,
                "bucket_used": bucket_label,
            }

    # No data at all -> return smoothed prior
    return {
        "win_rate_estimate": round(_bayesian_posterior(0, 0), 4),
        "sample_n": 0,
        "bucket_used": "global",
    }
