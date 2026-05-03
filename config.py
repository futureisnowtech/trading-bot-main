"""
config.py — Single source of truth. All values from .env.
Never hardcode anything that belongs here.
"""

import os
from datetime import time as dt_time

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(dotenv_path: str | None = None) -> bool:
        """Minimal .env loader fallback for audit scripts on hosts without python-dotenv."""
        path = dotenv_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if (
                    len(value) >= 2
                    and value[0] == value[-1]
                    and value[0] in {"'", '"'}
                ):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
        return True

load_dotenv()

# ════════════════════════════════════════════════════════════════════
# SYSTEM MODE
# ════════════════════════════════════════════════════════════════════
PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"
LIVE_TRADING: bool = not PAPER_TRADING

# Session start: all performance stats (win rate, P&L, trade counts) are
# measured from this date forward. Old trades are kept in DB for ML training
# but excluded from dashboard metrics and Kelly/sizing decisions.
# Reset this when making significant system changes so stale data doesn't
# pollute current performance signals.
TRADE_SESSION_START: str = os.getenv("TRADE_SESSION_START", "2026-03-28")

# ════════════════════════════════════════════════════════════════════
# ACCOUNT
# ════════════════════════════════════════════════════════════════════
ACCOUNT_SIZE: float = float(os.getenv("ACCOUNT_SIZE", "5000"))
MAX_DEPLOYED_PCT: float = 0.90
CASH_RESERVE_PCT: float = 0.10

# ════════════════════════════════════════════════════════════════════
# SYMBOL SUPPRESSION (v13.4 — forensic audit 2026-04-08)
# Symbols with statistically negative edge over 10+ clean trades.
# Review quarterly — remove a symbol when Bayesian WR recovers > 55%.
# ════════════════════════════════════════════════════════════════════
SUPPRESSED_SYMBOLS: set = {
    "PF_TAOUSD",  # 40% WR, -$4.31 net (10 trades)
    "HYPE",  # 20% WR, -$2.59 net (5 trades)
    "ALGO",  # 67% WR but -$2.41 net — win/loss ratio broken
    "PF_ADAUSD",  # 58% WR but -$2.40 net (12 trades, small wins/large losses)
    "MON",  # 33% WR, -$2.14 net (3 trades — too thin to trust, suppress until 10+)
    "PF_PEPEUSD",  # 17% WR, -$0.36 net (6 trades)
}

# ════════════════════════════════════════════════════════════════════
# EXECUTION UNIVERSE (v16.3)
# Core underlyings = the ACTUAL live-broker-supported Coinbase nano futures set.
# Live scanner / runner / manual scan should focus only on names we can
# truly execute right now. Add an underlying here only after live broker
# support exists.
# ════════════════════════════════════════════════════════════════════
CORE_EXECUTION_UNDERLYINGS: set = {
    "BTC",  # BIP-20DEC30-CDE — live Coinbase nano futures
    "ETH",  # ETP-20DEC30-CDE — live Coinbase nano futures
    "SOL",  # SLP-20DEC30-CDE — live Coinbase nano futures
    "XRP",  # XPP-20DEC30-CDE — live Coinbase nano futures
}

# ════════════════════════════════════════════════════════════════════
# AUTONOMOUS LIVE PERP SYMBOLS (v16.11)
# Only symbols where contract_min < PERP_MAX_TRADE_PCT * live account
# are safe for autonomous (bot-initiated) live entries.
# ETH min ~$233 < 15% of $1,966 (~$295) → passes.
# All four Coinbase nano perp symbols are eligible for autonomous live entry.
# Position sizing is account-relative (12% cap) so contracts that exceed safe
# allocation will be sized to minimum or skipped by the broker naturally.
# CORE_EXECUTION_UNDERLYINGS stays [BTC,ETH,SOL,XRP] for manual + research.
# ════════════════════════════════════════════════════════════════════
AUTONOMOUS_LIVE_PERP_SYMBOLS: list = os.getenv(
    "AUTONOMOUS_LIVE_PERP_SYMBOLS", "BTC,ETH,SOL,XRP"
).split(",")

