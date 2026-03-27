"""
risk/unified_sizer.py — Unified position sizing for all three markets.

Formula:  position_size_usd = base_size × V × E × D × T × K × M

  base_size  — from config (CRYPTO_POSITION_SIZE_USD, etc.)
  V          — volatility regime score [0.20, 1.00] (risk/volatility_regime.py)
  E          — edge quality score [0.00, 1.00] (risk/edge_monitor.py)
  D          — drawdown heat factor [0.00, 1.00] (risk/drawdown_controller.py)
  T          — time-of-day multiplier [0.50, 1.50] (inline, session-aware)
  K          — Kelly fraction [0.50, 1.00] (risk/position_sizer.py)
  M          — memory similarity [0.80, 1.20] (placeholder 1.0 until Sprint 8)

Devil's advocate gate:
  If completed trade count for this market < USE_ADAPTIVE_SIZING_MIN_TRADES (20),
  V and E multipliers are noisy and should not be applied.
  Gate: skip V and E when insufficient data. D and T always apply (they don't
  need trade history). K has its own built-in gate (activates after 15 trades).

Min / max guardrails:
  Result is clamped between MIN_POSITION_USD (5.0) and max from config.
  This prevents rounding or multiplier chains from producing unexecutable sizes.
"""
import os
import sys
from datetime import datetime
from typing import Optional
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    ACCOUNT_SIZE, PAPER_TRADING, MARKET_TIMEZONE,
    CRYPTO_POSITION_SIZE_USD, PERP_POSITION_SIZE_USD,
)

# ─── Constants ────────────────────────────────────────────────────────────────
USE_ADAPTIVE_SIZING_MIN_TRADES: int = 20   # V and E stay at 1.0 below this
MIN_POSITION_USD: float = 5.0              # absolute floor (below this = skip trade)
MAX_POSITION_SCALE: float = 1.5            # multipliers cannot push size above 150% of base

# Time-of-day multipliers (ET timezone)
_TOD_SCHEDULE = [
    # (start_hour, end_hour, multiplier)
    (2,   5,   0.50),   # dead zone — worst fills, thin books
    (5,   8,   0.70),   # pre-market — limited participation
    (9.5, 10.5, 1.50),  # NY open — highest-conviction window
    (10.5, 11.5, 1.20), # prime morning
    (11.5, 14.5, 0.60), # lunch dead zone — keep minimum exposure
    (14.5, 16.0, 1.10), # close run-up
    (16.0, 21.0, 0.80), # after hours / early evening
    # 21.0–2.0 covers late evening → Asia open at 0.9x (default below)
]
_TOD_DEFAULT: float = 0.90   # Asia session / overnight


def _get_time_of_day_multiplier(strategy: str) -> float:
    """
    Return the time-of-day size multiplier for the current ET time.

    Prediction markets (24/7 with uniform liquidity) always return 1.0.
    MES futures: follows ET session schedule strictly.
    Crypto: follows ET schedule (Coinbase/Binance liquidity mirrors US hours).
    """
    strat_lower = strategy.lower()
    if 'poly' in strat_lower:
        return 1.0   # prediction markets: 24/7, no session effect

    try:
        tz = pytz.timezone(MARKET_TIMEZONE)
        now = datetime.now(tz)
        hour_float = now.hour + now.minute / 60.0
        for start, end, multiplier in _TOD_SCHEDULE:
            if start <= hour_float < end:
                return multiplier
        return _TOD_DEFAULT
    except Exception:
        return 1.0   # safe default on error


