"""
strategies/market_type_classifier.py — Forever Playbook market-type router.

Every symbol is assigned to one of six buckets that determine:
  - which tactics apply
  - spot vs perp instrument choice
  - sizing policy
  - exit doctrine

Seed table is grounded in:
  - price_archive.db 1d/4h structure (365d of data, 39 symbols)
  - trades.db clean_paper_v10 performance (229 closes, 2026-04-14)
  - scan_candidates funnel stats (6720 rows, 55+ symbols)

Dynamic reclassification is available when price_db is supplied.
This module is READ-ONLY / ADDITIVE — it does not modify any live-path behavior.
Safe to wire now.
"""

from __future__ import annotations

import math
import sqlite3
from enum import Enum
from typing import Optional


class MarketType(str, Enum):
    CARRY_MAJOR = "carry_major"
    CLEAN_TREND_ALT = "clean_trend_alt"
    EXPLOSIVE_CONVEX = "explosive_convex"
    REFLEXIVE_MEME = "reflexive_meme"
    MEAN_REVERSION = "mean_reversion"
    DO_NOT_TRADE = "do_not_trade"


# ---------------------------------------------------------------------------
# Seed classification — data-grounded 2026-04-14
# Evidence basis noted inline.
# ---------------------------------------------------------------------------
_SEED: dict[str, MarketType] = {
    # ── CARRY MAJORS ────────────────────────────────────────────────────────
    # Low-mod vol, high liquidity, perp carry eligible when funding favorable.
    # BTC: rvol=48.9%, 30d+8.9%, 4h BULL
    # ETH: rvol=70.6%, 30d+17.3%, 4h BULL
    # SOL: rvol=57.6%, 30d-11.4%, 4h BULL; carry candidate
    # BNB: rvol=67.7%, 30d+12.8%
    # XRP: rvol=36.8% — lowest vol in scanner universe; carry when BULL
    "BTC": MarketType.CARRY_MAJOR,
    "ETH": MarketType.CARRY_MAJOR,
    "SOL": MarketType.CARRY_MAJOR,
    "BNB": MarketType.CARRY_MAJOR,
    "XRP": MarketType.CARRY_MAJOR,
    # Kraken PF_ variants — same market type; governance restricts to paper-only tonight
    "PF_XBTUSD": MarketType.CARRY_MAJOR,
    "PF_ETHUSD": MarketType.CARRY_MAJOR,
    "PF_SOLUSD": MarketType.CARRY_MAJOR,
    "PF_BNBUSD": MarketType.CARRY_MAJOR,
    "PF_XRPUSD": MarketType.CARRY_MAJOR,
    # ── CLEAN TREND ALTS ─────────────────────────────────────────────────────
    # Directional, multi-timeframe coherent, moderate vol, spot-first preferred.
    # NEAR: rvol=54.6%, 4h BULL; cleaner structure than reputation
    # LINK: rvol=58.8%, 4h BULL; stronger than expected on MTF alignment
    # AVAX: rvol=62.4%, 4h BULL; spot preferred (funding historically hostile to longs)
    # MORPHO: rvol=80.6%; strong 1y+103.9% but 4h BEAR now — constrained by governance
    # TON: rvol=59%, 4h BULL; longer hold OK
    # ZEC: rvol=122% but trades.db n=11 +2.57 AND PF_ZECUSD n=14 +5.41 — consistent edge
    # XMR: rvol=58.2%; privacy coin, econ-vetoed in scanner but classifiable
    "NEAR": MarketType.CLEAN_TREND_ALT,
    "LINK": MarketType.CLEAN_TREND_ALT,
    "AVAX": MarketType.CLEAN_TREND_ALT,
    "MORPHO": MarketType.CLEAN_TREND_ALT,
    "TON": MarketType.CLEAN_TREND_ALT,
    "ZEC": MarketType.CLEAN_TREND_ALT,
    "XMR": MarketType.CLEAN_TREND_ALT,
    "RENDER": MarketType.CLEAN_TREND_ALT,
    "ADA": MarketType.CLEAN_TREND_ALT,  # 1d trend but deep drawdown; governance=CONSTRAINED
    "SUI": MarketType.CLEAN_TREND_ALT,  # strong trend but 90d-47%; governance=CONSTRAINED
    "UNI": MarketType.CLEAN_TREND_ALT,  # governance=CONSTRAINED
    "CRV": MarketType.CLEAN_TREND_ALT,  # governance=CONSTRAINED (90d-49%)
    "PENGU": MarketType.CLEAN_TREND_ALT,  # governance=CONSTRAINED
    "PF_NEARUSD": MarketType.CLEAN_TREND_ALT,
    "PF_LINKUSD": MarketType.CLEAN_TREND_ALT,
    "PF_AVAXUSD": MarketType.CLEAN_TREND_ALT,
    "PF_ZECUSD": MarketType.CLEAN_TREND_ALT,
    "PF_XMRUSD": MarketType.CLEAN_TREND_ALT,
    "PF_SUIUSD": MarketType.CLEAN_TREND_ALT,  # governance=CONSTRAINED
    "BCH": MarketType.CLEAN_TREND_ALT,
    # ── EXPLOSIVE CONVEX ALTS ────────────────────────────────────────────────
    # High vol, momentum-driven, fast confirmation only, no passive holding.
    # TAO: rvol=109.8%, 4h BEAR — trap-like; PF_TAOUSD n=11 net=-2.49
    # ENA: rvol=95.6%, 30d-15.7%, 90d-55.6%
    # LIT: rvol=120.2%; n=2 net=+2.95 (too thin)
    # WLD: rvol=109%, 30d-24%
    # ZRO: rvol=101.8%; n=3 net=+13.23 (one large win, too thin to trust)
    # AAVE: rvol=72.6%; scanner keeps econ-vetoing; structural problems
    # HYPE/PF_HYPEUSD: n=5 net=-2.59; volatile defi token
    "TAO": MarketType.EXPLOSIVE_CONVEX,
    "ENA": MarketType.EXPLOSIVE_CONVEX,
    "LIT": MarketType.EXPLOSIVE_CONVEX,
    "WLD": MarketType.EXPLOSIVE_CONVEX,
    "ZRO": MarketType.EXPLOSIVE_CONVEX,
    "LDO": MarketType.EXPLOSIVE_CONVEX,
    "ARB": MarketType.EXPLOSIVE_CONVEX,
    "FET": MarketType.EXPLOSIVE_CONVEX,  # n=7 net=-2.24; governance=CONSTRAINED
    "JUP": MarketType.EXPLOSIVE_CONVEX,
    "AAVE": MarketType.EXPLOSIVE_CONVEX,
    "XPL": MarketType.EXPLOSIVE_CONVEX,
    "DASH": MarketType.EXPLOSIVE_CONVEX,
    "JTO": MarketType.EXPLOSIVE_CONVEX,  # n=4 net=-1.77; governance=CONSTRAINED
    "HYPE": MarketType.EXPLOSIVE_CONVEX,  # n=5 net=-2.59; governance=CONSTRAINED
    "PF_TAOUSD": MarketType.EXPLOSIVE_CONVEX,
    "PF_HYPEUSD": MarketType.EXPLOSIVE_CONVEX,
    # ── REFLEXIVE / MEME / UNSTABLE ──────────────────────────────────────────
    # Default BLOCKED unless explicitly unlocked via governance.
    # TRUMP: rvol=53.6% but extremely political/sentiment driven; n=3 net=-1.96
    # FARTCOIN: rvol=122.4%, trend_eff=0.039 (pure noise)
    # PUMP: rvol=78.8%; meme coin by design
    # VIRTUAL: rvol=74.6%; AI agent narrative, reflexive
    # VVV: n=23 net=-4.15 — worst by sample; governance=BLOCKED
    "TRUMP": MarketType.REFLEXIVE_MEME,
    "WLFI": MarketType.REFLEXIVE_MEME,
    "PUMP": MarketType.REFLEXIVE_MEME,
    "VIRTUAL": MarketType.REFLEXIVE_MEME,
    "FARTCOIN": MarketType.REFLEXIVE_MEME,
    "SPX": MarketType.REFLEXIVE_MEME,
    "ASTER": MarketType.REFLEXIVE_MEME,
    "VVV": MarketType.REFLEXIVE_MEME,
    "HEMI": MarketType.REFLEXIVE_MEME,  # n=13 net=-1.01
    "MON": MarketType.REFLEXIVE_MEME,  # n=4 net=-2.46
    "POPCAT": MarketType.REFLEXIVE_MEME,
    "BERA": MarketType.REFLEXIVE_MEME,
    "KAITO": MarketType.REFLEXIVE_MEME,
    "TST": MarketType.REFLEXIVE_MEME,
    "STBL": MarketType.REFLEXIVE_MEME,
    "PROMPT": MarketType.REFLEXIVE_MEME,
    "IP": MarketType.REFLEXIVE_MEME,
    "BLUR": MarketType.REFLEXIVE_MEME,
    "GAS": MarketType.REFLEXIVE_MEME,
    "ETHFI": MarketType.REFLEXIVE_MEME,
    "ACE": MarketType.REFLEXIVE_MEME,
    "ZK": MarketType.REFLEXIVE_MEME,
    "PF_PEPEUSD": MarketType.REFLEXIVE_MEME,
    # ── MEAN-REVERSION ELIGIBLE ──────────────────────────────────────────────
    # Low-trend, ranging, adequate liquidity, fee-aware only.
    # DOGE: rvol=43.8%, trend_eff=0.130 — ranging dominant
    # PAXG: rvol=36.1% — gold-backed, ultra-stable; special carry characteristics
    # AXS: rvol=51.9%; ranging, 90d flat to down
    "DOGE": MarketType.MEAN_REVERSION,
    "PAXG": MarketType.MEAN_REVERSION,
    "AXS": MarketType.MEAN_REVERSION,
    "PF_DOGEUSD": MarketType.MEAN_REVERSION,
    # ── DO NOT TRADE ─────────────────────────────────────────────────────────
    # Duplicate tickers, structurally broken PF symbols, or systematic losers
    # with enough evidence to exclude.
    "DOT": MarketType.DO_NOT_TRADE,  # 1y-67%, 90d-45%, n=351 all econ-vetoed
    "ALGO": MarketType.DO_NOT_TRADE,  # n=11 clean_paper net=-3.06
    "BTCUSDT": MarketType.DO_NOT_TRADE,  # duplicate of BTC
    "ETHUSDT": MarketType.DO_NOT_TRADE,  # duplicate of ETH
    "PF_ADAUSD": MarketType.DO_NOT_TRADE,  # n=17 net=-6.94 — worst performer
    "PF_ALGOUSD": MarketType.DO_NOT_TRADE,
    "PF_XLMUSD": MarketType.DO_NOT_TRADE,
    "PF_GALAUSD": MarketType.DO_NOT_TRADE,
    "PF_RAVEUSD": MarketType.DO_NOT_TRADE,
    "PF_BCHUSD": MarketType.DO_NOT_TRADE,
    "PF_DASHUSD": MarketType.DO_NOT_TRADE,
    "PF_XAUTUSD": MarketType.DO_NOT_TRADE,  # gold perp — non-crypto, no doctrine
    "ALT": MarketType.DO_NOT_TRADE,
    "PAXG": MarketType.MEAN_REVERSION,  # override below — gold-backed
}