# ════════════════════════════════════════════════════════════════════
# SPOT LANE (v16.11)
# Coinbase spot lane — no leverage, no shorting, no margin.
# SPOT_LANE_ACTIVE gates the lane; default false = disabled.
# ════════════════════════════════════════════════════════════════════
SPOT_LANE_ACTIVE: bool = os.getenv("SPOT_LANE_ACTIVE", "true").lower() == "true"
SPOT_SYMBOLS: list = [
    s.strip().upper()
    for s in os.getenv("SPOT_SYMBOLS", "BTC,ETH,SOL,XRP,LTC,DOGE,ADA,LINK").split(",")
    if s.strip()
]
SPOT_STRATEGY_SYMBOLS: list = [
    s.strip().upper()
    for s in os.getenv(
        "SPOT_STRATEGY_SYMBOLS", "BTC,ETH,SOL,XRP,LTC,DOGE,ADA,LINK"
    ).split(",")
    if s.strip()
]
SPOT_MAX_DEPLOYED_PCT: float = float(os.getenv("SPOT_MAX_DEPLOYED_PCT", "0.50"))
SPOT_MIN_ORDER_USD: float = float(os.getenv("SPOT_MIN_ORDER_USD", "10.0"))
SPOT_WEEKDAYS_ONLY: bool = os.getenv("SPOT_WEEKDAYS_ONLY", "false").lower() == "true"
SPOT_ENTRY_START_TIME: str = os.getenv("SPOT_ENTRY_START_TIME", "00:00")
SPOT_ENTRY_END_TIME: str = os.getenv("SPOT_ENTRY_END_TIME", "23:59")
# 1.5% - Tightened from 3% to reduce capital drag and improve net R:R in high-fee environments.
SPOT_STOP_PCT: float = float(os.getenv("SPOT_STOP_PCT", "0.015"))
# Profit target expressed as a multiple of the stop distance (R-multiple).
# 3.0 = 3R: with a 1.5% stop, target = 4.5% gain. This ensures winners clear fee drag.
SPOT_TARGET_R: float = float(os.getenv("SPOT_TARGET_R", "3.0"))
# End-of-day flatten time (HH:MM ET, 24h). All spot positions closed at or
# after this time on weekdays to prevent overnight gap exposure.
SPOT_EOD_CLOSE_TIME: str = os.getenv("SPOT_EOD_CLOSE_TIME", "15:45")
SPOT_EOD_FLATTEN_ENABLED: bool = (
    os.getenv("SPOT_EOD_FLATTEN_ENABLED", "false").lower() == "true"
)
SPOT_THESIS_MIN_HOLD_MINS: float = float(os.getenv("SPOT_THESIS_MIN_HOLD_MINS", "8.0"))
SPOT_THESIS_MIN_SCORE: float = float(os.getenv("SPOT_THESIS_MIN_SCORE", "52.0"))
SPOT_SESSION_MIN_EDGE_MULT: float = float(
    os.getenv("SPOT_SESSION_MIN_EDGE_MULT", "1.5")
)
SPOT_OFFSESSION_MIN_EDGE_MULT: float = float(
    os.getenv("SPOT_OFFSESSION_MIN_EDGE_MULT", "2.0")
)
SPOT_TOTAL_ALLOC_CAP_PCT: float = float(os.getenv("SPOT_TOTAL_ALLOC_CAP_PCT", "0.50"))
SPOT_EXIT_POLL_SECONDS: int = int(os.getenv("SPOT_EXIT_POLL_SECONDS", "5"))
SPOT_SCALP_SCAN_SECONDS: int = int(os.getenv("SPOT_SCALP_SCAN_SECONDS", "60"))
SPOT_STATE_CACHE_SECONDS: int = int(os.getenv("SPOT_STATE_CACHE_SECONDS", "45"))
SPOT_MAKER_WAIT_SECONDS: int = int(os.getenv("SPOT_MAKER_WAIT_SECONDS", "6"))
SPOT_MAKER_POLL_SECONDS: int = int(os.getenv("SPOT_MAKER_POLL_SECONDS", "2"))
SPOT_FRAME_SCORE_ANCHOR: float = float(os.getenv("SPOT_FRAME_SCORE_ANCHOR", "55.0"))
SPOT_MOMENTUM_IMPULSE_WINDOW: int = int(os.getenv("SPOT_MOMENTUM_IMPULSE_WINDOW", "12"))
SPOT_ACCEL_IMPULSE_WINDOW: int = int(os.getenv("SPOT_ACCEL_IMPULSE_WINDOW", "8"))
SPOT_MICROSTRUCTURE_MAX_SPREAD_PCT: float = float(
    os.getenv("SPOT_MICROSTRUCTURE_MAX_SPREAD_PCT", "0.0025")
)
SPOT_MICROSTRUCTURE_MIN_DEPTH_USD: float = float(
    os.getenv("SPOT_MICROSTRUCTURE_MIN_DEPTH_USD", "5000")
)
SPOT_SCALP_SCORE_WEIGHT_COMPOSITE: float = float(
    os.getenv("SPOT_SCALP_SCORE_WEIGHT_COMPOSITE", "0.80")
)
SPOT_SCALP_SCORE_WEIGHT_DERIVATIVE: float = float(
    os.getenv("SPOT_SCALP_SCORE_WEIGHT_DERIVATIVE", "0.20")
)
SPOT_NEUTRAL_SCORE_WEIGHT_COMPOSITE: float = float(
    os.getenv("SPOT_NEUTRAL_SCORE_WEIGHT_COMPOSITE", "0.90")
)
SPOT_NEUTRAL_SCORE_WEIGHT_DERIVATIVE: float = float(
    os.getenv("SPOT_NEUTRAL_SCORE_WEIGHT_DERIVATIVE", "0.10")
)
SPOT_REGIME_SCORE_FLOORS: dict[str, float] = {
    "TREND": float(os.getenv("SPOT_TREND_SCORE_FLOOR", "55.0")),
    "NEUTRAL": float(os.getenv("SPOT_NEUTRAL_SCORE_FLOOR", "55.0")),
    "CHOP": float(os.getenv("SPOT_CHOP_SCORE_FLOOR", "60.0")),
}
SPOT_ALLOWED_REGIMES: set[str] = {
    s.strip().upper()
    for s in os.getenv("SPOT_ALLOWED_REGIMES", "TREND,NEUTRAL").split(",")
    if s.strip()
}
SPOT_MIN_PATH_EFFICIENCY: float = float(os.getenv("SPOT_MIN_PATH_EFFICIENCY", "0.20"))
SPOT_TARGET_R_BY_REGIME: dict[str, float] = {
    "TREND": float(os.getenv("SPOT_TREND_TARGET_R", "4.0")),
    "NEUTRAL": float(os.getenv("SPOT_NEUTRAL_TARGET_R", "3.0")),
    "CHOP": float(os.getenv("SPOT_CHOP_TARGET_R", "3.0")),
}
SPOT_TRAIL_ARM_R_BY_REGIME: dict[str, float] = {
    "TREND": float(os.getenv("SPOT_TREND_TRAIL_ARM_R", "1.5")),
    "NEUTRAL": float(os.getenv("SPOT_NEUTRAL_TRAIL_ARM_R", "1.0")),
    "CHOP": float(os.getenv("SPOT_CHOP_TRAIL_ARM_R", "1.0")),
}
SPOT_EXIT_PROFILE_TARGETS: dict[str, dict[str, tuple[float, float]]] = {
    "balanced": {
        "TREND": (4.0, 1.5),
        "NEUTRAL": (3.0, 1.0),
        "CHOP": (2.0, 0.8),
    },
    "quick": {
        "TREND": (3.5, 1.5),
        "NEUTRAL": (2.5, 1.0),
        "CHOP": (1.8, 0.7),
    },
    "precision": {
        "TREND": (3.0, 1.2),
        "NEUTRAL": (2.0, 0.8),
        "CHOP": (1.5, 0.6),
    },
    "micro": {
        "TREND": (2.5, 1.0),
        "NEUTRAL": (1.8, 0.7),
        "CHOP": (1.2, 0.5),
    },
    "nano": {
        "TREND": (2.0, 0.8),
        "NEUTRAL": (1.5, 0.6),
        "CHOP": (1.0, 0.4),
    },
}
SPOT_SYMBOL_STRATEGY_OVERRIDES: dict[str, dict] = {
    "BTC": {
        "allowed_regimes": ("TREND", "NEUTRAL", "CHOP"),
        "allowed_setups": (
            "impulse_continuation",
            "pullback_reclaim",
            "compression_breakout",
            "trend_resume_after_shakeout",
            "compression_expansion_retest",
        ),
        "preferred_setups": ("compression_breakout",),
        "edge_profile": "quick",
        "edge_metrics": {
            "n": 29,
            "wr": 0.3448,
            "pf": 1.6751,
            "exp": 0.000573,
            "net": 0.01663,
            "score": 8.797,
        },
        "opportunistic_setup_score": 0.72,
        "wildcard_setup_score": 0.82,
        "score_floors": {"TREND": 57.0, "NEUTRAL": 57.0, "CHOP": 60.0},
        "score_weights": {
            "TREND": {"composite": 0.70, "derivative": 0.30},
            "NEUTRAL": {"composite": 0.88, "derivative": 0.12},
            "CHOP": {"composite": 0.62, "derivative": 0.38},
        },
        "min_confirm_count": 0,
        "min_5m_frame": 0.0,
        "min_30m_frame": 48.0,
        "min_momentum_impulse": -1.0,
        "min_structure_component": -1.0,
        "min_path_efficiency": 0.0,
        "min_participation_component": -1.0,
        "min_volatility_quality": -1.0,
    },
    "ETH": {
        "allowed_regimes": ("TREND", "NEUTRAL", "CHOP"),
        "allowed_setups": (
            "impulse_continuation",
            "pullback_reclaim",
            "compression_breakout",
            "trend_resume_after_shakeout",
            "compression_expansion_retest",
        ),
        "preferred_setups": (),
        "edge_profile": "quick",
        "edge_metrics": {
            "n": 47,
            "wr": 0.3830,
            "pf": 1.7722,
            "exp": 0.001168,
            "net": 0.054873,
            "score": 12.3816,
        },
        "opportunistic_setup_score": 0.72,
        "wildcard_setup_score": 0.82,
        "score_floors": {"TREND": 55.0, "NEUTRAL": 55.0, "CHOP": 60.0},
        "score_weights": {
            "TREND": {"composite": 0.72, "derivative": 0.28},
            "NEUTRAL": {"composite": 0.86, "derivative": 0.14},
            "CHOP": {"composite": 0.64, "derivative": 0.36},
        },
        "min_confirm_count": 0,
        "min_5m_frame": 0.0,
        "min_30m_frame": 48.0,
        "min_momentum_impulse": -1.0,
        "min_structure_component": -1.0,
        "min_path_efficiency": 0.0,
        "min_participation_component": -1.0,
        "min_volatility_quality": -1.0,
    },
    "SOL": {
        "allowed_regimes": ("TREND", "NEUTRAL", "CHOP"),
        "allowed_setups": (
            "impulse_continuation",
            "pullback_reclaim",
            "compression_breakout",
            "trend_resume_after_shakeout",
            "compression_expansion_retest",
        ),
        "preferred_setups": (),
        "edge_profile": "balanced",
        "edge_metrics": {
            "n": 45,
            "wr": 0.4000,
            "pf": 1.9427,
            "exp": 0.001357,
            "net": 0.061048,
            "score": 14.5696,
        },
        "opportunistic_setup_score": 0.76,
        "wildcard_setup_score": 0.86,
        "score_floors": {"TREND": 56.0, "NEUTRAL": 56.0, "CHOP": 60.0},
        "score_weights": {
            "TREND": {"composite": 0.76, "derivative": 0.24},
            "NEUTRAL": {"composite": 0.82, "derivative": 0.18},
            "CHOP": {"composite": 0.66, "derivative": 0.34},
        },
        "min_confirm_count": 0,
        "min_5m_frame": 0.0,
        "min_30m_frame": 50.0,
        "min_momentum_impulse": -1.0,
        "min_structure_component": -1.0,
        "min_path_efficiency": 0.0,
        "min_participation_component": -1.0,
        "min_volatility_quality": -1.0,
    },
    "XRP": {
        "allowed_regimes": ("TREND", "NEUTRAL", "CHOP"),
        "allowed_setups": (
            "impulse_continuation",
            "pullback_reclaim",
            "compression_breakout",
            "trend_resume_after_shakeout",
            "compression_expansion_retest",
        ),
        "preferred_setups": (),
        "edge_profile": "micro",
        "edge_metrics": {
            "n": 24,
            "wr": 0.4167,
            "pf": 2.6119,
            "exp": 0.001644,
            "net": 0.039448,
            "score": 20.7380,
        },
        "opportunistic_setup_score": 0.74,
        "wildcard_setup_score": 0.84,
        "score_floors": {"TREND": 56.0, "NEUTRAL": 56.0, "CHOP": 60.0},
        "score_weights": {
            "TREND": {"composite": 0.74, "derivative": 0.26},
            "NEUTRAL": {"composite": 0.84, "derivative": 0.16},
            "CHOP": {"composite": 0.64, "derivative": 0.36},
        },
        "min_confirm_count": 0,
        "min_5m_frame": 0.0,
        "min_30m_frame": 50.0,
        "min_momentum_impulse": -1.0,
        "min_structure_component": -1.0,
        "min_path_efficiency": 0.0,
        "min_participation_component": -1.0,
        "min_volatility_quality": -1.0,
    },
    "LTC": {
        "allowed_regimes": ("TREND", "NEUTRAL", "CHOP"),
        "allowed_setups": (
            "impulse_continuation",
            "pullback_reclaim",
            "compression_breakout",
            "trend_resume_after_shakeout",
            "compression_expansion_retest",
        ),
        "preferred_setups": ("impulse_continuation",),
        "edge_profile": "balanced",
        "edge_metrics": {
            "n": 12,
            "wr": 0.5000,
            "pf": 3.2662,
            "exp": 0.001732,
            "net": 0.020781,
            "score": 26.3576,
        },
        "opportunistic_setup_score": 0.72,
        "wildcard_setup_score": 0.82,
        "score_floors": {"TREND": 55.0, "NEUTRAL": 55.0, "CHOP": 60.0},
        "score_weights": {
            "TREND": {"composite": 0.72, "derivative": 0.28},
            "NEUTRAL": {"composite": 0.86, "derivative": 0.14},
            "CHOP": {"composite": 0.64, "derivative": 0.36},
        },
        "min_confirm_count": 0,
        "min_5m_frame": 0.0,
        "min_30m_frame": 50.0,
        "min_momentum_impulse": -1.0,
        "min_structure_component": -1.0,
        "min_path_efficiency": 0.0,
        "min_participation_component": -1.0,
        "min_volatility_quality": -1.0,
    },
    "DOGE": {
        "allowed_regimes": ("TREND", "NEUTRAL", "CHOP"),
        "allowed_setups": (
            "impulse_continuation",
            "pullback_reclaim",
            "compression_breakout",
            "trend_resume_after_shakeout",
            "compression_expansion_retest",
        ),
        "preferred_setups": ("impulse_continuation",),
        "edge_profile": "balanced",
        "edge_metrics": {
            "n": 19,
            "wr": 0.4737,
            "pf": 5.6269,
            "exp": 0.002809,
            "net": 0.053366,
            "score": 44.6058,
        },
        "opportunistic_setup_score": 0.78,
        "wildcard_setup_score": 0.88,
        "score_floors": {"TREND": 57.0, "NEUTRAL": 57.0, "CHOP": 60.0},
        "score_weights": {
            "TREND": {"composite": 0.78, "derivative": 0.22},
            "NEUTRAL": {"composite": 0.84, "derivative": 0.16},
            "CHOP": {"composite": 0.66, "derivative": 0.34},
        },
        "min_confirm_count": 0,
        "min_5m_frame": 0.0,
        "min_30m_frame": 50.0,
        "min_momentum_impulse": -1.0,
        "min_structure_component": -1.0,
        "min_path_efficiency": 0.0,
        "min_participation_component": -1.0,
        "min_volatility_quality": -1.0,
    },
    "ADA": {
        "allowed_regimes": ("TREND", "NEUTRAL", "CHOP"),
        "allowed_setups": (
            "impulse_continuation",
            "pullback_reclaim",
            "compression_breakout",
            "trend_resume_after_shakeout",
            "compression_expansion_retest",
        ),
        "preferred_setups": ("impulse_continuation",),
        "edge_profile": "balanced",
        "edge_metrics": {
            "n": 12,
            "wr": 0.3333,
            "pf": 3.0753,
            "exp": 0.001569,
            "net": 0.018832,
            "score": 23.5116,
        },
        "opportunistic_setup_score": 0.76,
        "wildcard_setup_score": 0.86,
        "score_floors": {"TREND": 55.0, "NEUTRAL": 55.0, "CHOP": 60.0},
        "score_weights": {
            "TREND": {"composite": 0.76, "derivative": 0.24},
            "NEUTRAL": {"composite": 0.82, "derivative": 0.18},
            "CHOP": {"composite": 0.66, "derivative": 0.34},
        },
        "min_confirm_count": 0,
        "min_5m_frame": 0.0,
        "min_30m_frame": 50.0,
        "min_momentum_impulse": -1.0,
        "min_structure_component": -1.0,
        "min_path_efficiency": 0.0,
        "min_participation_component": -1.0,
        "min_volatility_quality": -1.0,
    },
    "LINK": {
        "allowed_regimes": ("TREND", "NEUTRAL", "CHOP"),
        "allowed_setups": (
            "impulse_continuation",
            "pullback_reclaim",
            "compression_breakout",
            "trend_resume_after_shakeout",
            "compression_expansion_retest",
        ),
        "preferred_setups": ("compression_breakout",),
        "edge_profile": "balanced",
        "edge_metrics": {
            "n": 18,
            "wr": 0.4444,
            "pf": 2.8022,
            "exp": 0.001080,
            "net": 0.019446,
            "score": 19.9652,
        },
        "opportunistic_setup_score": 0.73,
        "wildcard_setup_score": 0.83,
        "score_floors": {"TREND": 55.0, "NEUTRAL": 55.0, "CHOP": 60.0},
        "score_weights": {
            "TREND": {"composite": 0.70, "derivative": 0.30},
            "NEUTRAL": {"composite": 0.88, "derivative": 0.12},
            "CHOP": {"composite": 0.62, "derivative": 0.38},
        },
        "min_confirm_count": 0,
        "min_5m_frame": 0.0,
        "min_30m_frame": 50.0,
        "min_momentum_impulse": -1.0,
        "min_structure_component": -1.0,
        "min_path_efficiency": 0.0,
        "min_participation_component": -1.0,
        "min_volatility_quality": -1.0,
    },
}
SPOT_REPLAY_LOOKBACK_DAYS: int = int(os.getenv("SPOT_REPLAY_LOOKBACK_DAYS", "365"))
SPOT_REPLAY_EVAL_TIMEFRAME: str = os.getenv("SPOT_REPLAY_EVAL_TIMEFRAME", "30m")
SPOT_REPLAY_OBJECTIVE: str = os.getenv(
    "SPOT_REPLAY_OBJECTIVE", "net_expectancy_per_trade"
)