def _get_trade_count(market: str, paper: bool) -> int:
    """Return total completed trades for this market from SQLite."""
    try:
        import sqlite3
        from config import DB_PATH

        conn = sqlite3.connect(DB_PATH)
        if market == 'polymarket':
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE paper=? AND strategy LIKE '%poly%' AND pnl_usd != 0",
                (1 if paper else 0,)
            ).fetchone()
        elif market == 'mes':
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE paper=? "
                "AND (strategy LIKE '%futures%' OR strategy LIKE '%mes%') AND pnl_usd != 0",
                (1 if paper else 0,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE paper=? "
                "AND strategy NOT LIKE '%poly%' "
                "AND strategy NOT LIKE '%futures%' AND strategy NOT LIKE '%mes%' "
                "AND pnl_usd != 0",
                (1 if paper else 0,)
            ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _strategy_to_market(strategy: str) -> str:
    """Map strategy name to market label."""
    s = strategy.lower()
    if 'poly' in s:
        return 'polymarket'
    if 'futures' in s or 'mes' in s or 'scalp' in s:
        return 'mes'
    return 'crypto'


def get_position_size(
    strategy: str,
    symbol: str,
    base_size: float,
    confidence: float,
    paper: Optional[bool] = None,
    current_price: float = 0.0,
    funding_rate: float = 0.0,
) -> float:
    """
    Compute the final position size for a trade entry.

    Args:
        strategy:      Strategy name string (used to derive market + Kelly stats).
        symbol:        Instrument symbol (used for vol regime lookup).
        base_size:     Requested position size in USD (from config or caller).
        confidence:    Agent debate confidence [0, 1] (used by Kelly component).
        paper:         Override paper/live mode. Defaults to config.PAPER_TRADING.
        current_price: Current price (optional — used for stop math if needed).
        funding_rate:  Current 8h funding rate (crypto, fraction). Passed to vol regime.

    Returns:
        Final position size in USD, always ≥ MIN_POSITION_USD and ≤ base_size × MAX_POSITION_SCALE.
        Returns 0.0 if base_size is zero or any hard block is hit.
    """
    if paper is None:
        paper = PAPER_TRADING

    if base_size <= 0:
        return 0.0

    market = _strategy_to_market(strategy)
    trade_count = _get_trade_count(market, paper)
    adaptive = trade_count >= USE_ADAPTIVE_SIZING_MIN_TRADES

    # ── V: Volatility regime ──────────────────────────────────────────────────
    v_score = 1.0
    if adaptive:
        try:
            from risk.volatility_regime import get_volatility_regime
            regime = get_volatility_regime(symbol, market=market, funding_rate=funding_rate)
            v_score = regime['v_score']
        except Exception:
            v_score = 0.75   # NORMAL fallback

    # ── E: Edge quality ───────────────────────────────────────────────────────
    e_score = 1.0
    if adaptive:
        try:
            from risk.edge_monitor import get_edge_score, get_edge_size_factor
            edge_data = get_edge_score(market=market, paper=paper)
            # Use normalised edge score centred at 0.5 → multiplier 0.5–1.5
            # edge_score 0.0 → E=0.50, 0.5 → E=1.00, 1.0 → E=1.50
            raw_edge = edge_data['edge_score']
            e_score = 0.50 + raw_edge  # [0.50, 1.50]
            # Also apply binary size_down factor from consecutive low windows
            e_score *= get_edge_size_factor(market=market, paper=paper)
        except Exception:
            e_score = 1.0

    # ── D: Drawdown heat factor ───────────────────────────────────────────────
    d_factor = 1.0
    try:
        from risk.drawdown_controller import get_heat_level
        heat = get_heat_level(paper=paper)
        d_factor = heat['size_factor']
        if d_factor == 0.0:
            return 0.0   # HALT level — no entries
    except Exception:
        d_factor = 1.0

    # ── T: Time-of-day multiplier ─────────────────────────────────────────────
    t_mult = _get_time_of_day_multiplier(strategy)

    # ── K: Kelly fraction ─────────────────────────────────────────────────────
    # Delegate to existing position_sizer which already has its own activation gate
    # (activates at 15 trades) and losing-streak clamp logic.
    # We pass base_size=1.0 and capture the fractional output.
    k_factor = 1.0
    try:
        from risk.position_sizer import size_from_kelly
        # size_from_kelly applies heat + Kelly to a base size; we want pure K fraction.
        # To extract K only: call with base_size=1.0, confidence, then divide out D
        k_raw = size_from_kelly(strategy, symbol, 1.0, confidence, paper)
        # k_raw already has heat baked in; we want pure Kelly fraction
        # Reverse out D: k_factor = k_raw / max(d_factor, 1e-10)
        k_factor = k_raw / max(d_factor, 1e-10)
        k_factor = max(0.25, min(k_factor, 1.50))  # safety clamp
    except Exception:
        k_factor = max(0.60, min(float(confidence), 1.0))

    # ── M: Memory similarity (placeholder) ───────────────────────────────────
    # Sprint 8: replace LanceDB with NumPy cosine similarity.
    # Until then, M = 1.0 (neutral, no adjustment).
    m_score = 1.0

    # ── Final formula ─────────────────────────────────────────────────────────
    if adaptive:
        final = base_size * v_score * e_score * d_factor * t_mult * k_factor * m_score
    else:
        # Pre-20-trade: apply only D (drawdown) and T (time-of-day).
        # V and E are noise before sufficient trade history.
        # K still applies via position_sizer (it has its own 15-trade gate).
        final = base_size * d_factor * t_mult * k_factor * m_score

    # ── Guardrails ────────────────────────────────────────────────────────────
    max_size = base_size * MAX_POSITION_SCALE
    final = max(MIN_POSITION_USD, min(final, max_size))

    return round(final, 2)


def get_sizing_breakdown(
    strategy: str,
    symbol: str,
    base_size: float,
    confidence: float,
    paper: Optional[bool] = None,
    funding_rate: float = 0.0,
) -> dict:
    """
    Return a full breakdown of all multipliers for diagnostics / MCP server.

    Returns dict with: base_size, v, e, d, t, k, m, final_size, adaptive, trade_count.
    """
    if paper is None:
        paper = PAPER_TRADING

    market = _strategy_to_market(strategy)
    trade_count = _get_trade_count(market, paper)
    adaptive = trade_count >= USE_ADAPTIVE_SIZING_MIN_TRADES

    v_score = e_score = d_factor = t_mult = k_factor = m_score = 1.0

    if adaptive:
        try:
            from risk.volatility_regime import get_volatility_regime
            regime = get_volatility_regime(symbol, market=market, funding_rate=funding_rate)
            v_score = regime['v_score']
        except Exception:
            v_score = 0.75

    if adaptive:
        try:
            from risk.edge_monitor import get_edge_score, get_edge_size_factor
            edge_data = get_edge_score(market=market, paper=paper)
            e_score = (0.50 + edge_data['edge_score']) * get_edge_size_factor(market=market, paper=paper)
        except Exception:
            e_score = 1.0

    try:
        from risk.drawdown_controller import get_heat_level
        d_factor = get_heat_level(paper=paper)['size_factor']
    except Exception:
        d_factor = 1.0

    t_mult = _get_time_of_day_multiplier(strategy)

    try:
        from risk.position_sizer import size_from_kelly
        k_raw = size_from_kelly(strategy, symbol, 1.0, confidence, paper)
        k_factor = max(0.25, min(k_raw / max(d_factor, 1e-10), 1.50))
    except Exception:
        k_factor = max(0.60, min(float(confidence), 1.0))

    if adaptive:
        final = base_size * v_score * e_score * d_factor * t_mult * k_factor * m_score
    else:
        final = base_size * d_factor * t_mult * k_factor * m_score

    final = max(MIN_POSITION_USD, min(final, base_size * MAX_POSITION_SCALE))

    return {
        'base_size':   round(base_size, 2),
        'v':           round(v_score, 4),
        'e':           round(e_score, 4),
        'd':           round(d_factor, 4),
        't':           round(t_mult, 4),
        'k':           round(k_factor, 4),
        'm':           round(m_score, 4),
        'final_size':  round(final, 2),
        'adaptive':    adaptive,
        'trade_count': trade_count,
        'market':      market,
    }
