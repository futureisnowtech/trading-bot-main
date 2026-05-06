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

from config import PAPER_TRADING

# ─── Market-level Constants (Ported from risk/edge_monitor.py) ───────────────
WINDOW = 20               # rolling trade window
EDGE_LOW_THRESHOLD  = 0.30  # below this = degraded edge
EDGE_HIGH_THRESHOLD = 0.70  # above this = strong edge
CONSECUTIVE_TRIGGER = 2   # windows in a row before auto-action fires

# ─── Consecutive window counters (in-memory, resets on restart) ──────────────
_consecutive_low:  Dict[str, int] = {}   # market → int (consecutive low-edge windows)
_consecutive_high: Dict[str, int] = {}   # market → int (consecutive high-edge windows)


def _conn():
    if not os.path.exists(_DB_PATH):
        return None
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ─── Market-level Helpers ────────────────────────────────────────────────────

def strategy_to_market(strategy: str) -> str:
    """Map a strategy name string to a market label."""
    s = strategy.lower()
    if 'poly' in s:
        return 'polymarket'
    if 'futures' in s or 'mes' in s or 'scalp' in s:
        return 'mes'
    return 'crypto'   # default


def _get_market_trades(market: str, window: int, paper: bool) -> list:
    """Return the most recent `window` completed trades for `market`."""
    try:
        conn = _conn()
        if not conn: return []
        
        if market == 'polymarket':
            rows = conn.execute(
                "SELECT pnl_usd, value_usd, fee_usd FROM trades "
                "WHERE paper=? AND strategy LIKE '%poly%' AND pnl_usd != 0 "
                "ORDER BY ts DESC LIMIT ?",
                (1 if paper else 0, window),
            ).fetchall()
        elif market == 'mes':
            rows = conn.execute(
                "SELECT pnl_usd, value_usd, fee_usd FROM trades "
                "WHERE paper=? AND (strategy LIKE '%futures%' OR strategy LIKE '%mes%') "
                "AND pnl_usd != 0 ORDER BY ts DESC LIMIT ?",
                (1 if paper else 0, window),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT pnl_usd, value_usd, fee_usd FROM trades "
                "WHERE paper=? AND strategy NOT LIKE '%poly%' "
                "AND strategy NOT LIKE '%futures%' AND strategy NOT LIKE '%mes%' "
                "AND pnl_usd != 0 ORDER BY ts DESC LIMIT ?",
                (1 if paper else 0, window),
            ).fetchall()

        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _compute_market_edge_score_metrics(trades: list) -> dict:
    """Compute market-level edge metrics from a list of trade dicts."""
    n = len(trades)
    if n == 0:
        return {'win_rate': 0.0, 'profit_factor': 0.0, 'sharpe': 0.0,
                'edge_score': 0.0, 'n_trades': 0}

    import math
    pnls = [float(t.get('pnl_usd', 0.0)) for t in trades]
    wins  = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_rate = len(wins) / n
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / max(gross_loss, 1e-10)

    if n < 2:
        sharpe = 0.0
    else:
        mean_pnl = sum(pnls) / n
        import numpy as np
        std_pnl  = float(np.std(pnls)) if n > 1 else 1e-10
        sharpe   = mean_pnl / std_pnl if std_pnl > 0 else 0.0

    # Normalise each component to [0, 1]
    norm_wr     = _clamp((win_rate - 0.30) / (0.70 - 0.30), 0.0, 1.0)
    norm_pf     = _clamp((profit_factor - 0.80) / (2.00 - 0.80), 0.0, 1.0)
    norm_sharpe = _clamp((sharpe - (-1.0)) / (2.0 - (-1.0)), 0.0, 1.0)

    edge_score = 0.40 * norm_wr + 0.35 * norm_pf + 0.25 * norm_sharpe

    return {
        'win_rate':      round(win_rate, 4),
        'profit_factor': round(profit_factor, 4),
        'sharpe':        round(sharpe, 4),
        'edge_score':    round(_clamp(edge_score, 0.0, 1.0), 4),
        'n_trades':      n,
    }


def get_market_edge_score(
    market: str = 'crypto',
    window: int = WINDOW,
    paper: bool = None,
) -> dict:
    """Compute the rolling market-level edge score."""
    if paper is None:
        paper = PAPER_TRADING

    trades = _get_market_trades(market, window, paper)
    metrics = _compute_market_edge_score_metrics(trades)

    metrics['sufficient'] = metrics['n_trades'] >= window
    metrics['market']     = market

    return metrics


def check_market_edge_actions(
    market: str = 'crypto',
    paper: bool = None,
) -> str | None:
    """Evaluate current market edge and fire auto-actions if thresholds are breached."""
    if paper is None:
        paper = PAPER_TRADING

    metrics = get_market_edge_score(market=market, paper=paper)
    if metrics['n_trades'] < WINDOW // 2:
        return None

    score = metrics['edge_score']
    action_taken = None

    if score < EDGE_LOW_THRESHOLD:
        _consecutive_low[market]  = _consecutive_low.get(market, 0) + 1
        _consecutive_high[market] = 0
        if _consecutive_low[market] >= CONSECUTIVE_TRIGGER:
            _fire_notification(
                market,
                f"[EdgeMonitor] {market.upper()} edge degraded: "
                f"score={score:.2f} (WR={metrics['win_rate']:.0%} "
                f"PF={metrics['profit_factor']:.2f}) — position size REDUCED 50%",
                level='WARNING',
            )
            action_taken = 'size_down'
    elif score > EDGE_HIGH_THRESHOLD:
        _consecutive_high[market]  = _consecutive_high.get(market, 0) + 1
        _consecutive_low[market]   = 0
        if _consecutive_high[market] >= CONSECUTIVE_TRIGGER:
            _fire_notification(
                market,
                f"[EdgeMonitor] {market.upper()} edge strong: "
                f"score={score:.2f} — position size allowed toward Kelly max",
                level='INFO',
            )
            action_taken = 'size_up'
    else:
        _consecutive_low[market]  = 0
        _consecutive_high[market] = 0

    return action_taken