SPOT_SCALP_SYMBOL_CONFIG: dict[str, dict[str, float | int]] = {
    "BTC": {
        "stop_floor_pct": 0.008,
        "stop_cap_pct": 0.012,
        "risk_fraction": 0.0030,
        "allocation_cap_pct": 0.20,
        "spread_cap_pct": 0.0010,
        "depth_min_usd": 0,
        "cooldown_min": 10,
        "symbol_k": 1.05,
    },
    "ETH": {
        "stop_floor_pct": 0.010,
        "stop_cap_pct": 0.014,
        "risk_fraction": 0.0025,
        "allocation_cap_pct": 0.15,
        "spread_cap_pct": 0.0012,
        "depth_min_usd": 0,
        "cooldown_min": 12,
        "symbol_k": 1.10,
    },
    "SOL": {
        "stop_floor_pct": 0.013,
        "stop_cap_pct": 0.018,
        "risk_fraction": 0.0018,
        "allocation_cap_pct": 0.07,
        "spread_cap_pct": 0.0018,
        "depth_min_usd": 0,
        "cooldown_min": 15,
        "symbol_k": 1.15,
    },
    "XRP": {
        "stop_floor_pct": 0.014,
        "stop_cap_pct": 0.020,
        "risk_fraction": 0.0015,
        "allocation_cap_pct": 0.05,
        "spread_cap_pct": 0.0022,
        "depth_min_usd": 0,
        "cooldown_min": 18,
        "symbol_k": 1.18,
    },
    "LTC": {
        "stop_floor_pct": 0.010,
        "stop_cap_pct": 0.015,
        "risk_fraction": 0.0018,
        "allocation_cap_pct": 0.08,
        "spread_cap_pct": 0.0016,
        "depth_min_usd": 0,
        "cooldown_min": 12,
        "symbol_k": 1.12,
    },
    "DOGE": {
        "stop_floor_pct": 0.015,
        "stop_cap_pct": 0.022,
        "risk_fraction": 0.0015,
        "allocation_cap_pct": 0.05,
        "spread_cap_pct": 0.0025,
        "depth_min_usd": 0,
        "cooldown_min": 18,
        "symbol_k": 1.20,
    },
    "ADA": {
        "stop_floor_pct": 0.013,
        "stop_cap_pct": 0.019,
        "risk_fraction": 0.0015,
        "allocation_cap_pct": 0.05,
        "spread_cap_pct": 0.0022,
        "depth_min_usd": 0,
        "cooldown_min": 16,
        "symbol_k": 1.16,
    },
    "LINK": {
        "stop_floor_pct": 0.013,
        "stop_cap_pct": 0.019,
        "risk_fraction": 0.0018,
        "allocation_cap_pct": 0.07,
        "spread_cap_pct": 0.0020,
        "depth_min_usd": 0,
        "cooldown_min": 15,
        "symbol_k": 1.15,
    },
}

