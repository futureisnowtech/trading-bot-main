"""
strategies/symbol_governance.py — Forever Playbook symbol governance registry.

Every symbol has a GovernanceStatus that controls whether it can be traded,
at what size, and in which direction. Status is determined by:
  - market type (from market_type_classifier)
  - integrity cleanliness (trust tier)
  - net after fees (from trustworthy closes)
  - price sanity stability
  - exit quality
  - timeframe coherence
  - funding friendliness
  - sample size

Launch-state ladder:
  RESEARCH      — observation only, no live or paper trades
  PAPER         — paper trading only, not live
  CONSTRAINED_LIVE — live allowed, reduced size, longs only
  SCALED_LIVE   — full live operation
  DEFENSE_MODE  — emergency: no new entries, exits only

Safe to wire now as a read-only helper.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from strategies.market_type_classifier import MarketType, classify


class GovernanceStatus(str, Enum):
    PROMOTED = "promoted"  # exceeds evidence bar; full-size allowed
    ALLOWED = "allowed"  # standard operation
    CONSTRAINED = "constrained"  # allowed but reduced size / longs only
    BLOCKED = "blocked"  # no new trades; exits existing if any
    RESEARCH_ONLY = "research_only"  # data collection only; no trades


class LaunchState(str, Enum):
    RESEARCH = "research"
    PAPER = "paper"
    CONSTRAINED_LIVE = "constrained_live"
    SCALED_LIVE = "scaled_live"
    DEFENSE_MODE = "defense_mode"


@dataclass
class SymbolPolicy:
    symbol: str
    market_type: MarketType
    governance: GovernanceStatus
    longs_allowed: bool = True
    shorts_allowed: bool = False  # default OFF per go-live audit
    max_size_pct: float = 1.0  # fraction of standard position size (1.0 = full)
    notes: str = ""

    @property
    def can_enter(self) -> bool:
        return self.governance not in (
            GovernanceStatus.BLOCKED,
            GovernanceStatus.RESEARCH_ONLY,
        )

    @property
    def can_short(self) -> bool:
        return self.can_enter and self.shorts_allowed

    @property
    def can_long(self) -> bool:
        return self.can_enter and self.longs_allowed


# ---------------------------------------------------------------------------
# Seed governance registry — grounded in trades.db 2026-04-14 evidence
# Format: symbol → (governance, longs, shorts, max_size_pct, notes)
# ---------------------------------------------------------------------------
#
# Evidence key:
#   n=X net=Y → from clean_paper_v10 SELL rows (229 total)
#   pf=Z → from price structure (30d/90d/1d)
# ---------------------------------------------------------------------------

_SEED_GOVERNANCE: dict[str, tuple[GovernanceStatus, bool, bool, float, str]] = {
    # ── PROMOTED / ALLOWED (positive evidence, adequate sample) ──────────────
    "PF_ZECUSD": (
        GovernanceStatus.PROMOTED,
        True,
        False,
        1.0,
        "n=14 net=+5.41; consistent across venues",
    ),
    "ZEC": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "n=11 net=+2.57; 30d+30.8%; high vol but directional",
    ),
    "PF_SOLUSD": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "n=6 net=+2.44; paper-only tonight",
    ),
    "PF_ETHUSD": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "n=7 net=+2.20; paper-only tonight",
    ),
    "PF_XBTUSD": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "n=4 net=+1.95; paper-only tonight",
    ),
    "PF_NEARUSD": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "n=4 net=+1.87; paper-only tonight",
    ),
    "PF_SUIUSD": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "n=3 net=+1.60; thin sample; SUI 90d-47%",
    ),
    "BTC": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "carry major; 4h BULL; perp eligible",
    ),
    "ETH": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "carry major; 4h BULL; 30d+17.3%",
    ),
    "SOL": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "carry major; 4h BULL; recent pullback watched",
    ),
    "BNB": (GovernanceStatus.ALLOWED, True, False, 1.0, "carry major; 30d+12.8%"),
    "NEAR": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "clean trend alt; 4h BULL; spot-first",
    ),
    "LINK": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "clean trend alt; 4h BULL; spot-first",
    ),
    "AVAX": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        1.0,
        "clean trend alt; 4h BULL; spot-first",
    ),
    "TON": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        0.75,
        "clean trend alt; 4h BULL but TON showed MTF issues; watch",
    ),
    "XRP": (
        GovernanceStatus.ALLOWED,
        True,
        False,
        0.75,
        "carry major; low rvol; spot-first given erratic perp funding",
    ),
    # ── CONSTRAINED (reduced size, longs only, watch closely) ────────────────
    "MORPHO": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "4h BEAR currently; strong 1y but 30d-3.4%; wait for 4h turn",
    ),
    "TAO": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "PF_TAOUSD n=11 net=-2.49; 4h BEAR; trap-prone; only if 4h BULL",
    ),
    "ENA": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "rvol=95.6%; 90d-55.6%; high risk, fast confirmation only",
    ),
    "ZRO": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "n=3 net=+13.23 — one large win, too thin; constrained until n>=10",
    ),
    "LIT": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "n=2 net=+2.95; too thin; rvol=120%; explosive only",
    ),
    "RENDER": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "n=4 net=+0.26; thin sample; watch",
    ),
    "ADA": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "ADA raw slightly positive; PF_ADAUSD blocked; 90d-39%",
    ),
    "SUI": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "90d-47.3%; deep drawdown; only long on strong structure",
    ),
    "UNI": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "90d-40.6%; no clear edge; reduced size only",
    ),
    "CRV": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "90d-49%; 1y-65.6%; extreme downtrend; only counter-trend with extreme care",
    ),
    "FET": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "n=7 net=-2.24; negative evidence; watch",
    ),
    "JTO": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "n=4 net=-1.77; negative trending; constrained",
    ),
    "XMR": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "all econ-vetoed in scanner; fee/spread problem; constrained",
    ),
    "DOGE": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.75,
        "mean-reversion eligible; 30d-6.7%; tight conditions only",
    ),
    "AXS": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "mean-reversion eligible; 90d-16%; thin evidence",
    ),
    "PF_XRPUSD": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.75,
        "paper-only; XRP low vol carry candidate",
    ),
    "PF_AVAXUSD": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "n=2 net=-5.34; very thin, alarming loss/trade; caution",
    ),
    "PF_LINKUSD": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.75,
        "paper-only; LINK is ALLOWED underlying",
    ),
    "PF_XMRUSD": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.5,
        "paper-only; XMR scanner-blocked",
    ),
    "HEMI": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.25,
        "n=13 net=-1.01; negative; near-blocked",
    ),
    "PENGU": (
        GovernanceStatus.CONSTRAINED,
        True,
        False,
        0.25,
        "rvol=79.9%; 90d-43%; meme-adjacent; minimum size if any",
    ),
    # ── BLOCKED ──────────────────────────────────────────────────────────────
    "PF_ADAUSD": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "n=17 net=-6.94 — worst performer; systematic loser",
    ),
    "ALGO": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "n=11 net=-3.06; systematic loser",
    ),
    "VVV": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "n=23 net=-4.15; worst by sample size; systematic loser",
    ),
    "PF_TAOUSD": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "n=11 net=-2.49; TAO constrained; PF version blocked",
    ),
    "HYPE": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "n=5 net=-2.59; go-live audit watch-list; negative evidence",
    ),
    "DOT": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "1y-67.2%; 90d-45%; systematic loser; all econ-vetoed",
    ),
    "MON": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "n=4 net=-2.46; high loss/trade",
    ),
    "TRUMP": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "reflexive/meme; n=3 net=-1.96; sentiment driven",
    ),
    "WLFI": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "reflexive/meme; 90d-52%; political token",
    ),
    "PUMP": (GovernanceStatus.BLOCKED, False, False, 0.0, "reflexive/meme by design"),
    "VIRTUAL": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "reflexive/meme; AI narrative token",
    ),
    "FARTCOIN": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "reflexive/meme; rvol=122%; trend_eff=0.039 (pure noise)",
    ),
    "SPX": (GovernanceStatus.BLOCKED, False, False, 0.0, "reflexive meme token"),
    "ASTER": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "meme/reflexive; thin history",
    ),
    "BERA": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "new chain reflexive; n=3 net=+2.11 too thin to trust",
    ),
    "KAITO": (
        GovernanceStatus.BLOCKED,
        False,
        False,
        0.0,
        "reflexive/narrative; too thin",
    ),
    "POPCAT": (GovernanceStatus.BLOCKED, False, False, 0.0, "meme; n=1 net=-2.08"),
    "STBL": (GovernanceStatus.BLOCKED, False, False, 0.0, "meme; n=1 net=-0.33"),
    "PROMPT": (GovernanceStatus.BLOCKED, False, False, 0.0, "meme; n=3 net=-0.72"),
    # ── RESEARCH ONLY ────────────────────────────────────────────────────────
    "BTCUSDT": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "duplicate ticker; use BTC",
    ),
    "ETHUSDT": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "duplicate ticker; use ETH",
    ),
    "PF_ALGOUSD": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "broken PF symbol",
    ),
    "PF_XLMUSD": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "broken PF symbol",
    ),
    "PF_GALAUSD": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "broken PF symbol",
    ),
    "PF_RAVEUSD": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "broken PF symbol",
    ),
    "PF_BCHUSD": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "broken PF symbol",
    ),
    "PF_DASHUSD": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "broken PF symbol",
    ),
    "PF_XAUTUSD": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "gold perp; non-crypto; no doctrine",
    ),
    "PF_PEPEUSD": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "meme PF symbol"),
    "TST": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "too thin"),
    "ALT": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "too thin"),
    "ZK": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "too thin"),
    "BCH": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "thin data"),
    "PAXG": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "gold-backed; mean-reversion only; no perp doctrine yet",
    ),
    "DASH": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "rvol=125%; trend_eff=0.065; noise",
    ),
    "XPL": (
        GovernanceStatus.RESEARCH_ONLY,
        False,
        False,
        0.0,
        "rvol=120%; high vol, thin evidence",
    ),
    "BLUR": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "thin"),
    "GAS": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "thin"),
    "ETHFI": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "thin"),
    "ACE": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "thin"),
    "IP": (GovernanceStatus.RESEARCH_ONLY, False, False, 0.0, "thin"),
}


def get_policy(
    symbol: str,
    price_db: Optional[str] = None,
) -> SymbolPolicy:
    """
    Return the SymbolPolicy for *symbol*.

    For symbols not in the seed, policy is derived from market type:
      - DO_NOT_TRADE → BLOCKED
      - REFLEXIVE_MEME → BLOCKED
      - EXPLOSIVE_CONVEX → CONSTRAINED (50% size, longs only)
      - MEAN_REVERSION → CONSTRAINED (75% size, longs only)
      - CLEAN_TREND_ALT → ALLOWED (longs only)
      - CARRY_MAJOR → ALLOWED (longs only)
    """
    mt = classify(symbol, price_db=price_db)

    if symbol in _SEED_GOVERNANCE:
        gov, longs, shorts, max_size, notes = _SEED_GOVERNANCE[symbol]
    else:
        # Derive from market type
        if mt in (MarketType.DO_NOT_TRADE, MarketType.REFLEXIVE_MEME):
            gov, longs, shorts, max_size, notes = (
                GovernanceStatus.BLOCKED,
                False,
                False,
                0.0,
                f"auto: {mt.value}",
            )
        elif mt == MarketType.EXPLOSIVE_CONVEX:
            gov, longs, shorts, max_size, notes = (
                GovernanceStatus.CONSTRAINED,
                True,
                False,
                0.5,
                f"auto: {mt.value}; fast confirm required",
            )
        elif mt == MarketType.MEAN_REVERSION:
            gov, longs, shorts, max_size, notes = (
                GovernanceStatus.CONSTRAINED,
                True,
                False,
                0.75,
                f"auto: {mt.value}; tight fee gate",
            )
        else:
            gov, longs, shorts, max_size, notes = (
                GovernanceStatus.ALLOWED,
                True,
                False,
                1.0,
                f"auto: {mt.value}",
            )

    return SymbolPolicy(
        symbol=symbol,
        market_type=mt,
        governance=gov,
        longs_allowed=longs,
        shorts_allowed=shorts,
        max_size_pct=max_size,
        notes=notes,
    )


def get_policies(
    symbols: list[str],
    price_db: Optional[str] = None,
) -> dict[str, SymbolPolicy]:
    """Bulk policy lookup."""
    return {s: get_policy(s, price_db=price_db) for s in symbols}


# ---------------------------------------------------------------------------
# Evidence-based update helpers (called by nightly audit / post-trade loop)
# These do NOT modify the seed — they surface recommendations only.
# ---------------------------------------------------------------------------


def evaluate_governance_update(
    symbol: str,
    n_trades: int,
    net_pnl: float,
    expectancy: float,
    current_status: GovernanceStatus,
) -> Optional[tuple[GovernanceStatus, str]]:
    """
    Given real trade evidence, return a suggested governance update or None.

    Rules:
      PROMOTE: n>=15 AND expectancy>0.30 AND net>0 → PROMOTED
      ALLOW: n>=8 AND expectancy>0.05 → ALLOWED (from CONSTRAINED)
      CONSTRAIN: n>=5 AND expectancy<-0.20 → CONSTRAINED (from ALLOWED/PROMOTED)
      BLOCK: n>=8 AND expectancy<-0.30 → BLOCKED
    """
    if n_trades < 3:
        return None

    if n_trades >= 15 and expectancy > 0.30 and net_pnl > 0:
        if current_status != GovernanceStatus.PROMOTED:
            return (
                GovernanceStatus.PROMOTED,
                f"n={n_trades} exp={expectancy:+.2f} net={net_pnl:+.2f}",
            )

    if n_trades >= 8 and expectancy > 0.05:
        if current_status == GovernanceStatus.CONSTRAINED:
            return (GovernanceStatus.ALLOWED, f"n={n_trades} exp={expectancy:+.2f}")

    if n_trades >= 8 and expectancy < -0.30:
        if current_status in (GovernanceStatus.ALLOWED, GovernanceStatus.PROMOTED):
            return (
                GovernanceStatus.BLOCKED,
                f"n={n_trades} exp={expectancy:+.2f} net={net_pnl:+.2f}",
            )

    if n_trades >= 5 and expectancy < -0.20:
        if current_status in (GovernanceStatus.ALLOWED, GovernanceStatus.PROMOTED):
            return (GovernanceStatus.CONSTRAINED, f"n={n_trades} exp={expectancy:+.2f}")

    return None


# ---------------------------------------------------------------------------
# Launch-state ladder (system-level, not per-symbol)
# ---------------------------------------------------------------------------

LAUNCH_STATE_RULES = {
    LaunchState.RESEARCH: {
        "description": "Observation only. No live or paper trades.",
        "live_allowed": False,
        "paper_allowed": False,
        "shorts_allowed": False,
        "max_size_pct": 0.0,
    },
    LaunchState.PAPER: {
        "description": "Paper trading only. No live money.",
        "live_allowed": False,
        "paper_allowed": True,
        "shorts_allowed": True,
        "max_size_pct": 1.0,
    },
    LaunchState.CONSTRAINED_LIVE: {
        "description": "Live with hard limits. Longs only. Reduced size.",
        "live_allowed": True,
        "paper_allowed": True,
        "shorts_allowed": False,
        "max_size_pct": 0.5,
        "max_positions": 4,
        "max_bankroll_deployed": 0.70,
        "notes": "Go-live audit status: AMBER. Tonight's $500 operating mode.",
    },
    LaunchState.SCALED_LIVE: {
        "description": "Full live operation. All buckets per governance.",
        "live_allowed": True,
        "paper_allowed": True,
        "shorts_allowed": True,
        "max_size_pct": 1.0,
        "notes": "Requires: n>=50 trustworthy closes, expectancy>0.15, profit_factor>1.4",
    },
    LaunchState.DEFENSE_MODE: {
        "description": "Emergency. No new entries. Exits only.",
        "live_allowed": False,
        "paper_allowed": False,
        "shorts_allowed": False,
        "max_size_pct": 0.0,
        "notes": "Triggered by: kill switch, large drawdown, API errors, risk breach.",
    },
}