def get_market_edge_size_factor(
    market: str = 'crypto',
    paper: bool = None,
) -> float:
    """Return the size factor from current market-level edge state."""
    if paper is None:
        paper = PAPER_TRADING
    consecutive_low = _consecutive_low.get(market, 0)
    if consecutive_low >= CONSECUTIVE_TRIGGER:
        return 0.50
    return 1.00


def _fire_notification(market: str, message: str, level: str = 'INFO') -> None:
    try:
        from logging_db.trade_logger import log_event
        log_event(level, 'edge_monitor', message)
    except Exception:
        import logging
        logging.getLogger(__name__).error(f"[edge_monitor] {level}: {message}")


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
_ADF_EMA_STATE: Dict[str, float] = {}
_KYLE_LAMBDA_HISTORY: dict[str, list[float]] = {}
_KYLE_LAMBDA_MAX_HISTORY = 1440  # 24h at 1-bar-per-minute cadence


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


def _compute_kyle_lambda(prices: list, volumes: list) -> float:
    """
    OLS estimate of Kyle's Lambda (price impact per unit signed sqrt-volume).
    Returns lambda_estimate: float. Fragility detection moved to update_shadow_state
    using the persistent 24h rolling history in _KYLE_LAMBDA_HISTORY.
    Sqrt-volume normalization reduces heteroscedasticity from large sweeps.
    """
    import numpy as _np

    if len(prices) < 10:
        return 0.0
    p = _np.array(prices, dtype=float)
    v = _np.array(volumes, dtype=float)
    dp = _np.diff(p)
    sv = _np.sign(dp) * _np.sqrt(_np.abs(v[1:]))
    denom = float(sv @ sv)
    return float((sv @ dp) / denom) if denom != 0.0 else 0.0


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


def _compute_ou_transition_prob(
    current_price: float,
    target_price: float,
    mu: float,
    ou_halflife_bars: float,
    price_std: float,
) -> float:
    """
    P(X_T >= target | X_0 = current_price) under an Ornstein-Uhlenbeck process.

    Uses the analytically tractable Gaussian transition density — the closed-form
    solution to the Chapman-Kolmogorov equation for OU kernels. No Monte Carlo needed.

    Returns 0.5 (fail-open) when inputs are degenerate.
    """
    from math import erf as _erf, exp as _exp, log as _log, sqrt as _sqrt

    if ou_halflife_bars <= 0 or price_std <= 0 or current_price <= 0:
        return 0.5
    theta = _log(2.0) / ou_halflife_bars  # mean-reversion speed
    T = ou_halflife_bars  # horizon = one halflife
    exp_decay = _exp(-theta * T)  # = 0.5 by definition at T == halflife
    cond_mean = mu + (current_price - mu) * exp_decay
    cond_var = max((price_std**2) / (2.0 * theta) * (1.0 - exp_decay**2), 1e-12)
    z = (target_price - cond_mean) / _sqrt(cond_var)
    return float(0.5 * (1.0 - _erf(z / _sqrt(2.0))))  # P(X_T >= target)


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
    import numpy as _np

    fair_value, dev_pct = _compute_kalman(prices)

    # Kyle's Lambda — sqrt-volume OLS; fragility via 24h rolling history
    kyle_lam = _compute_kyle_lambda(prices, volumes)
    hist = _KYLE_LAMBDA_HISTORY.setdefault(symbol, [])
    hist.append(kyle_lam)
    if len(hist) > _KYLE_LAMBDA_MAX_HISTORY:
        hist.pop(0)
    if len(hist) >= 10:
        arr = _np.array(hist, dtype=float)
        is_fragile = bool(kyle_lam > arr.mean() + 2.0 * arr.std())
    else:
        is_fragile = False

    adf_stat, _ = _compute_adf_stat(prices)
    
    # v18.16: Rolling ADF EMA (span=5) to prevent stationarity flicker
    # alpha = 2/(span+1) = 2/6 = 0.3333
    alpha = 0.3333
    last_ema = _ADF_EMA_STATE.get(symbol, adf_stat)
    ema_adf = (alpha * adf_stat) + ((1.0 - alpha) * last_ema)
    _ADF_EMA_STATE[symbol] = ema_adf
    
    # Stationarity threshold: -3.10 (MacKinnon 5% critical value)
    is_stationary = bool(ema_adf < -3.10)
    
    ou_hl = _compute_ou_halflife(prices)

    # OU transition probability — P(price reaches 0.3% scalp target within one halflife)
    price_std = float(_np.std(prices[-60:]) if len(prices) >= 60 else _np.std(prices))
    scalp_target = float(prices[-1]) * 1.003
    ou_prob = _compute_ou_transition_prob(
        current_price=float(prices[-1]),
        target_price=scalp_target,
        mu=fair_value,
        ou_halflife_bars=ou_hl,
        price_std=price_std,
    )

    _SHADOW_STATE[symbol] = {
        "kalman_fair_value": fair_value,
        "kalman_dev_pct": dev_pct,
        "kyle_lambda": round(kyle_lam, 8),
        "kyle_lambda_fragile": is_fragile,
        "adf_stat": round(ema_adf, 4),
        "adf_stationary": is_stationary,
        "ou_halflife_bars": ou_hl,
        "ou_transition_prob": round(ou_prob, 4),
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