# ════════════════════════════════════════════════════════════════════
# RISK — HARDCODED. NO AI CAN OVERRIDE THESE.
# Paper mode uses looser limits to maximise learning velocity.
# Live mode uses tight limits to protect real capital.
# ════════════════════════════════════════════════════════════════════
MAX_RISK_PER_TRADE_PCT: float = 0.01  # 1% of account per trade

# Daily loss halt: paper = 20% (don't halt learning), live = 4%
MAX_DAILY_LOSS_PCT: float = 0.20 if PAPER_TRADING else 0.04

# Max open positions: paper = wide open for learning, live = conservative
MAX_POSITIONS_EQUITY: int = 10 if PAPER_TRADING else 3
MAX_POSITIONS_CRYPTO: int = (
    20 if PAPER_TRADING else 5
)  # all 20 pairs can run simultaneously

# Daily trade caps: paper = uncapped, live = PDT compliance
MAX_TRADES_PER_DAY_EQUITY: int = 999 if PAPER_TRADING else 3
MAX_TRADES_PER_DAY_CRYPTO: int = 999  # Effectively unlimited in both modes

CRYPTO_MIN_PROFIT_FEE_MULTIPLE: float = (
    1.0  # Take-profit must clear 1.0x round-trip fees
)
EQUITY_STOP_LOSS_PCT: float = 0.025  # was 0.05, cut 50%
EQUITY_TAKE_PROFIT_PCT: float = 0.075  # was 0.15, cut 50% — maintains 3:1 R:R
EQUITY_RSI_OVERSOLD: int = 35  # kept for exit signals only (not entry gate)
EQUITY_RSI_OVERBOUGHT: int = 70  # kept for exit signals only (not entry gate)
CRYPTO_STOP_LOSS_PCT: float = 0.015  # was 0.03, cut 50%
CRYPTO_TAKE_PROFIT_PCT: float = 0.045  # was 0.09, cut 50% — maintains 3:1 R:R
CRYPTO_RSI_OVERSOLD: int = 35  # kept for exit signals only (not entry gate)
CRYPTO_RSI_OVERBOUGHT: int = 70  # kept for exit signals only (not entry gate)
CRYPTO_MIN_ADX: float = 15.0
# 3-variant MACD params — must match backtested values (crypto_macd.py docstring)
# Workhorse: MACD(3/15/3) — trades every signal, high frequency
# Classic:   MACD(4/16/3) — line vs signal crossover, slightly lower frequency
# Sniper:    MACD(6/20/5) — strong momentum only, highest win rate (63.7%)
CRYPTO_MACD1_FAST: int = 3
CRYPTO_MACD1_SLOW: int = 15
CRYPTO_MACD1_SIGNAL: int = 3
CRYPTO_MACD2_FAST: int = 4
CRYPTO_MACD2_SLOW: int = 16
CRYPTO_MACD2_SIGNAL: int = 3
CRYPTO_MACD3_FAST: int = 6
CRYPTO_MACD3_SLOW: int = 20
CRYPTO_MACD3_SIGNAL: int = 5
CRYPTO_MACD3_HISTOGRAM_THRESHOLD: float = 0.0
# Coinbase nano perp-style futures fees (Advanced Trade API direct, promotional)
COINBASE_TAKER_FEE_PCT: float = 0.0003  # 0.03% taker — Coinbase perp futures
COINBASE_MAKER_FEE_PCT: float = (
    0.0000  # 0.00% maker — Coinbase perp futures (promotional)
)
MAX_DAILY_FEE_DRAG_PCT: float = (
    0.50 if PAPER_TRADING else 0.10
)  # paper: fees never halt learning; live: 10% cap
MARKET_BREADTH_MIN_SPY_PCT: float = (
    -2.0
)  # Block equity longs if SPY down more than this
BACKTEST_SLIPPAGE_PCT: float = float(
    os.getenv("BACKTEST_SLIPPAGE_PCT", "0.001")
)  # 0.1% per leg slippage added to commission
MAX_STRATEGY_LOSS_STREAK: int = (
    99 if PAPER_TRADING else 4
)  # paper: never pause on streak; live: 4-loss circuit breaker
EQUITY_MAX_HOLD_HOURS: float = (
    6.0  # Close equity position if flat after this many hours
)
CRYPTO_MAX_HOLD_HOURS: float = (
    12.0  # Close crypto position if flat after this many hours
)
FLAT_POSITION_THRESHOLD_PCT: float = 0.015  # Position is "flat" if P&L within ±1.5%
CRYPTO_MIN_HOLD_MINUTES: int = (
    3  # Min hold before strategy SELL fires (prevents same-candle $0.00 exits)
)

# ── ATR-based exit multipliers (Dennis Turtle / deep research) ───────────────
ATR_STOP_MULTIPLIER: float = 2.0  # Stop = 2×ATR below entry
ATR_TARGET_MULTIPLIER: float = 4.0  # Target = 4×ATR above entry (2:1 R:R)
ATR_FEE_FLOOR_PCT: float = (
    0.004  # Min ATR/price to clear 2.4% round-trip (skip debate if below)
)

# ── Advanced math signal thresholds (deep-research-backed) ──────────────────
SQUEEZE_MIN_BARS: int = 20  # BB-Keltner squeeze must be on ≥20 bars before it fires
RV_EXPANSION_THRESHOLD: float = (
    1.3  # RV ratio ≥ 1.3 = short vol > long vol = expansion regime
)
RV_COMPRESSION_THRESHOLD: float = (
    0.8  # RV ratio ≤ 0.8 = compressed vol = mean-reversion preferred
)
OBI_ACTIONABLE_THRESHOLD: float = 0.20  # OBI ≥ 0.20 = actionable buy pressure
OBI_STRONG_THRESHOLD: float = 0.35  # OBI ≥ 0.35 = strong buy pressure
OU_HALFLIFE_MIN_MINUTES: float = (
    3.0  # Min OU half-life to be tradeable (shorter = noise)
)
OU_HALFLIFE_MAX_MINUTES: float = (
    60.0  # Max OU half-life (longer = too slow for 1-min bars)
)
KALMAN_ENTRY_DEV_PCT: float = -1.0  # Enter when price ≥1% below Kalman estimate
AVWAP_ENTRY_DEV_PCT: float = -0.5  # Enter when price ≥0.5% below AVWAP (reclaim setup)
KYLE_LAMBDA_LOW_PCT: float = 30.0  # Kyle lambda ≤ 30th pct = liquid market, good fills

# ════════════════════════════════════════════════════════════════════
# AI — ANTHROPIC
# ════════════════════════════════════════════════════════════════════
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CRYPTOPANIC_API_KEY: str = os.getenv(
    "CRYPTOPANIC_API_KEY", ""
)  # Free tier at cryptopanic.com/developers/api/