# Correct duplicate: PAXG was listed twice; keep MEAN_REVERSION
_SEED["PAXG"] = MarketType.MEAN_REVERSION


# ---------------------------------------------------------------------------
# PF_ → underlying strip helper
# ---------------------------------------------------------------------------
_PF_MAP = {
    "PF_XBTUSD": "BTC",
    "PF_ETHUSD": "ETH",
    "PF_SOLUSD": "SOL",
    "PF_NEARUSD": "NEAR",
    "PF_AVAXUSD": "AVAX",
    "PF_LINKUSD": "LINK",
    "PF_XRPUSD": "XRP",
    "PF_SUIUSD": "SUI",
    "PF_XMRUSD": "XMR",
    "PF_DOGEUSD": "DOGE",
    "PF_ZECUSD": "ZEC",
    "PF_HYPEUSD": "HYPE",
    "PF_ADAUSD": "ADA",
    "PF_TAOUSD": "TAO",
    "PF_BNBUSD": "BNB",
    "PF_ALGOUSD": "ALGO",
    "PF_XLMUSD": "XLM",
    "PF_GALAUSD": "GALA",
    "PF_RAVEUSD": "RAVE",
    "PF_BCHUSD": "BCH",
    "PF_DASHUSD": "DASH",
    "PF_XAUTUSD": "XAUT",
    "PF_PEPEUSD": "PEPE",
}


