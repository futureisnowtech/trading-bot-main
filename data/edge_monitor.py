"""
data/edge_monitor.py — Rolling edge score monitor with multi-window memory.

Philosophical basis (Philosophical Supplement §1):
  "Require two consecutive windows of degraded performance before acting on it.
   Use it for gradual sizing adjustments, never for abrupt halts."

Window = last 20 closed trades per strategy.
Edge score = profit_factor - 1.0
  0.0 = break even (PF 1.0)
  0.5 = good (PF 1.5)
  1.0 = excellent (PF 2.0)

Response table:
  edge_score >= 0.45          → STRONG/OK  : 1.00× sizing
  edge_score in [0.30, 0.45) → 1 bad window: 0.75× sizing
  edge_score in [0.30, 0.45) → 2+ windows : 0.50× sizing
  edge_score < 0.30           → BLOCKED    : block new entries (PF < 1.30 = neg. EV)
  window_trades < 10          → UNCERTAIN  : no gate (not enough data)

Cached 5 minutes per strategy to avoid hammering the DB each scan cycle.
"""

import os
import sqlite3
from datetime import datetime
from typing import Dict, Tuple

_WINDOW_SIZE = (
    30  # trades per evaluation window (was 20 — too small, one bad run distorted PF)
)
_BAD_THRESHOLD = 0.45  # PF < 1.45 = degraded window
_BLOCK_THRESHOLD = 0.10  # PF < 1.10 = block entries (was 0.30/PF1.30 — too strict at small sample sizes)
_MIN_TRADES_TO_GATE = 20  # require at least this many trades before gating (was 10)
_CACHE_MINUTES = 5

_cache: Dict[str, dict] = {}

# Resolve DB path from config without circular import risk
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB_PATH = os.path.join(_PROJ_ROOT, "logs", "trades.db")


def _conn():
    if not os.path.exists(_DB_PATH):
        return None
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def get_edge_state(strategy: str, paper: bool = True) -> dict:
    """
    Compute rolling edge state for a strategy.

    Returns:
      edge_score          float   — current window profit_factor - 1
      consecutive_bad     int     — how many consecutive windows were degraded
      sizing_multiplier   float   — 1.0 / 0.75 / 0.50 / 0.0
      should_block        bool    — True when edge_score < BLOCK_THRESHOLD
      window_trades       int     — trades in current evaluation window
      status              str     — 'STRONG' | 'OK' | 'DEGRADED' | 'UNCERTAIN' | 'BLOCKED'
    """
    cached = _cache.get(strategy)
    if cached:
        age = (datetime.now() - cached["computed_at"]).total_seconds() / 60
        if age < _CACHE_MINUTES:
            return {k: v for k, v in cached.items() if k != "computed_at"}

    conn = _conn()
    if conn is None:
        return _default_state(status="UNCERTAIN")

    try:
        cur = conn.cursor()
        # Fetch last 2 windows to detect consecutive degradation
        cur.execute(
            """
            SELECT pnl_usd FROM trades
            WHERE strategy=? AND paper=? AND pnl_usd != 0
            ORDER BY ts DESC
            LIMIT ?
        """,
            (strategy, int(paper), _WINDOW_SIZE * 2),
        )
        rows = [r["pnl_usd"] for r in cur.fetchall()]
        conn.close()
    except Exception:
        conn.close()
        return _default_state(status="UNCERTAIN")

    window_trades = min(len(rows), _WINDOW_SIZE)

    if window_trades < _MIN_TRADES_TO_GATE:
        result = _default_state(window_trades=window_trades, status="UNCERTAIN")
        _store_cache(strategy, result)
        return result

    # ── Current window ────────────────────────────────────────────────────────
    current_pnls = rows[:_WINDOW_SIZE]
    edge_score, pf = _compute_edge(current_pnls)

    # ── Previous window (for consecutive-bad detection) ───────────────────────
    prev_bad = False
    if len(rows) >= _WINDOW_SIZE * 2:
        prev_pnls = rows[_WINDOW_SIZE : _WINDOW_SIZE * 2]
        prev_edge, _ = _compute_edge(prev_pnls)
        prev_bad = prev_edge < _BAD_THRESHOLD

    # ── Graduated response ────────────────────────────────────────────────────
    consecutive_bad = 0
    if edge_score < _BAD_THRESHOLD:
        consecutive_bad = 2 if prev_bad else 1

    should_block = edge_score < _BLOCK_THRESHOLD

    if should_block:
        multiplier, status = 0.0, "BLOCKED"
    elif consecutive_bad >= 2:
        multiplier, status = 0.50, "DEGRADED"
    elif consecutive_bad == 1:
        multiplier, status = 0.75, "DEGRADED"
    elif edge_score >= 0.60:
        multiplier, status = 1.0, "STRONG"
    else:
        multiplier, status = 1.0, "OK"

    result = {
        "edge_score": round(edge_score, 3),
        "profit_factor": round(pf, 3),
        "consecutive_bad": consecutive_bad,
        "sizing_multiplier": multiplier,
        "should_block": should_block,
        "window_trades": window_trades,
        "status": status,
    }
    _store_cache(strategy, result)
    return result