# Reddit sentiment (PRAW — optional; graceful fallback if missing)
# Get free credentials: reddit.com/prefs/apps → create app (script type)
REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT: str = os.getenv("REDDIT_USER_AGENT", "AlgoTradingBot/1.0")
CLAUDE_MODEL: str = "claude-sonnet-4-6"  # Always latest
CLAUDE_MODEL_EXTENDED: str = "claude-sonnet-4-6"  # For exit extended thinking
CLAUDE_DEBATE_MODEL: str = os.getenv(
    "CLAUDE_DEBATE_MODEL", "claude-haiku-4-5-20251001"
)  # Debate agents (cheap)
DEBATE_MAX_TOKENS: int = 700  # Raised from 300 — agents need room to reason deeply
EXIT_REVIEW_MAX_TOKENS: int = (
    1500  # Raised from 800 — exit reasoning is the most critical decision
)
MODERATOR_MAX_TOKENS: int = 900  # CIO synthesis
# 3-agent debate — same agents for quick and full (no distinction needed)
# funding_regime: macro + funding rate (crypto-native edge)
# momentum_structure: ADX + squeeze + WAE + WaveTrend + MACD
# risk_economics: fee math + ATR + volume + time-of-day gate
QUICK_DEBATE_AGENTS: list = ["funding_regime", "momentum_structure", "risk_economics"]
FULL_DEBATE_AGENTS: list = ["funding_regime", "momentum_structure", "risk_economics"]
MES_DEBATE_AGENTS: list = ["mes_momentum_risk", "mes_quant", "mes_market_structure"]
FULL_DEBATE_MIN_AGREEMENT: float = (
    0.20 if PAPER_TRADING else 0.60
)  # paper: any 1/3 agent BUY = BUY

# ML signal gate — skip debate if P(win) below threshold
# Calibrated to seeded data baseline (~9% WR from math-only backtest).
# Once live trades accumulate (AI-filtered ~30-50% WR), raise this to 0.35+
# In .env: set ML_SIGNAL_MIN_PROB=0.35 after 50+ real trades
ML_SIGNAL_MIN_PROB: float = float(os.getenv("ML_SIGNAL_MIN_PROB", "0.08"))

# Funding rate signal thresholds (Coinglass per-8h %)
FUNDING_OVERHEATED_PCT: float = 0.0005  # > this = longs overloaded (Binance decimal: 0.0001 = 0.01%/8h; 0.05% = overheated)
FUNDING_FAVORABLE_PCT: float = 0.0001  # < this = low crowding, good long entry window

# Auto-tuning thresholds (AI switches debate depth based on account + win rate)
AUTO_TUNE_FULL_DEBATE_THRESHOLD: float = float(
    os.getenv("AUTO_TUNE_FULL_DEBATE_THRESHOLD", "1000.0")
)  # Account > $1000 → always full debate
AUTO_TUNE_WIN_RATE_THRESHOLD: float = 0.55  # Win rate > 55% → upgrade debate depth

# ════════════════════════════════════════════════════════════════════
# BROKERS
# ════════════════════════════════════════════════════════════════════
WEBULL_USERNAME: str = os.getenv("WEBULL_USERNAME", "")
WEBULL_PASSWORD: str = os.getenv("WEBULL_PASSWORD", "")
WEBULL_TRADE_PIN: str = os.getenv("WEBULL_TRADE_PIN", "")
WEBULL_MFA: str = os.getenv("WEBULL_MFA", "")
WEBULL_DEVICE_ID: str = os.getenv("WEBULL_DEVICE_ID", "algo_bot_001")

# Legacy Coinbase spot API keys (unused in v10 — CDP JWT auth used instead)
COINBASE_API_KEY: str = os.getenv("COINBASE_API_KEY", "")
COINBASE_API_SECRET: str = os.getenv("COINBASE_API_SECRET", "")

# ── Coinbase Developer Platform (CDP) JWT credentials — live crypto execution ──
# Required for live mode. Paper mode does not call the API.
# Key format: COINBASE_CDP_KEY_NAME = organizations/{org_id}/apiKeys/{key_id}
# Key format: COINBASE_CDP_PRIVATE_KEY = EC PEM (\\n-escaped in .env)
COINBASE_CDP_KEY_NAME: str = os.getenv("COINBASE_CDP_KEY_NAME", "")
COINBASE_CDP_PRIVATE_KEY: str = os.getenv("COINBASE_CDP_PRIVATE_KEY", "")
UPTIME_PING_URL: str = os.getenv("UPTIME_PING_URL", "")  # UptimeRobot heartbeat URL

EQUITY_ENABLED: bool = os.getenv("EQUITY_ENABLED", "true").lower() == "true"
CRYPTO_ENABLED: bool = os.getenv("CRYPTO_ENABLED", "true").lower() == "true"
FUTURES_ENABLED: bool = os.getenv("FUTURES_ENABLED", "false").lower() == "true"
PERP_ENABLED: bool = os.getenv("PERP_ENABLED", "false").lower() == "true"

# Lane activation + operator-governance flags (v17.3)
# *_LANE_ACTIVE controls whether the runner is started.
# *_DASHBOARD_VISIBLE controls whether the lane gets an explicit operator surface.
# *_AUTONOMOUS_ENABLED controls whether the lane is allowed to place autonomous trades.
# *_MANUAL_ENABLED controls whether the lane is allowed to place manual trades.
# This separates visibility from autonomy so side lanes can stay promotion-ready
# without competing with the primary crypto workflow.
FUTURES_LANE_ACTIVE: bool = os.getenv("FUTURES_LANE_ACTIVE", "false").lower() == "true"
FORECAST_LANE_ACTIVE: bool = (
    os.getenv("FORECAST_LANE_ACTIVE", "false").lower() == "true"
)
FORECAST_DASHBOARD_VISIBLE: bool = (
    os.getenv("FORECAST_DASHBOARD_VISIBLE", "false").lower() == "true"
)
FORECAST_AUTONOMOUS_ENABLED: bool = (
    os.getenv("FORECAST_AUTONOMOUS_ENABLED", "false").lower() == "true"
)
FORECAST_MANUAL_ENABLED: bool = (
    os.getenv("FORECAST_MANUAL_ENABLED", "false").lower() == "true"
)
FUTURES_DASHBOARD_VISIBLE: bool = (
    os.getenv("FUTURES_DASHBOARD_VISIBLE", "false").lower() == "true"
)
FUTURES_AUTONOMOUS_ENABLED: bool = (
    os.getenv("FUTURES_AUTONOMOUS_ENABLED", "false").lower() == "true"
)
FUTURES_MANUAL_ENABLED: bool = (
    os.getenv("FUTURES_MANUAL_ENABLED", "false").lower() == "true"
)

# ── Stocks lane (v17.2) ───────────────────────────────────────────────────────
# STOCKS_LANE_ACTIVE — gates US equity swing-trading lane runner startup.
# Connects to IBKR TWS live account on port 7496, clientId=4.
STOCKS_LANE_ACTIVE: bool = os.getenv("STOCKS_LANE_ACTIVE", "false").lower() == "true"
STOCKS_DASHBOARD_VISIBLE: bool = (
    os.getenv("STOCKS_DASHBOARD_VISIBLE", "false").lower() == "true"
)
STOCKS_AUTONOMOUS_ENABLED: bool = (
    os.getenv("STOCKS_AUTONOMOUS_ENABLED", "false").lower() == "true"
)
STOCKS_MANUAL_ENABLED: bool = (
    os.getenv("STOCKS_MANUAL_ENABLED", "false").lower() == "true"
)
STOCK_UNIVERSE: list = [
    s.strip()
    for s in os.getenv(
        "STOCK_UNIVERSE",
        "AMD,GOOGL,AAPL,AMZN,TSLA,COIN,IWM,XLF,XLE,XLK,NFLX,UBER",
    ).split(",")
    if s.strip()
]
STOCKS_MAX_POSITIONS: int = int(os.getenv("STOCKS_MAX_POSITIONS", "3"))
STOCKS_RISK_PCT: float = float(os.getenv("STOCKS_RISK_PCT", "0.02"))
STOCKS_MAX_POSITION_PCT: float = float(os.getenv("STOCKS_MAX_POSITION_PCT", "0.15"))

# ── IBKR connection (shared across MES and ForecastEx lanes) ─────────────────
IBKR_HOST: str = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT: int = int(os.getenv("IBKR_PORT", "7497"))  # 7497=paper, 7496=live