def underlying(symbol: str) -> str:
    """Strip PF_ prefix and USD suffix to get the underlying asset symbol."""
    if symbol in _PF_MAP:
        return _PF_MAP[symbol]
    if symbol.startswith("PF_") and symbol.endswith("USD"):
        return symbol[3:-3]
    return symbol


# ---------------------------------------------------------------------------
# Dynamic reclassification thresholds (from 1d price_archive.db)
# ---------------------------------------------------------------------------
_RVOL_CARRY_MAX = 55.0  # annualized; above this = not carry major
_RVOL_TREND_MAX = 80.0  # above this + low trend eff = explosive
_RVOL_EXPLOSIVE_MIN = 90.0  # clearly explosive volatility
_TREND_EFF_NOISE = 0.06  # below this = noise/reflexive
_DD90_CARRY_MAX = -30.0  # worse drawdown = not carry major


def _compute_1d_stats(symbol: str, conn: sqlite3.Connection) -> Optional[dict]:
    """
    Compute 1d structure stats from price_archive.db.
    Returns None if fewer than 30 bars available.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT close FROM ohlcv WHERE symbol=? AND timeframe='1d' ORDER BY open_time",
        (symbol,),
    )
    rows = cur.fetchall()
    closes = [r[0] for r in rows]
    n = len(closes)
    if n < 30:
        return None

    # 30d realized volatility (annualized)
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, n - 30), n)]
    rvol = (
        math.sqrt(sum(x * x for x in log_rets) / len(log_rets)) * math.sqrt(365) * 100
    )

    # Trend efficiency (30d): net directional move / total path length
    moves = [abs(closes[i] - closes[i - 1]) for i in range(max(1, n - 30), n)]
    net_move = abs(closes[-1] - closes[-30]) if n >= 30 else 0
    tot_path = sum(moves) or 1e-9
    trend_eff = net_move / tot_path

    # 90d drawdown from high
    if n >= 90:
        high90 = max(closes[-90:])
        dd90 = (closes[-1] / high90 - 1) * 100
    else:
        dd90 = None

    # 30d return
    ret30 = (closes[-1] / closes[-30] - 1) * 100 if n >= 30 else None

    return {
        "rvol30": round(rvol, 1),
        "trend_eff30": round(trend_eff, 3),
        "dd90": round(dd90, 1) if dd90 is not None else None,
        "ret30": round(ret30, 1) if ret30 is not None else None,
        "bars": n,
    }


def _dynamic_classify(stats: dict) -> Optional[MarketType]:
    """
    Classify based on computed price stats.
    Returns None if the evidence is ambiguous (fall back to seed).
    """
    rv = stats["rvol30"]
    te = stats["trend_eff30"]
    dd = stats.get("dd90")

    # Clear explosive: very high vol regardless of direction
    if rv >= _RVOL_EXPLOSIVE_MIN and te < _TREND_EFF_NOISE:
        return MarketType.REFLEXIVE_MEME

    if rv >= _RVOL_EXPLOSIVE_MIN:
        return MarketType.EXPLOSIVE_CONVEX

    # Noisy / reflexive regardless of vol level
    if te < _TREND_EFF_NOISE and rv > 60:
        return MarketType.REFLEXIVE_MEME

    # Carry major candidates: lower vol + manageable drawdown
    if rv <= _RVOL_CARRY_MAX and (dd is None or dd >= _DD90_CARRY_MAX):
        return MarketType.CARRY_MAJOR

    # Mid-range vol → trend alt if there's directional efficiency
    if rv <= _RVOL_TREND_MAX and te >= _TREND_EFF_NOISE:
        return MarketType.CLEAN_TREND_ALT

    # High but not extreme vol → explosive
    if rv > _RVOL_TREND_MAX:
        return MarketType.EXPLOSIVE_CONVEX

    return None  # ambiguous — keep seed


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def classify(
    symbol: str,
    price_db: Optional[str] = None,
    dynamic_override: bool = True,
) -> MarketType:
    """
    Return the MarketType for *symbol*.

    Args:
        symbol: ticker as seen in the scanner (e.g. "BTC", "PF_XBTUSD")
        price_db: path to price_archive.db. When supplied, dynamic stats
                  are computed and may override the seed for UNKNOWN symbols.
        dynamic_override: if False, skip dynamic computation even if price_db given.

    Returns:
        MarketType enum value. Falls back to EXPLOSIVE_CONVEX for unknowns
        (safer than assuming a carry major or trend alt).
    """
    # Exact seed lookup
    if symbol in _SEED:
        mt = _SEED[symbol]
    else:
        # Try stripping PF_ prefix and look up underlying
        base = underlying(symbol)
        if base != symbol and base in _SEED:
            mt = _SEED[base]
        else:
            mt = None

    # Dynamic stats for unknown or borderline symbols
    if price_db and dynamic_override:
        try:
            conn = sqlite3.connect(f"file:{price_db}?mode=ro", uri=True)
            # Try exact symbol then underlying
            stats = _compute_1d_stats(symbol, conn)
            if stats is None:
                stats = _compute_1d_stats(underlying(symbol), conn)
            conn.close()
            if stats is not None:
                dynamic = _dynamic_classify(stats)
                if mt is None:
                    mt = dynamic  # unknown → use dynamic
                elif mt in (MarketType.CARRY_MAJOR, MarketType.CLEAN_TREND_ALT):
                    # Demote if dynamic says it's clearly more volatile
                    if dynamic in (
                        MarketType.EXPLOSIVE_CONVEX,
                        MarketType.REFLEXIVE_MEME,
                    ):
                        mt = dynamic
        except Exception:
            pass  # DB unavailable — use seed only

    # Final fallback for completely unknown symbols
    if mt is None:
        mt = MarketType.EXPLOSIVE_CONVEX  # conservative: require explicit unlock

    return mt


def classify_many(
    symbols: list[str],
    price_db: Optional[str] = None,
) -> dict[str, MarketType]:
    """Classify a list of symbols. Returns {symbol: MarketType} dict."""
    if price_db:
        try:
            conn = sqlite3.connect(f"file:{price_db}?mode=ro", uri=True)
        except Exception:
            conn = None
    else:
        conn = None

    result = {}
    for sym in symbols:
        if sym in _SEED:
            mt = _SEED[sym]
        else:
            base = underlying(sym)
            mt = _SEED.get(base)

        if conn is not None:
            try:
                stats = _compute_1d_stats(sym, conn) or _compute_1d_stats(
                    underlying(sym), conn
                )
                if stats:
                    dyn = _dynamic_classify(stats)
                    if mt is None:
                        mt = dyn
                    elif mt in (MarketType.CARRY_MAJOR, MarketType.CLEAN_TREND_ALT):
                        if dyn in (
                            MarketType.EXPLOSIVE_CONVEX,
                            MarketType.REFLEXIVE_MEME,
                        ):
                            mt = dyn
            except Exception:
                pass

        result[sym] = mt or MarketType.EXPLOSIVE_CONVEX

    if conn is not None:
        conn.close()

    return result


def is_tradeable(market_type: MarketType) -> bool:
    """True if the market type is not DO_NOT_TRADE and not REFLEXIVE_MEME."""
    return market_type not in (MarketType.DO_NOT_TRADE, MarketType.REFLEXIVE_MEME)


def requires_explicit_unlock(market_type: MarketType) -> bool:
    """True if the bucket is blocked by default and needs governance unlock."""
    return market_type == MarketType.REFLEXIVE_MEME