def is_in_stop_cooldown(
    strategy: str, symbol: str, paper: bool = True
) -> Tuple[bool, str]:
    """
    Check if a full-stop-loss hit has occurred in the last 30 minutes for this symbol.
    Called before any new entry — prevents re-entering a symbol immediately after a stop.

    Philosophical basis (§6): "After any trade that hits its full stop loss,
    no new entries for 30 minutes in that market."
    """
    conn = _conn()
    if conn is None:
        return False, ""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ts FROM trades
            WHERE strategy=? AND symbol=? AND paper=?
              AND pnl_usd < 0
              AND (LOWER(notes) LIKE '%stop%' OR LOWER(notes) LIKE '%hard stop%')
              AND ts >= datetime('now', '-30 minutes')
            ORDER BY ts DESC LIMIT 1
        """,
            (strategy, symbol, int(paper)),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return (
                True,
                f"30-min stop cooldown active: {symbol} stopped out at {row['ts'][:19]}",
            )
        return False, ""
    except Exception:
        return False, ""


def format_edge_context(state: dict) -> str:
    """One-line summary for agent context injection."""
    return (
        f"Edge: {state['status']} | PF={state.get('profit_factor', '?'):.2f} "
        f"| score={state['edge_score']:.2f} "
        f"| {state['window_trades']} trades "
        f"| consecutive_bad={state['consecutive_bad']} "
        f"| sizing={state['sizing_multiplier']:.0%}"
    )


def invalidate_cache(strategy: str = None) -> None:
    """Call after any trade closes to force fresh recomputation."""
    if strategy:
        _cache.pop(strategy, None)
    else:
        _cache.clear()


def _compute_edge(pnls: list) -> Tuple[float, float]:
    gross_wins = sum(p for p in pnls if p > 0)
    gross_losses = abs(sum(p for p in pnls if p < 0))
    if gross_losses == 0:
        pf = 2.0 if gross_wins > 0 else 1.0
    else:
        pf = gross_wins / gross_losses
    return max(0.0, pf - 1.0), pf


def _default_state(window_trades: int = 0, status: str = "UNCERTAIN") -> dict:
    return {
        "edge_score": 0.50,  # neutral — don't gate on insufficient data
        "profit_factor": 1.50,
        "consecutive_bad": 0,
        "sizing_multiplier": 1.0,
        "should_block": False,
        "window_trades": window_trades,
        "status": status,
    }


def _store_cache(strategy: str, result: dict) -> None:
    entry = dict(result)
    entry["computed_at"] = datetime.now()
    _cache[strategy] = entry


# ══════════════════════════════════════════════════════════════════════════════
# Shadow State — Kalman / Kyle's Lambda / ADF / OU Halflife
# Manifest Sections 1.1, 2.1, 2.2
# ══════════════════════════════════════════════════════════════════════════════

_SHADOW_STATE: dict = {}


def get_shadow_state(symbol: str) -> dict:
    """
    Return last-known-good shadow state for symbol.
    Returns {} when cold (first ~60s after startup).
    All callers must treat missing keys as fail-open defaults.
    """
    return _SHADOW_STATE.get(symbol, {})


def _compute_kalman(prices: list) -> tuple:
    """
    1D Kalman filter over a price series.
    Returns (fair_value: float, dev_pct: float).
    dev_pct = (last_price - fair_value) / fair_value * 100
    Process noise Q=0.001, measurement noise R=0.01 (conservative).
    """
    import numpy as _np

    Q, R = 0.001, 0.01
    x = float(prices[0])
    P = 1.0
    for obs in prices[1:]:
        P += Q
        K = P / (P + R)
        x += K * (float(obs) - x)
        P = (1.0 - K) * P
    last = float(prices[-1])
    dev_pct = (last - x) / x * 100.0 if x != 0.0 else 0.0
    return round(x, 6), round(dev_pct, 4)


def _compute_kyle_lambda(prices: list, volumes: list) -> tuple:
    """
    OLS estimate of Kyle's Lambda (price impact per unit signed volume).
    Returns (lambda_estimate: float, is_fragile: bool).
    is_fragile=True when current lambda > rolling_mean + 2*rolling_std.
    """
    import numpy as _np

    if len(prices) < 10:
        return 0.0, False
    p = _np.array(prices, dtype=float)
    v = _np.array(volumes, dtype=float)
    dp = _np.diff(p)
    sv = _np.sign(dp) * v[1:]
    denom = float(sv @ sv)
    lam = float((sv @ dp) / denom) if denom != 0.0 else 0.0
    lams = []
    step = max(5, len(dp) // 10)
    for i in range(0, len(dp) - step, step):
        d_i, s_i = dp[i : i + step], sv[i : i + step]
        dd = float(s_i @ s_i)
        if dd != 0.0:
            lams.append(float((s_i @ d_i) / dd))
    if len(lams) < 3:
        return lam, False
    mean_l = float(_np.mean(lams))
    std_l = float(_np.std(lams))
    fragile = std_l > 0.0 and lam > mean_l + 2.0 * std_l
    return lam, bool(fragile)


def _compute_adf_stat(prices: list) -> tuple:
    """
    Lightweight ADF test (numpy-only, no statsmodels).
    Critical value: -2.86 (MacKinnon 5%, n~100, constant only).
    Returns (adf_statistic: float, is_stationary: bool).
    Fails open (returns is_stationary=True) on numerical error.
    """
    import numpy as _np

    y = _np.array(prices, dtype=float)
    dy = _np.diff(y)
    y_lag = y[:-1]
    X = _np.column_stack([_np.ones(len(y_lag)), y_lag])
    try:
        coeffs, _, _, _ = _np.linalg.lstsq(X, dy, rcond=None)
    except _np.linalg.LinAlgError:
        return 0.0, True
    beta = float(coeffs[1])
    resid = dy - X @ coeffs
    n = len(resid)
    s2 = float(_np.sum(resid**2) / max(n - 2, 1))
    ss_lag = float(y_lag @ y_lag) - len(y_lag) * float(_np.mean(y_lag)) ** 2
    se = float(_np.sqrt(s2 / max(ss_lag, 1e-12)))
    adf_stat = beta / se if se > 0.0 else 0.0
    return round(adf_stat, 4), bool(adf_stat < -2.86)


def _compute_ou_halflife(prices: list) -> float:
    """
    Ornstein-Uhlenbeck halflife via AR(1) regression.
    halflife = -ln(2) / ln(|phi|). Returns 999.0 when non-stationary.
    """
    import numpy as _np

    y = _np.array(prices, dtype=float)
    y_lag = y[:-1]
    y_now = y[1:]
    X = _np.column_stack([_np.ones(len(y_lag)), y_lag])
    try:
        coeffs, _, _, _ = _np.linalg.lstsq(X, y_now, rcond=None)
    except _np.linalg.LinAlgError:
        return 999.0
    phi = float(coeffs[1])
    if abs(phi) >= 1.0:
        return 999.0
    hl = -_np.log(2.0) / _np.log(abs(phi))
    return round(float(hl), 2)


async def update_shadow_state(
    symbol: str,
    prices: list,
    volumes: list,
) -> None:
    """
    Compute and cache all shadow state metrics for one symbol.
    Call every 60 seconds per symbol from the async runner loop.
    Requires at least 20 price/volume bars. Silently returns if fewer.
    """
    if len(prices) < 20 or len(volumes) < 20:
        return
    fair_value, dev_pct = _compute_kalman(prices)
    kyle_lam, is_fragile = _compute_kyle_lambda(prices, volumes)
    adf_stat, is_stationary = _compute_adf_stat(prices)
    ou_hl = _compute_ou_halflife(prices)
    _SHADOW_STATE[symbol] = {
        "kalman_fair_value": fair_value,
        "kalman_dev_pct": dev_pct,
        "kyle_lambda": round(kyle_lam, 8),
        "kyle_lambda_fragile": is_fragile,
        "adf_stat": adf_stat,
        "adf_stationary": is_stationary,
        "ou_halflife_bars": ou_hl,
        "computed_at": __import__("datetime").datetime.utcnow().isoformat(),
    }


# ── WAE Missed Winner Tracking (Manifest Section 5.1) ────────────────────────

_WAE_MISSED_COUNTER: dict = {}
_WAE_RECOVERY_THRESHOLD = 3


def increment_wae_missed(strategy: str = "spot_scalp") -> int:
    """Increment consecutive missed WAE counter. Returns new count."""
    count = _WAE_MISSED_COUNTER.get(strategy, 0) + 1
    _WAE_MISSED_COUNTER[strategy] = count
    return count


def reset_wae_missed(strategy: str = "spot_scalp") -> None:
    """Reset WAE missed counter after a successful WAE entry."""
    _WAE_MISSED_COUNTER[strategy] = 0


def is_wae_recovery_mode(strategy: str = "spot_scalp") -> bool:
    """True when > 3 consecutive WAE setups have been missed."""
    return _WAE_MISSED_COUNTER.get(strategy, 0) > _WAE_RECOVERY_THRESHOLD


# ── Strategy Ladder — Automated Probation (Manifest Section 6.1) ─────────────

import os as _os
import sqlite3 as _sqlite3
from datetime import datetime as _datetime

_LADDER_STATE: dict = {}
_PROBATION_WR_GATE = 0.20
_REINSTATE_WR_GATE = 0.40
_SHADOW_CANDIDATES = 10
_INCUBATION_TRADES = 5
_LADDER_CACHE_MIN = 30
_LADDER_MIN_TRADES = 10
_LADDER_DB_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "logs", "trades.db"
)


def _ladder_conn():
    if not _os.path.exists(_LADDER_DB_PATH):
        return None
    c = _sqlite3.connect(_LADDER_DB_PATH)
    c.row_factory = _sqlite3.Row
    return c


def get_strategy_ladder_state(
    strategy: str,
    paper: bool = True,
) -> dict:
    """
    Compute and return the current RBIPMS ladder state for a strategy.

    State machine:
      ACTIVE     -> PROBATION   if 14d win_rate < 0.20 (and n >= 10)
      PROBATION  -> INCUBATION  if shadow win_rate >= 0.40 over last 10 candidates
      INCUBATION -> ACTIVE      after 5 clean incubation trades
    """
    cached = _LADDER_STATE.get(strategy)
    if cached:
        age = (
            _datetime.now() - cached.get("_computed_at", _datetime.min)
        ).total_seconds() / 60.0
        if age < _LADDER_CACHE_MIN:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    _default = {
        "state": "ACTIVE",
        "win_rate_14d": 1.0,
        "should_shadow": False,
        "shadow_n": 0,
        "shadow_wins": 0,
        "incubation_n": 0,
    }
    conn = _ladder_conn()
    if conn is None:
        return _default

    try:
        row = conn.execute(
            """
            SELECT COUNT(*) as n,
                   SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) as wins
            FROM trades
            WHERE strategy=? AND paper=?
              AND ts >= datetime('now', '-14 days')
              AND won IS NOT NULL
            """,
            (strategy, int(paper)),
        ).fetchone()
        conn.close()
    except Exception:
        return _default

    n = int(row["n"] or 0)
    wins = int(row["wins"] or 0)
    win_rate = wins / n if n >= _LADDER_MIN_TRADES else 1.0

    current = _LADDER_STATE.get(strategy, {})
    state = current.get("state", "ACTIVE")
    shadow_n = current.get("shadow_n", 0)
    shadow_wins = current.get("shadow_wins", 0)
    incub_n = current.get("incubation_n", 0)

    if state == "ACTIVE":
        if win_rate < _PROBATION_WR_GATE:
            state = "PROBATION"
            shadow_n = shadow_wins = 0
            import logging as _logging

            _logging.getLogger(__name__).warning(
                f"[strategy_ladder] {strategy} -> PROBATION 14d_wr={win_rate:.1%} n={n}"
            )
    elif state == "PROBATION":
        if shadow_n >= _SHADOW_CANDIDATES:
            shadow_wr = shadow_wins / shadow_n
            if shadow_wr >= _REINSTATE_WR_GATE:
                state = "INCUBATION"
                incub_n = 0
                import logging as _logging

                _logging.getLogger(__name__).info(
                    f"[strategy_ladder] {strategy} -> INCUBATION "
                    f"shadow_wr={shadow_wr:.1%}"
                )
    elif state == "INCUBATION":
        if incub_n >= _INCUBATION_TRADES:
            state = "ACTIVE"
            import logging as _logging

            _logging.getLogger(__name__).info(
                f"[strategy_ladder] {strategy} -> ACTIVE (restored)"
            )

    result = {
        "state": state,
        "win_rate_14d": round(win_rate, 4),
        "should_shadow": state == "PROBATION",
        "shadow_n": shadow_n,
        "shadow_wins": shadow_wins,
        "incubation_n": incub_n,
    }
    _LADDER_STATE[strategy] = {**result, "_computed_at": _datetime.now()}
    return result


def record_shadow_outcome(strategy: str, won: bool) -> None:
    """Record outcome of a shadow candidate (trade not executed). PROBATION only."""
    current = _LADDER_STATE.get(strategy, {})
    if current.get("state") != "PROBATION":
        return
    _LADDER_STATE[strategy]["shadow_n"] = current.get("shadow_n", 0) + 1
    _LADDER_STATE[strategy]["shadow_wins"] = current.get("shadow_wins", 0) + int(won)
    _LADDER_STATE[strategy].pop("_computed_at", None)


def record_incubation_trade(strategy: str) -> None:
    """Record completion of a live trade during INCUBATION."""
    current = _LADDER_STATE.get(strategy, {})
    if current.get("state") != "INCUBATION":
        return
    _LADDER_STATE[strategy]["incubation_n"] = current.get("incubation_n", 0) + 1
    _LADDER_STATE[strategy].pop("_computed_at", None)