# ── Binance USD-M perpetual futures (replaced Bybit, Sprint 1 overhaul) ──────
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET: bool = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
PERP_PAIRS: list = os.getenv("PERP_PAIRS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT").split(",")
PERP_POSITION_SIZE_USD: float = float(
    os.getenv("PERP_POSITION_SIZE_USD", "50")
)  # was 100, cut 50%
PERP_MAX_LEVERAGE: int = int(os.getenv("PERP_MAX_LEVERAGE", "10"))  # was 20, cut 50%
PERP_MAX_POSITIONS: int = int(os.getenv("PERP_MAX_POSITIONS", "2"))  # was 3, cut 50%
PERP_STOP_PCT: float = float(os.getenv("PERP_STOP_PCT", "0.008"))  # was 0.015, cut 50%
PERP_TAKE_PROFIT_PCT: float = float(
    os.getenv("PERP_TAKE_PROFIT_PCT", "0.016")
)  # was 0.03, cut 50%, maintains 2:1
BINANCE_TAKER_FEE_PCT: float = 0.00040  # 0.040% taker (USD-M futures standard tier)
BINANCE_MAKER_FEE_PCT: float = 0.00020  # 0.020% maker
BINANCE_SPOT_MAKER_FEE_PCT: float = float(
    os.getenv("BINANCE_SPOT_MAKER_FEE_PCT", "0.001")
)  # legacy v9 spot; unused in v10 (perps only)

# ── Mean-reversion strategy (ranging / volatile regimes) ─────────────────────
MEAN_REVERSION_ENABLED: bool = (
    os.getenv("MEAN_REVERSION_ENABLED", "true").lower() == "true"
)
MEAN_REVERSION_RSI_ENTRY: float = float(os.getenv("MEAN_REVERSION_RSI_ENTRY", "33"))
MEAN_REVERSION_ADX_MAX: float = float(os.getenv("MEAN_REVERSION_ADX_MAX", "22"))

# ── Fade-the-rally (SHORT overbought in ranging/volatile regimes) ─────────────
FADE_ENABLED: bool = os.getenv("FADE_ENABLED", "true").lower() == "true"

# ── Range scalper (ultra-flat ADX < 15 + tight BBs — buy range support) ──────
RANGE_SCALPER_ENABLED: bool = (
    os.getenv("RANGE_SCALPER_ENABLED", "true").lower() == "true"
)
TRADOVATE_USERNAME: str = os.getenv("TRADOVATE_USERNAME", "")
TRADOVATE_PASSWORD: str = os.getenv("TRADOVATE_PASSWORD", "")
TRADOVATE_APP_ID: str = os.getenv("TRADOVATE_APP_ID", "")
TRADOVATE_APP_VERSION: str = os.getenv("TRADOVATE_APP_VERSION", "1.0")
TRADOVATE_DEVICE_ID: str = os.getenv("TRADOVATE_DEVICE_ID", "algo_bot_001")
FUTURES_CONTRACT: str = "MES"
FUTURES_NUM_CONTRACTS: int = int(
    os.getenv("FUTURES_NUM_CONTRACTS", "2")
)  # was 3, cut 50% → 2 MES = ~$40 risk/trade
FUTURES_DAILY_GOAL_PTS: float = (
    8.0  # 8pts × 2 contracts × $5 = $80/day target (was $180)
)
FUTURES_DAILY_MAX_LOSS_PTS: float = (
    5.0  # was 10pts, cut 50% — 5pts × 2 contracts × $5 = $50 max daily loss
)
FUTURES_MAX_TRADES_DAY: int = 10

# ════════════════════════════════════════════════════════════════════
# MARKET & INSTRUMENTS
# ════════════════════════════════════════════════════════════════════
CRYPTO_PAIRS: list = os.getenv(
    "CRYPTO_PAIRS",
    "BTC-USDC,ETH-USDC,SOL-USDC,XRP-USDC",
).split(",")
CRYPTO_CANDLE_GRANULARITY: str = (
    "FIVE_MINUTE"  # v5.0 Sprint 2: 5-min bars (was ONE_MINUTE)
)
EQUITY_MIN_PRICE: float = 1.00
EQUITY_MAX_PRICE: float = 200.00
EQUITY_MIN_VOLUME: int = 500_000
EQUITY_MIN_DOLLAR_VOLUME: float = 1_000_000
EQUITY_VOLUME_SPIKE_MULTIPLIER: float = 1.5
EQUITY_POSITION_SIZE_USD: float = float(os.getenv("EQUITY_POSITION_SIZE_USD", "75"))
CRYPTO_POSITION_SIZE_USD: float = float(os.getenv("CRYPTO_POSITION_SIZE_USD", "500"))

MARKET_TIMEZONE: str = "America/New_York"
MARKET_OPEN: dt_time = dt_time(9, 30)
MARKET_CLOSE: dt_time = dt_time(16, 0)
NO_TRADE_UNTIL: dt_time = dt_time(10, 0)

# ════════════════════════════════════════════════════════════════════
# SCHEDULER INTERVALS
# ════════════════════════════════════════════════════════════════════
EQUITY_SCAN_INTERVAL_SECONDS: int = 60
CRYPTO_SCAN_INTERVAL_SECONDS: int = (
    300  # matches v10_runner 5-minute scan_and_trade loop
)
FUTURES_SCAN_INTERVAL_SECONDS: int = 60
POSITION_MONITOR_INTERVAL_SECONDS: int = 30
WATCHDOG_INTERVAL_SECONDS: int = 900  # Alert if no scan in 15 min
LABELER_INTERVAL_MINUTES: int = int(os.getenv("LABELER_INTERVAL_MINUTES", "60"))
ML_RETRAIN_MIN_HOURS: int = int(os.getenv("ML_RETRAIN_MIN_HOURS", "24"))
ML_RETRAIN_MIN_NEW_CLEAN_TRADES: int = int(
    os.getenv("ML_RETRAIN_MIN_NEW_CLEAN_TRADES", "20")
)
RBI_MIN_DAYS: int = int(os.getenv("RBI_MIN_DAYS", "7"))
RBI_MIN_NEW_CLEAN_TRADES: int = int(os.getenv("RBI_MIN_NEW_CLEAN_TRADES", "20"))
RBI_SCHEDULE_MODE: str = (
    os.getenv("RBI_SCHEDULE_MODE", "weekly_or_threshold").strip().lower()
)
RBI_WEEKDAY: str = os.getenv("RBI_WEEKDAY", "SUN").strip().upper()
RBI_TIME_UTC: str = os.getenv("RBI_TIME_UTC", "07:00").strip()
NIGHTLY_AUDIT_RUN_PROOF: bool = (
    os.getenv("NIGHTLY_AUDIT_RUN_PROOF", "false").lower() == "true"
)
NIGHTLY_AUDIT_FULL_PROOF_WEEKDAY: str = (
    os.getenv("NIGHTLY_AUDIT_FULL_PROOF_WEEKDAY", "SUN").strip().upper()
)
NIGHTLY_AUDIT_TIME_UTC: str = os.getenv("NIGHTLY_AUDIT_TIME_UTC", "08:00").strip()
HEDGE_MIN_NOTIONAL_USD: float = float(os.getenv("HEDGE_MIN_NOTIONAL_USD", "100.0"))

# ════════════════════════════════════════════════════════════════════
# SCANNER THROTTLES
# ════════════════════════════════════════════════════════════════════
SCANNER_TOP_N: int = int(os.getenv("SCANNER_TOP_N", "20"))
SCANNER_PARALLEL_WORKERS: int = int(os.getenv("SCANNER_PARALLEL_WORKERS", "8"))

# ════════════════════════════════════════════════════════════════════
# TRADINGVIEW WEBHOOK INTEGRATION
# ════════════════════════════════════════════════════════════════════
TV_SIGNALS_ENABLED: bool = os.getenv("TV_SIGNALS_ENABLED", "true").lower() == "true"
TV_WEBHOOK_PORT: int = int(os.getenv("TV_WEBHOOK_PORT", "8765"))
TV_WEBHOOK_SECRET: str = os.getenv("TV_WEBHOOK_SECRET", "")
TV_SIGNAL_PROFILE_NAME: str = os.getenv(
    "TV_SIGNAL_PROFILE_NAME", "algobot_htf_v2"
).strip()
TV_SIGNAL_INDICATOR_NAME: str = os.getenv(
    "TV_SIGNAL_INDICATOR_NAME", "AlgoBot HTF Confluence Engine v2"
).strip()
TV_SIGNAL_MODE: str = os.getenv("TV_SIGNAL_MODE", "monitor_only").strip().lower()
TV_REQUIRE_SCANNER_CONFIRMATION: bool = (
    os.getenv("TV_REQUIRE_SCANNER_CONFIRMATION", "true").lower() == "true"
)
TV_PROMOTE_SYNTHETIC_CANDIDATES: bool = (
    os.getenv("TV_PROMOTE_SYNTHETIC_CANDIDATES", "false").lower() == "true"
)
TV_SIGNAL_BOOST_CONVICTION: int = int(os.getenv("TV_SIGNAL_BOOST_CONVICTION", "0"))
TV_SIGNAL_MAX_AGE_SECONDS: int = int(
    os.getenv("TV_SIGNAL_MAX_AGE_SECONDS", "14400")
)  # 4h — matches the 4H candle duration; signal stays valid until next candle closes
TV_ALLOWED_UNDERLYINGS: list[str] = [
    s.strip().upper()
    for s in os.getenv(
        "TV_ALLOWED_UNDERLYINGS", "BTC,ETH,SOL,XRP,LTC,DOGE,ADA,LINK"
    ).split(",")
    if s.strip()
]
TV_BLOCK_ON_HTF_SHORT: bool = (
    os.getenv("TV_BLOCK_ON_HTF_SHORT", "true").lower() == "true"
)
TV_BLOCK_ON_HTF_CLOSE: bool = (
    os.getenv("TV_BLOCK_ON_HTF_CLOSE", "true").lower() == "true"
)
TV_HTF_TIMEFRAME_MINUTES: int = int(os.getenv("TV_HTF_TIMEFRAME_MINUTES", "240"))
TV_HTF_SUPERTREND_ATR_MULTIPLIER: float = float(
    os.getenv("TV_HTF_SUPERTREND_ATR_MULTIPLIER", "3.0")
)
TV_HTF_SUPERTREND_ATR_PERIOD: int = int(os.getenv("TV_HTF_SUPERTREND_ATR_PERIOD", "10"))
TV_HTF_WAVETREND_CHANNEL: int = int(os.getenv("TV_HTF_WAVETREND_CHANNEL", "10"))
TV_HTF_WAVETREND_AVG: int = int(os.getenv("TV_HTF_WAVETREND_AVG", "21"))
TV_HTF_WAVETREND_OB: float = float(os.getenv("TV_HTF_WAVETREND_OB", "58"))
TV_HTF_WAVETREND_OS: float = float(os.getenv("TV_HTF_WAVETREND_OS", "-58"))
TV_HTF_VOLUME_FILTER_ENABLED: bool = (
    os.getenv("TV_HTF_VOLUME_FILTER_ENABLED", "true").lower() == "true"
)
TV_HTF_MIN_ATR_PCT: float = float(os.getenv("TV_HTF_MIN_ATR_PCT", "0.5"))
TV_HTF_MAX_ATR_PCT: float = float(os.getenv("TV_HTF_MAX_ATR_PCT", "8.0"))

# ════════════════════════════════════════════════════════════════════
# SPOT GOVERNANCE / DEPLOYMENT SAFETY
# ════════════════════════════════════════════════════════════════════
SPOT_FAILURE_WINDOW_START: str = os.getenv(
    "SPOT_FAILURE_WINDOW_START", "2026-04-22T21:36:39.390822+00:00"
).strip()
SPOT_GOV_WINDOW_DAYS: int = int(os.getenv("SPOT_GOV_WINDOW_DAYS", "30"))
SPOT_GOV_MIN_CLUSTER_TRADES: int = int(os.getenv("SPOT_GOV_MIN_CLUSTER_TRADES", "5"))
SPOT_GOV_CONFIDENT_TRADES: int = int(os.getenv("SPOT_GOV_CONFIDENT_TRADES", "20"))
SPOT_GOV_HIGH_CONF_TRADES: int = int(os.getenv("SPOT_GOV_HIGH_CONF_TRADES", "50"))
SPOT_GOV_SETUP_QUARANTINE_MIN_TRADES: int = int(
    os.getenv("SPOT_GOV_SETUP_QUARANTINE_MIN_TRADES", "20")
)
SPOT_GOV_SYMBOL_PROBATION_MIN_TRADES: int = int(
    os.getenv("SPOT_GOV_SYMBOL_PROBATION_MIN_TRADES", "10")
)
SPOT_GOV_ROUTE_DISABLE_MIN_TRADES: int = int(
    os.getenv("SPOT_GOV_ROUTE_DISABLE_MIN_TRADES", "5")
)
SPOT_GOV_MIN_EXPECTED_NET_PNL: float = float(
    os.getenv("SPOT_GOV_MIN_EXPECTED_NET_PNL", "0.0")
)
SPOT_GOV_MAX_THESIS_DECAY_RATE: float = float(
    os.getenv("SPOT_GOV_MAX_THESIS_DECAY_RATE", "0.60")
)
SPOT_GOV_MIN_FAST_FOLLOW_RATE: float = float(
    os.getenv("SPOT_GOV_MIN_FAST_FOLLOW_RATE", "0.25")
)
SPOT_GOV_MIN_PROFIT_FACTOR: float = float(
    os.getenv("SPOT_GOV_MIN_PROFIT_FACTOR", "1.00")
)
SPOT_TINY_LIVE_MAX_CONCURRENT: int = int(
    os.getenv("SPOT_TINY_LIVE_MAX_CONCURRENT", "5")
)
SPOT_TINY_LIVE_MAX_POSITION_USD: float = float(
    os.getenv("SPOT_TINY_LIVE_MAX_POSITION_USD", "50.0")
)
SPOT_TINY_LIVE_ALLOWED_ROUTE: str = (
    os.getenv("SPOT_TINY_LIVE_ALLOWED_ROUTE", "maker_only").strip().lower()
)
# Conviction floor for quarantined setup families. If a setup is quarantined, 
# it must clear this higher score to be tradeable.
SPOT_QUARANTINE_OVERRIDE_SCORE: float = float(
    os.getenv("SPOT_QUARANTINE_OVERRIDE_SCORE", "72.0")
)
SPOT_TINY_LIVE_ENABLEMENT_CONFIRMED: bool = os.getenv(
    "SPOT_TINY_LIVE_ENABLEMENT_CONFIRMED", "false"
).strip().lower() in ("true", "1", "yes")
SPOT_EXTERNAL_MANUAL_HOLDINGS: list[str] = [
    s.strip().upper()
    for s in os.getenv(
        "SPOT_EXTERNAL_MANUAL_HOLDINGS", "BTC,ETH,LTC,SOL,XRP,ADA,MANA,CLOV,STETH"
    ).split(",")
    if s.strip()
]
SPOT_ALLOWED_SETUP_FAMILIES_TINY_LIVE: tuple[str, ...] = (
    "impulse_continuation",
    "compression_breakout",
    "trend_resume_after_shakeout",
    "compression_expansion_retest",
)
SPOT_DISABLED_SETUP_FAMILIES_TINY_LIVE: tuple[str, ...] = ("pullback_reclaim",)
SPOT_TINY_LIVE_MIN_CONFIRMS: dict[str, int] = {"TREND": 2, "NEUTRAL": 3, "CHOP": 99}
SPOT_TINY_LIVE_MIN_5M_FRAME: dict[str, float] = {
    "TREND": 52.0,
    "NEUTRAL": 55.0,
    "CHOP": 99.0,
}
SPOT_TINY_LIVE_MIN_30M_FRAME: dict[str, float] = {
    "TREND": 55.0,
    "NEUTRAL": 58.0,
    "CHOP": 99.0,
}
SPOT_TINY_LIVE_MIN_STRUCTURE_COMPONENT: dict[str, float] = {
    "TREND": 0.000001,
    "NEUTRAL": 0.0,
    "CHOP": 999.0,
}
SPOT_TINY_LIVE_MIN_PARTICIPATION_COMPONENT: dict[str, float] = {
    "TREND": -999.0,
    "NEUTRAL": 0.000001,
    "CHOP": 999.0,
}
SPOT_TINY_LIVE_MIN_MOMENTUM_IMPULSE: dict[str, float] = {
    "TREND": 0.000001,
    "NEUTRAL": 0.000001,
    "CHOP": 999.0,
}
SPOT_TINY_LIVE_SCORE_FLOORS: dict[str, float] = {
    "TREND": 58.0,
    "NEUTRAL": 57.0,
    "CHOP": 99.0,
}
SPOT_TINY_LIVE_SCORE_WEIGHTS: dict[str, dict[str, float]] = {
    "TREND": {"composite": 1.0, "derivative": 0.0},
    "NEUTRAL": {"composite": 1.0, "derivative": 0.0},
    "CHOP": {"composite": 1.0, "derivative": 0.0},
}
SPOT_TINY_LIVE_EXIT_PROFILE_BY_REGIME: dict[str, str] = {
    "TREND": "precision",
    "NEUTRAL": "micro",
    "CHOP": "nano",
}
SPOT_STOP_MATRIX_VERSION: str = os.getenv(
    "SPOT_STOP_MATRIX_VERSION", "spot_stop_matrix_2026_04_28_v1"
).strip()
SPOT_STOP_TIGHTEN_NEUTRAL: float = float(os.getenv("SPOT_STOP_TIGHTEN_NEUTRAL", "0.92"))
SPOT_STOP_TIGHTEN_CHOP: float = float(os.getenv("SPOT_STOP_TIGHTEN_CHOP", "0.88"))
SPOT_STOP_TIGHTEN_PULLBACK: float = float(
    os.getenv("SPOT_STOP_TIGHTEN_PULLBACK", "0.90")
)
SPOT_STOP_TIGHTEN_TAKER: float = float(os.getenv("SPOT_STOP_TIGHTEN_TAKER", "0.90"))
SPOT_STOP_TIGHTEN_LOW_SETUP: float = float(
    os.getenv("SPOT_STOP_TIGHTEN_LOW_SETUP", "0.90")
)
SPOT_STOP_TIGHTEN_WEAK_HTF: float = float(
    os.getenv("SPOT_STOP_TIGHTEN_WEAK_HTF", "0.95")
)
# Evidence-based setup quarantine (derived from 140-trade live failure window 2026-04-22):
# pullback_reclaim NEUTRAL: n=115, 0% WR, avg -$1.28 — quarantined
# pullback_reclaim CHOP:    n=22,  0% WR, avg -$0.70 — quarantined (insufficient sample for positive case)
SPOT_PULLBACK_RECLAIM_NEUTRAL_BLOCKED: bool = os.getenv(
    "SPOT_PULLBACK_RECLAIM_NEUTRAL_BLOCKED", "true"
).strip().lower() in ("true", "1", "yes")
SPOT_PULLBACK_RECLAIM_CHOP_BLOCKED: bool = os.getenv(
    "SPOT_PULLBACK_RECLAIM_CHOP_BLOCKED", "true"
).strip().lower() in ("true", "1", "yes")
# Taker fallback disabled: all 113 taker trades in failure window were losers ($-131, 0% WR).
# Maker-only policy: if maker order does not fill within SPOT_MAKER_WAIT_SECONDS, cancel and skip.
SPOT_TAKER_FALLBACK_ENABLED: bool = os.getenv(
    "SPOT_TAKER_FALLBACK_ENABLED", "false"
).strip().lower() in ("true", "1", "yes")
# Loss-cluster kill switch thresholds (KS10)
SPOT_KS_DAILY_LOSS_PCT: float = float(os.getenv("SPOT_KS_DAILY_LOSS_PCT", "0.02"))
SPOT_KS_CONSECUTIVE_LOSSES: int = int(os.getenv("SPOT_KS_CONSECUTIVE_LOSSES", "4"))
SPOT_KS_ROLLING_LOSSES_10: int = int(
    os.getenv("SPOT_KS_ROLLING_LOSSES_10", "3")
)  # max net-negative R over last 10

# ════════════════════════════════════════════════════════════════════
# ALERTS
# v10: Telegram removed. All alerts go to SQLite system_events and are
# displayed on the dashboard Notifications panel.
# TELEGRAM_* kept here for config compatibility but have no effect.
# ════════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")  # unused in v10
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")  # unused in v10

# ════════════════════════════════════════════════════════════════════
# LANE 3 — PREDICTION MARKETS (Polymarket + Kalshi)
# LEGACY (v9): scanner files moved to legacy/lane3/. Kept here so .env
# keys don't cause config errors if present. All flags default to false.
# ════════════════════════════════════════════════════════════════════
LANE3_ENABLED: bool = os.getenv("LANE3_ENABLED", "false").lower() == "true"
POLYMARKET_ENABLED: bool = os.getenv("POLYMARKET_ENABLED", "false").lower() == "true"
KALSHI_ENABLED: bool = os.getenv("KALSHI_ENABLED", "false").lower() == "true"
POLYMARKET_PAPER: bool = os.getenv("POLYMARKET_PAPER", "true").lower() == "true"
KALSHI_PAPER: bool = os.getenv("KALSHI_PAPER", "true").lower() == "true"

# Polymarket (Polygon CLOB — requires crypto wallet for live trading)
POLYMARKET_PRIVATE_KEY: str = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_API_KEY: str = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET: str = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_API_PASSPHRASE: str = os.getenv("POLYMARKET_API_PASSPHRASE", "")
POLYMARKET_CHAIN_ID: int = int(
    os.getenv("POLYMARKET_CHAIN_ID", "137")
)  # Polygon mainnet

# Kalshi (CFTC-regulated, USD-direct — demo.kalshi.co for paper)
KALSHI_API_KEY: str = os.getenv("KALSHI_API_KEY", "")
KALSHI_API_SECRET: str = os.getenv("KALSHI_API_SECRET", "")

# Market selection filters
PM_MIN_VOLUME_USD: float = float(
    os.getenv("PM_MIN_VOLUME_USD", "10000")
)  # min $10k/day volume
PM_MAX_POSITION_USD: float = float(
    os.getenv("PM_MAX_POSITION_USD", "25")
)  # max $25 per trade
PM_MIN_EDGE_PCT: float = float(
    os.getenv("PM_MIN_EDGE_PCT", "0.03")
)  # need ≥3% edge vs market
PM_MAX_POSITIONS: int = int(
    os.getenv("PM_MAX_POSITIONS", "5")
)  # max 5 open pred. market positions
PM_MIN_DAYS: float = float(os.getenv("PM_MIN_DAYS", "1.0"))  # min days to expiry
PM_MAX_DAYS: float = float(
    os.getenv("PM_MAX_DAYS", "90.0")
)  # max days to expiry (avoid illiquid far-dated)
PM_STOP_LOSS_FRACTION: float = (
    0.50  # exit if price drops to 50% of entry (e.g. $0.60 → exit at $0.30)
)
PM_TAKE_PROFIT_FRACTION: float = 0.60  # exit when 60% of potential gain captured
LANE3_SCAN_INTERVAL_SECONDS: int = int(
    os.getenv("LANE3_SCAN_INTERVAL_SECONDS", "900")
)  # 15 min

# Multi-LLM ensemble weights (must sum to 1.0)
# Weights are adapted by pm_calibrator.py based on per-model Brier scores
ENSEMBLE_CLAUDE_WEIGHT: float = float(
    os.getenv("ENSEMBLE_CLAUDE_WEIGHT", "1.0")
)  # start Claude-only
ENSEMBLE_GPT_WEIGHT: float = float(
    os.getenv("ENSEMBLE_GPT_WEIGHT", "0.0")
)  # add when OPENAI_API_KEY set
ENSEMBLE_GEMINI_WEIGHT: float = float(
    os.getenv("ENSEMBLE_GEMINI_WEIGHT", "0.0")
)  # add when GOOGLE_API_KEY set
PM_ENSEMBLE_MIN_MODELS: int = int(
    os.getenv("PM_ENSEMBLE_MIN_MODELS", "1")
)  # min models needed for forecast
PM_LLM_TEMPERATURE: float = float(
    os.getenv("PM_LLM_TEMPERATURE", "0.3")
)  # lower = more deterministic
PM_LLM_MAX_TOKENS: int = int(os.getenv("PM_LLM_MAX_TOKENS", "600"))

# Optional additional LLM providers (add keys to enable)
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

# ════════════════════════════════════════════════════════════════════
# DATABASE & LOGGING
# ════════════════════════════════════════════════════════════════════
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH: str = os.path.join(_ROOT_DIR, "logs", "trades.db")
LANCEDB_PATH: str = os.path.join(_ROOT_DIR, "logs", "memory")
CSV_LOG_DIR: str = os.path.join(_ROOT_DIR, "logs", "csv")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
