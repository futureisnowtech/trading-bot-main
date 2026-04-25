"""
backtesting/spot_replay_optimizer.py — research-grade 365-day spot scalp replay/optimizer.

This module is intentionally research-only. It does not change live parameters.
It reuses the live spot state math, then searches a bounded parameter surface
over real archived/backfilled candles to answer:

- which setups/regimes actually produce positive net expectancy
- which NEUTRAL blend / floor combinations work best
- which target/trail profiles fit the spot lane best
- where near-misses cluster

Usage:
    python3 -m backtesting.spot_replay_optimizer --days 365
    python3 -m backtesting.spot_replay_optimizer --symbol BTC --days 180 --top 10
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import pickle
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import (  # noqa: E402
    DB_PATH,
    SPOT_REPLAY_EVAL_TIMEFRAME,
    SPOT_REPLAY_LOOKBACK_DAYS,
    SPOT_REPLAY_OBJECTIVE,
    SPOT_SCALP_SYMBOL_CONFIG,
    SPOT_SYMBOLS,
)
try:
    from data.indicators import add_all_indicators  # noqa: E402
except Exception:
    _ind_spec = importlib.util.spec_from_file_location(
        "repo_indicators", os.path.join(ROOT, "data", "indicators.py")
    )
    _ind_mod = importlib.util.module_from_spec(_ind_spec)
    assert _ind_spec and _ind_spec.loader
    _ind_spec.loader.exec_module(_ind_mod)
    add_all_indicators = _ind_mod.add_all_indicators
from runtime.spot_momentum import (  # noqa: E402
    classify_setup_candidates,
    classify_setup_family,
    timeframe_state_from_history,
)
from runtime.spot_regime import classify_spot_regime  # noqa: E402
from runtime.spot_strategy import get_spot_strategy, setup_policy_for_symbol  # noqa: E402
from spot_engine import _compute_stop_pct  # noqa: E402


PRICE_ARCHIVE_PATH = os.path.join(ROOT, "logs", "price_archive.db")
BACKTEST_DIR = os.path.join(ROOT, "logs", "backtest")
REPLAY_CACHE_DIR = os.path.join(BACKTEST_DIR, "cache")
BINANCE_SPOT_BASE = "https://api.binance.com"
COINBASE_PUBLIC_BASE = "https://api.exchange.coinbase.com"
BINANCE_INTERVAL = {
    "5m": "5m",
    "30m": "30m",
    "4h": "4h",
    "1d": "1d",
}
COINBASE_GRANULARITY = {
    "5m": 300,
    "30m": 1800,
    "4h": 14400,
    "1d": 86400,
}
TF_MS = {
    "5m": 300_000,
    "30m": 1_800_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
MAX_BINANCE_LIMIT = 1500
ROUND_TRIP_TAKER_FEE_PCT = 0.0006
TIMEFRAMES = ("5m", "30m", "4h", "1d")
EVENT_CACHE_VERSION = "v1"
_TRIAL_NUMERIC_FIELDS = (
    "neutral_composite_weight",
    "neutral_floor",
    "trend_floor",
    "chop_floor",
    "min_confirm_count",
    "min_5m_frame",
    "min_30m_frame",
    "min_momentum_impulse",
    "min_structure_component",
    "min_path_efficiency",
    "min_participation_component",
    "min_volatility_quality",
)
_TRIAL_DISCRETE_FIELDS = (
    "target_profile",
    "setup_mode",
    "regime_mode",
    "min_confirm_count",
)


@dataclass(frozen=True)
class ReplayTrial:
    neutral_composite_weight: float
    neutral_floor: float
    trend_floor: float
    chop_floor: float
    target_profile: str
    setup_mode: str = "all"
    regime_mode: str = "all"
    min_confirm_count: int = 2
    min_5m_frame: float = 0.0
    min_30m_frame: float = 0.0
    min_momentum_impulse: float = -1.0
    min_structure_component: float = -1.0
    min_path_efficiency: float = -1.0
    min_participation_component: float = -1.0
    min_volatility_quality: float = -1.0

    @property
    def neutral_derivative_weight(self) -> float:
        return round(1.0 - self.neutral_composite_weight, 4)

    def label(self) -> str:
        return (
            f"nw={self.neutral_composite_weight:.2f}|"
            f"nf={self.neutral_floor:.1f}|"
            f"tf={self.trend_floor:.1f}|"
            f"cf={self.chop_floor:.1f}|"
            f"tp={self.target_profile}|"
            f"sm={self.setup_mode}|"
            f"rm={self.regime_mode}|"
            f"cfm={self.min_confirm_count}|"
            f"f5={self.min_5m_frame:.0f}|"
            f"f30={self.min_30m_frame:.0f}|"
            f"mi={self.min_momentum_impulse:.2f}|"
            f"sc={self.min_structure_component:.2f}|"
            f"pe={self.min_path_efficiency:.2f}|"
            f"pc={self.min_participation_component:.2f}|"
            f"vq={self.min_volatility_quality:.2f}"
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_backtest_dir() -> None:
    os.makedirs(BACKTEST_DIR, exist_ok=True)
    os.makedirs(REPLAY_CACHE_DIR, exist_ok=True)


def _write_json(path: str, payload: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _cache_key_df(df: pd.DataFrame, symbol: str, timeframe: str, days: int) -> str:
    if df is None or df.empty:
        return f"{_normalize_symbol(symbol)}_{timeframe}_{days}_empty"
    end_ns = int(df.index[-1].value)
    return f"{_normalize_symbol(symbol)}_{timeframe}_{days}_{len(df)}_{end_ns}"


def _cache_path(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in name)
    return os.path.join(REPLAY_CACHE_DIR, f"{safe}.pkl")


def _load_cached_frame(name: str) -> pd.DataFrame | None:
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    try:
        frame = pd.read_pickle(path)
        if isinstance(frame, pd.DataFrame):
            return frame
    except Exception:
        return None
    return None


def _save_cached_frame(name: str, df: pd.DataFrame) -> None:
    path = _cache_path(name)
    try:
        df.to_pickle(path)
    except Exception:
        return


def _archive_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(PRICE_ARCHIVE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            PRIMARY KEY (symbol, timeframe, open_time)
        )
        """
    )
    return conn


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("-USD", "").replace("USD", "").replace("USDT", "")


def _binance_symbol(symbol: str) -> str:
    return f"{_normalize_symbol(symbol)}USDT"


def _load_archive(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    conn = _archive_conn()
    try:
        df = pd.read_sql_query(
            """
            SELECT open_time, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol=? AND timeframe=? AND open_time>=? AND open_time<?
            ORDER BY open_time
            """,
            conn,
            params=(symbol, timeframe, start_ms, end_ms),
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def _save_archive(symbol: str, timeframe: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    rows = []
    for ts, row in df.iterrows():
        rows.append(
            (
                symbol,
                timeframe,
                int(ts.timestamp() * 1000),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row.get("volume", 0.0)),
            )
        )
    conn = _archive_conn()
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO ohlcv
            (symbol, timeframe, open_time, open, high, low, close, volume)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _binance_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list[Any]]:
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": MAX_BINANCE_LIMIT,
        }
    )
    url = f"{BINANCE_SPOT_BASE}/api/v3/klines?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "AlgoBot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return payload


def _coinbase_product(symbol: str) -> str:
    return f"{_normalize_symbol(symbol)}-USD"


def _coinbase_candles(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[list[Any]]:
    granularity = COINBASE_GRANULARITY[timeframe]
    params = urllib.parse.urlencode(
        {
            "granularity": granularity,
            "start": datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat(),
        }
    )
    url = f"{COINBASE_PUBLIC_BASE}/products/{_coinbase_product(symbol)}/candles?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "AlgoBot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return payload


def _fetch_binance_history(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    interval = BINANCE_INTERVAL[timeframe]
    bar_ms = TF_MS[timeframe]
    end_ms = int(_utc_now().timestamp() * 1000)
    start_ms = int((_utc_now() - timedelta(days=days)).timestamp() * 1000)
    cursor = start_ms
    chunks: list[pd.DataFrame] = []

    while cursor < end_ms:
        try:
            raw = _binance_klines(_binance_symbol(symbol), interval, cursor, end_ms)
        except Exception:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        if not raw:
            break
        rows = []
        for r in raw:
            rows.append(
                {
                    "open_time": int(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                }
            )
        df = pd.DataFrame(rows)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("open_time").sort_index()
        chunks.append(df)

        last_open_ms = int(raw[-1][0])
        next_cursor = last_open_ms + bar_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(0.05)

        if len(raw) < MAX_BINANCE_LIMIT:
            break

    if not chunks:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = pd.concat(chunks).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def _fetch_coinbase_history(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    granularity = COINBASE_GRANULARITY[timeframe]
    step_ms = granularity * 1000
    max_bars = 300
    chunk_ms = max_bars * step_ms
    end_ms = int(_utc_now().timestamp() * 1000)
    start_ms = int((_utc_now() - timedelta(days=days)).timestamp() * 1000)
    cursor = start_ms
    chunks: list[pd.DataFrame] = []

    while cursor < end_ms:
        chunk_end = min(end_ms, cursor + chunk_ms)
        try:
            raw = _coinbase_candles(symbol, timeframe, cursor, chunk_end)
        except Exception:
            break
        if not raw:
            cursor = chunk_end + step_ms
            time.sleep(0.05)
            continue

        rows = []
        for r in raw:
            # Coinbase candles: [time, low, high, open, close, volume]
            rows.append(
                {
                    "open_time": int(r[0]) * 1000,
                    "open": float(r[3]),
                    "high": float(r[2]),
                    "low": float(r[1]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                }
            )
        df = pd.DataFrame(rows)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("open_time").sort_index()
        chunks.append(df)
        cursor = chunk_end + step_ms
        time.sleep(0.05)

    if not chunks:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    out = pd.concat(chunks).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def _fetch_market_history(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    df = _fetch_binance_history(symbol, timeframe, days)
    if df is not None and not df.empty:
        return df
    return _fetch_coinbase_history(symbol, timeframe, days)


def ensure_history(symbol: str, timeframe: str, days: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    bar_ms = TF_MS[timeframe]
    end_ms = int(_utc_now().timestamp() * 1000)
    start_ms = int((_utc_now() - timedelta(days=days)).timestamp() * 1000)
    expected = max(1, math.ceil((end_ms - start_ms) / bar_ms))
    cached = _load_archive(symbol, timeframe, start_ms, end_ms)
    coverage = len(cached) / expected if expected > 0 else 0.0

    fetched = False
    if coverage < 0.85:
        fresh = _fetch_market_history(symbol, timeframe, days)
        if not fresh.empty:
            _save_archive(symbol, timeframe, fresh)
            cached = _load_archive(symbol, timeframe, start_ms, end_ms)
            coverage = len(cached) / expected if expected > 0 else 0.0
            fetched = True

    meta = {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": int(len(cached)),
        "expected_bars": int(expected),
        "coverage": round(float(coverage), 4),
        "fetched": fetched,
    }
    return cached, meta


def _profile_targets(profile: str, regime: str) -> tuple[float, float]:
    profiles = {
        "balanced": {
            "TREND": (1.8, 1.0),
            "NEUTRAL": (1.2, 0.8),
            "CHOP": (0.9, 0.6),
        },
        "runner": {
            "TREND": (2.2, 1.2),
            "NEUTRAL": (1.4, 0.9),
            "CHOP": (1.0, 0.7),
        },
        "quick": {
            "TREND": (1.5, 0.9),
            "NEUTRAL": (1.0, 0.7),
            "CHOP": (0.8, 0.5),
        },
        "precision": {
            "TREND": (1.05, 0.65),
            "NEUTRAL": (0.80, 0.50),
            "CHOP": (0.65, 0.40),
        },
        "micro": {
            "TREND": (0.85, 0.55),
            "NEUTRAL": (0.65, 0.40),
            "CHOP": (0.50, 0.30),
        },
        "nano": {
            "TREND": (0.60, 0.40),
            "NEUTRAL": (0.45, 0.28),
            "CHOP": (0.35, 0.22),
        },
    }
    return profiles.get(profile, profiles["balanced"]).get(regime, (1.2, 0.8))


def _resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    rule = {
        "30m": "30min",
        "4h": "4h",
        "1d": "1d",
    }.get(timeframe)
    if not rule:
        return df
    out = (
        df[["open", "high", "low", "close", "volume"]]
        .resample(rule, label="right", closed="right")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )
    return out


def _dynamic_floor(trial: ReplayTrial, regime: str, confirm_count: int, setup_family: str) -> float:
    base_map = {
        "TREND": trial.trend_floor,
        "NEUTRAL": trial.neutral_floor,
        "CHOP": trial.chop_floor,
    }
    floor = float(base_map.get(regime, trial.neutral_floor))
    if regime in {"TREND", "NEUTRAL"} and setup_family == "impulse_continuation":
        floor -= 1.0
    if regime != "CHOP" and confirm_count >= 3:
        floor -= 1.0
    if regime == "CHOP" and setup_family == "compression_breakout":
        floor += 1.0
    return max(58.0, min(70.0, floor))


def _composite_proxy(states: dict[str, dict[str, Any]]) -> float:
    s5 = float(states["5m"]["frame_score"])
    s30 = float(states["30m"]["frame_score"])
    s4 = float(states["4h"]["frame_score"])
    s1 = float(states["1d"]["frame_score"])
    confirms = max(
        int(states["5m"]["structural_confirm_count"]),
        int(states["30m"]["structural_confirm_count"]),
    )
    proxy = 0.45 * s30 + 0.25 * s4 + 0.15 * s1 + 0.15 * s5 + confirms * 1.5
    return max(0.0, min(100.0, round(proxy, 2)))


def _final_score(composite_proxy: float, derivative_score: float, regime: str, trial: ReplayTrial) -> float:
    if regime == "NEUTRAL":
        cw = trial.neutral_composite_weight
        dw = trial.neutral_derivative_weight
    else:
        cw = 0.60
        dw = 0.40
    return round(composite_proxy * cw + derivative_score * dw, 2)


def _nearest_idx(index: pd.DatetimeIndex, ts: pd.Timestamp) -> int:
    pos = int(index.searchsorted(ts, side="right")) - 1
    return max(0, min(pos, len(index) - 1))


def _build_trial_grid() -> list[ReplayTrial]:
    trials: list[ReplayTrial] = []
    for neutral_weight in (0.80, 0.90):
        for neutral_floor in (60.0, 61.0, 62.0):
            for trend_floor in (60.0, 61.0):
                for chop_floor in (67.0, 68.0):
                    for profile in ("balanced", "quick", "precision"):
                        for setup_mode in (
                            "all",
                            "no_compression",
                            "pullback_only",
                            "impulse_or_pullback",
                        ):
                            for regime_mode in ("all", "trend_neutral", "trend_only"):
                                for min_confirm_count in (2, 3):
                                    for min_5m_frame in (0.0, 55.0, 60.0):
                                        for min_30m_frame in (0.0, 54.0, 58.0):
                                            for min_impulse in (-1.0, 0.0):
                                                for min_structure in (-1.0, 0.0, 0.15):
                                                    for min_path_efficiency in (-1.0, 0.20, 0.35):
                                                        for min_participation in (-1.0, 0.0):
                                                            for min_volatility_quality in (-1.0, 0.0):
                                                                trials.append(
                                                                    ReplayTrial(
                                                                        neutral_composite_weight=neutral_weight,
                                                                        neutral_floor=neutral_floor,
                                                                        trend_floor=trend_floor,
                                                                        chop_floor=chop_floor,
                                                                        target_profile=profile,
                                                                        setup_mode=setup_mode,
                                                                        regime_mode=regime_mode,
                                                                        min_confirm_count=min_confirm_count,
                                                                        min_5m_frame=min_5m_frame,
                                                                        min_30m_frame=min_30m_frame,
                                                                        min_momentum_impulse=min_impulse,
                                                                        min_structure_component=min_structure,
                                                                        min_path_efficiency=min_path_efficiency,
                                                                        min_participation_component=min_participation,
                                                                        min_volatility_quality=min_volatility_quality,
                                                                    )
                                                                )
    return trials


def _setup_allowed(setup_mode: str, setup_family: str) -> bool:
    raise RuntimeError("legacy signature should not be used")


def _setup_allowed_dynamic(
    setup_mode: str,
    symbol: str,
    setup_family: str,
    setup_score: float,
) -> bool:
    setup_family = str(setup_family or "")
    symbol = _normalize_symbol(symbol)
    if setup_mode == "all":
        return True
    if setup_mode == "impulse_only":
        return setup_family == "impulse_continuation"
    if setup_mode == "compression_only":
        return setup_family == "compression_breakout"
    if setup_mode == "no_compression":
        return setup_family not in {"compression_breakout", "compression_expansion_retest"}
    if setup_mode == "pullback_only":
        return setup_family == "pullback_reclaim"
    if setup_mode == "impulse_or_pullback":
        return setup_family in {"impulse_continuation", "pullback_reclaim"}
    if setup_mode == "preferred_only":
        return setup_family in set(get_spot_strategy(symbol).get("preferred_setups", ()))
    if setup_mode == "preferred_dynamic":
        policy = setup_policy_for_symbol(symbol, setup_family, setup_score)
        return bool(policy.get("allowed"))
    if setup_mode == "reclaim_dynamic":
        return setup_family in {"pullback_reclaim", "trend_resume_after_shakeout"} or bool(
            setup_policy_for_symbol(symbol, setup_family, setup_score).get("allowed")
        )
    if setup_mode == "breakout_dynamic":
        return setup_family in {"compression_breakout", "compression_expansion_retest"} or bool(
            setup_policy_for_symbol(symbol, setup_family, setup_score).get("allowed")
        )
    if setup_mode == "momentum_dynamic":
        return setup_family in {"impulse_continuation", "compression_expansion_retest"} or bool(
            setup_policy_for_symbol(symbol, setup_family, setup_score).get("allowed")
        )
    return True


def _symbol_family(symbol: str) -> str:
    clean = _normalize_symbol(symbol)
    if clean in {"BTC", "ETH", "LTC", "LINK"}:
        return "major"
    if clean == "SOL":
        return "beta"
    if clean == "XRP":
        return "compression_alt"
    return "alt"


def _symbol_trial_bank(symbol: str) -> list[ReplayTrial]:
    family = _symbol_family(symbol)
    if family == "major":
        return [
            ReplayTrial(0.88, 58.0, 58.0, 66.0, "micro", "impulse_or_pullback", "trend_neutral", 2, 54.0, 52.0, -0.05, 0.00, 0.18, -0.10, -0.20),
            ReplayTrial(0.86, 59.0, 59.0, 67.0, "micro", "pullback_only", "trend_neutral", 2, 55.0, 54.0, 0.00, 0.05, 0.20, -0.05, -0.10),
            ReplayTrial(0.84, 60.0, 60.0, 67.0, "precision", "pullback_only", "trend_neutral", 2, 56.0, 55.0, 0.00, 0.05, 0.22, 0.00, -0.05),
            ReplayTrial(0.82, 60.0, 60.0, 68.0, "precision", "impulse_only", "trend_only", 3, 58.0, 56.0, 0.02, 0.10, 0.25, 0.00, 0.00),
            ReplayTrial(0.84, 59.0, 59.0, 67.0, "precision", "preferred_dynamic", "trend_neutral", 2, 55.0, 54.0, 0.00, 0.05, 0.22, -0.02, -0.08),
            ReplayTrial(0.82, 60.0, 60.0, 68.0, "precision", "reclaim_dynamic", "trend_neutral", 2, 56.0, 55.0, 0.00, 0.06, 0.24, 0.00, -0.05),
        ]
    if family == "beta":
        return [
            ReplayTrial(0.84, 61.0, 61.0, 68.0, "precision", "impulse_or_pullback", "trend_only", 3, 58.0, 56.0, 0.02, 0.10, 0.30, 0.05, -0.05),
            ReplayTrial(0.82, 62.0, 61.0, 68.0, "micro", "impulse_only", "trend_only", 3, 60.0, 58.0, 0.05, 0.10, 0.34, 0.05, 0.00),
            ReplayTrial(0.80, 63.0, 62.0, 69.0, "nano", "impulse_only", "trend_only", 3, 61.0, 58.0, 0.06, 0.12, 0.36, 0.08, 0.00),
            ReplayTrial(0.82, 61.0, 61.0, 68.0, "precision", "momentum_dynamic", "trend_only", 3, 59.0, 57.0, 0.04, 0.10, 0.32, 0.05, -0.02),
            ReplayTrial(0.80, 61.0, 60.0, 68.0, "micro", "preferred_dynamic", "trend_only", 3, 58.0, 56.0, 0.02, 0.08, 0.30, 0.04, -0.04),
        ]
    if family == "compression_alt":
        return [
            ReplayTrial(0.84, 60.0, 60.0, 68.0, "micro", "pullback_only", "trend_neutral", 3, 56.0, 55.0, 0.00, 0.05, 0.28, 0.05, -0.05),
            ReplayTrial(0.82, 61.0, 60.0, 68.0, "precision", "compression_only", "trend_neutral", 3, 57.0, 55.0, 0.00, 0.05, 0.30, 0.05, -0.05),
            ReplayTrial(0.80, 62.0, 61.0, 69.0, "nano", "compression_only", "trend_only", 3, 58.0, 56.0, 0.02, 0.08, 0.32, 0.08, 0.00),
            ReplayTrial(0.82, 60.0, 60.0, 68.0, "precision", "preferred_dynamic", "trend_neutral", 3, 56.0, 55.0, 0.00, 0.05, 0.30, 0.05, -0.05),
            ReplayTrial(0.80, 61.0, 60.0, 68.0, "micro", "breakout_dynamic", "trend_neutral", 3, 57.0, 55.0, 0.00, 0.05, 0.30, 0.05, -0.05),
        ]
    return [
        ReplayTrial(0.84, 61.0, 61.0, 68.0, "precision", "impulse_or_pullback", "trend_only", 3, 58.0, 56.0, 0.02, 0.08, 0.30, 0.05, -0.05),
        ReplayTrial(0.82, 62.0, 62.0, 69.0, "nano", "impulse_only", "trend_only", 3, 60.0, 58.0, 0.05, 0.10, 0.34, 0.08, 0.00),
        ReplayTrial(0.80, 62.0, 61.0, 69.0, "precision", "pullback_only", "trend_only", 3, 58.0, 56.0, 0.02, 0.08, 0.32, 0.05, 0.00),
        ReplayTrial(0.82, 61.0, 61.0, 68.0, "precision", "preferred_dynamic", "trend_only", 3, 58.0, 56.0, 0.02, 0.08, 0.30, 0.05, -0.05),
        ReplayTrial(0.80, 61.0, 60.0, 68.0, "micro", "momentum_dynamic", "trend_only", 3, 58.0, 56.0, 0.02, 0.08, 0.32, 0.05, 0.00),
    ]


def _setup_mode_to_allowed_setups(setup_mode: str) -> tuple[str, ...]:
    mapping = {
        "all": ("impulse_continuation", "pullback_reclaim", "compression_breakout"),
        "impulse_only": ("impulse_continuation",),
        "compression_only": ("compression_breakout",),
        "no_compression": ("impulse_continuation", "pullback_reclaim"),
        "pullback_only": ("pullback_reclaim",),
        "impulse_or_pullback": ("impulse_continuation", "pullback_reclaim"),
        "preferred_only": tuple(),
        "preferred_dynamic": tuple(),
        "reclaim_dynamic": ("pullback_reclaim", "trend_resume_after_shakeout"),
        "breakout_dynamic": ("compression_breakout", "compression_expansion_retest"),
        "momentum_dynamic": ("impulse_continuation", "compression_expansion_retest"),
    }
    return mapping.get(setup_mode, mapping["all"])


def _regime_mode_to_allowed_regimes(regime_mode: str) -> tuple[str, ...]:
    mapping = {
        "all": ("TREND", "NEUTRAL", "CHOP"),
        "trend_neutral": ("TREND", "NEUTRAL"),
        "trend_only": ("TREND",),
    }
    return mapping.get(regime_mode, mapping["all"])


def _passes_live_gate(
    result: dict[str, Any],
    *,
    min_win_rate: float,
    min_profit_factor: float,
    min_trades: int,
) -> bool:
    return (
        int(result.get("n_trades", 0)) >= int(min_trades)
        and float(result.get("win_rate", 0.0)) >= float(min_win_rate)
        and float(result.get("profit_factor", 0.0)) >= float(min_profit_factor)
        and float(result.get("net_expectancy_per_trade", -999.0)) > 0.0
    )


def _utility_score(
    result: dict[str, Any],
    *,
    min_trades: int,
) -> float:
    trades = max(int(result.get("n_trades", 0)), 0)
    if trades <= 0:
        return -50.0
    win_rate = float(result.get("win_rate", 0.0))
    raw_profit_factor = max(float(result.get("profit_factor", 0.0)), 1e-9)
    profit_factor = min(raw_profit_factor, 5.0)
    expectancy = float(result.get("net_expectancy_per_trade", -999.0))
    expectancy_bps = expectancy * 10_000.0
    confidence = min(trades / max(float(min_trades), 1.0), 1.0)
    trade_depth = min(math.log1p(trades) / math.log1p(max(min_trades * 6, 1)), 1.0)
    edge = (
        2.6 * expectancy_bps
        + 1.4 * math.log(profit_factor)
        + 1.1 * max(profit_factor - 1.0, 0.0)
        + 0.35 * ((win_rate - 0.40) * 10.0)
    )
    if expectancy <= 0:
        edge -= 1.0 + abs(expectancy_bps) * 0.35
    if raw_profit_factor < 1.0:
        edge -= (1.0 - raw_profit_factor) * 1.25
    if trades < min_trades:
        thin_sample_penalty = 0.75 + ((min_trades - trades) / max(float(min_trades), 1.0)) * 1.5
        edge -= thin_sample_penalty
    if trades < max(1, min_trades // 2):
        edge -= 6.0
    return round((0.85 * confidence + 0.15 * trade_depth) * edge + 0.10 * trade_depth, 6)


def _trial_fingerprint(trial: ReplayTrial) -> tuple[str, str, str]:
    return (
        trial.target_profile,
        trial.setup_mode,
        trial.regime_mode,
    )


def _trial_from_label(label: str) -> ReplayTrial:
    parts = {}
    for chunk in str(label or "").split("|"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        parts[key] = value
    return ReplayTrial(
        neutral_composite_weight=float(parts.get("nw", 0.84)),
        neutral_floor=float(parts.get("nf", 60.0)),
        trend_floor=float(parts.get("tf", 60.0)),
        chop_floor=float(parts.get("cf", 68.0)),
        target_profile=str(parts.get("tp", "precision")),
        setup_mode=str(parts.get("sm", "all")),
        regime_mode=str(parts.get("rm", "all")),
        min_confirm_count=int(float(parts.get("cfm", 2))),
        min_5m_frame=float(parts.get("f5", 0.0)),
        min_30m_frame=float(parts.get("f30", 0.0)),
        min_momentum_impulse=float(parts.get("mi", -1.0)),
        min_structure_component=float(parts.get("sc", -1.0)),
        min_path_efficiency=float(parts.get("pe", -1.0)),
        min_participation_component=float(parts.get("pc", -1.0)),
        min_volatility_quality=float(parts.get("vq", -1.0)),
    )


def _rounded_trial_value(name: str, value: float) -> float | int:
    if name == "min_confirm_count":
        return int(max(2, min(3, round(value))))
    if name in {"neutral_floor", "trend_floor", "chop_floor"}:
        return round(value, 1)
    return round(value, 4)


def _interpolate_trial(
    anchor: ReplayTrial,
    other: ReplayTrial,
    alpha: float,
) -> ReplayTrial:
    values: dict[str, Any] = {
        "target_profile": anchor.target_profile,
        "setup_mode": anchor.setup_mode,
        "regime_mode": anchor.regime_mode,
    }
    for name in _TRIAL_NUMERIC_FIELDS:
        start = float(getattr(anchor, name))
        end = float(getattr(other, name))
        values[name] = _rounded_trial_value(name, (1.0 - alpha) * start + alpha * end)
    return ReplayTrial(**values)


def _centroid_trial(
    template: ReplayTrial,
    trials: list[ReplayTrial],
    weights: list[float],
) -> ReplayTrial:
    values: dict[str, Any] = {
        "target_profile": template.target_profile,
        "setup_mode": template.setup_mode,
        "regime_mode": template.regime_mode,
    }
    total = max(sum(weights), 1e-9)
    for name in _TRIAL_NUMERIC_FIELDS:
        blended = sum(float(getattr(trial, name)) * w for trial, w in zip(trials, weights, strict=False)) / total
        values[name] = _rounded_trial_value(name, blended)
    return ReplayTrial(**values)


def _strategy_role(symbol: str, policy: dict[str, Any]) -> str:
    setups = set(policy.get("allowed_setups") or [])
    regimes = tuple(policy.get("allowed_regimes") or [])
    family = _symbol_family(symbol)
    if regimes == ("TREND",):
        if {"compression_breakout", "compression_expansion_retest"} & setups:
            return "trend_only_breakout_specialist"
        if {"impulse_continuation", "compression_expansion_retest"} & setups:
            return "trend_only_momentum_specialist"
        return "trend_only_precision_specialist"
    if family == "major":
        return "major_reclaim_engine"
    return "selective_hybrid_spot_scalp"


def _latest_strategy_extract_path() -> str | None:
    try:
        names = sorted(
            name
            for name in os.listdir(BACKTEST_DIR)
            if name.startswith("spot_strategy_extract_") and name.endswith(".json")
        )
    except Exception:
        return None
    if not names:
        return None
    return os.path.join(BACKTEST_DIR, names[-1])


def _event_value_quantiles(events: list[dict[str, Any]], getter) -> list[float]:
    values: list[float] = []
    for event in events:
        try:
            value = float(getter(event))
        except Exception:
            continue
        if math.isnan(value) or math.isinf(value):
            continue
        values.append(value)
    if not values:
        return [0.0]
    series = pd.Series(values)
    quantiles = []
    for q in (0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
        try:
            quantiles.append(float(series.quantile(q)))
        except Exception:
            continue
    return sorted(set(round(v, 4) for v in quantiles))


def _field_candidate_values(
    field: str,
    *,
    current: ReplayTrial,
    events: list[dict[str, Any]],
) -> list[Any]:
    if field == "target_profile":
        return ["balanced", "quick", "precision", "micro", "nano"]
    if field == "setup_mode":
        return [
            "all",
            "impulse_only",
            "compression_only",
            "pullback_only",
            "impulse_or_pullback",
            "preferred_dynamic",
            "reclaim_dynamic",
            "breakout_dynamic",
            "momentum_dynamic",
        ]
    if field == "regime_mode":
        return ["all", "trend_neutral", "trend_only"]
    if field == "min_confirm_count":
        return [2, 3]

    current_value = getattr(current, field)
    if field == "neutral_composite_weight":
        return sorted(set([0.76, 0.80, 0.84, 0.88, 0.92, round(float(current_value), 2)]))
    if field == "neutral_floor":
        return sorted(set([58.0, 59.0, 60.0, 61.0, 62.0, 63.0, round(float(current_value), 1)]))
    if field == "trend_floor":
        return sorted(set([58.0, 59.0, 60.0, 61.0, 62.0, round(float(current_value), 1)]))
    if field == "chop_floor":
        return sorted(set([66.0, 67.0, 68.0, 69.0, round(float(current_value), 1)]))

    quantiles = {
        "min_5m_frame": _event_value_quantiles(events, lambda e: e["states"]["5m"]["frame_score"]),
        "min_30m_frame": _event_value_quantiles(events, lambda e: e["states"]["30m"]["frame_score"]),
        "min_momentum_impulse": _event_value_quantiles(events, lambda e: e["states"]["5m"].get("momentum_impulse") or 0.0),
        "min_structure_component": _event_value_quantiles(events, lambda e: e["states"]["5m"].get("structure_component") or 0.0),
        "min_path_efficiency": _event_value_quantiles(events, lambda e: e["states"]["5m"].get("path_efficiency") or 0.0),
        "min_participation_component": _event_value_quantiles(events, lambda e: e["states"]["5m"].get("participation_component") or 0.0),
        "min_volatility_quality": _event_value_quantiles(events, lambda e: e["states"]["30m"].get("volatility_quality") or 0.0),
    }
    if field in {"min_5m_frame", "min_30m_frame"}:
        vals = [0.0] + quantiles[field] + [round(float(current_value), 2)]
        return sorted(set(round(v, 2) for v in vals))
    sentinel_map = {
        "min_momentum_impulse": -0.05,
        "min_structure_component": 0.0,
        "min_path_efficiency": 0.0,
        "min_participation_component": min([0.0] + quantiles[field]),
        "min_volatility_quality": min([0.0] + quantiles[field]),
    }
    vals = [sentinel_map.get(field, 0.0)] + quantiles[field] + [round(float(current_value), 4)]
    return sorted(set(round(v, 4) for v in vals))


def _candidate_delta_score(
    result: dict[str, Any],
    baseline: dict[str, Any],
    *,
    min_trades: int,
) -> float:
    trades = int(result.get("n_trades", 0))
    base_trades = max(int(baseline.get("n_trades", 0)), 1)
    utility = float(result.get("utility_score", _utility_score(result, min_trades=min_trades)))
    wr_delta = float(result.get("win_rate", 0.0)) - float(baseline.get("win_rate", 0.0))
    pf_delta = float(result.get("profit_factor", 0.0)) - float(baseline.get("profit_factor", 0.0))
    exp_delta = float(result.get("net_expectancy_per_trade", -999.0)) - float(
        baseline.get("net_expectancy_per_trade", -999.0)
    )
    trade_ratio = min(trades / base_trades, 1.0)
    score = utility
    score += 8.0 * wr_delta
    score += 1.8 * pf_delta
    score += 4000.0 * exp_delta
    score += 0.35 * trade_ratio
    if trades < max(1, min_trades // 2):
        score -= 4.0
    elif trades < min_trades:
        score -= 1.5
    if trades < max(1, int(base_trades * 0.35)):
        score -= 1.25
    return round(score, 6)


def _tweak_delta_summary(
    baseline_trial: ReplayTrial,
    optimized_trial: ReplayTrial,
) -> dict[str, dict[str, Any]]:
    deltas: dict[str, dict[str, Any]] = {}
    for field in _TRIAL_DISCRETE_FIELDS + _TRIAL_NUMERIC_FIELDS:
        before = getattr(baseline_trial, field)
        after = getattr(optimized_trial, field)
        if before == after:
            continue
        entry: dict[str, Any] = {"before": before, "after": after}
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            entry["delta"] = round(float(after) - float(before), 6)
        deltas[field] = entry
    return deltas


def _trial_to_live_policy(symbol: str, trial: ReplayTrial) -> dict[str, Any]:
    target_map = {
        regime: _profile_targets(trial.target_profile, regime)[0]
        for regime in ("TREND", "NEUTRAL", "CHOP")
    }
    trail_map = {
        regime: _profile_targets(trial.target_profile, regime)[1]
        for regime in ("TREND", "NEUTRAL", "CHOP")
    }
    return {
        "symbol": _normalize_symbol(symbol),
        "allowed_regimes": list(_regime_mode_to_allowed_regimes(trial.regime_mode)),
        "allowed_setups": list(_setup_mode_to_allowed_setups(trial.setup_mode)),
        "score_floors": {
            "TREND": trial.trend_floor,
            "NEUTRAL": trial.neutral_floor,
            "CHOP": trial.chop_floor,
        },
        "score_weights": {
            "TREND": {"composite": 0.60, "derivative": 0.40},
            "NEUTRAL": {
                "composite": trial.neutral_composite_weight,
                "derivative": trial.neutral_derivative_weight,
            },
            "CHOP": {"composite": 0.60, "derivative": 0.40},
        },
        "min_confirm_count": trial.min_confirm_count,
        "min_5m_frame": trial.min_5m_frame,
        "min_30m_frame": trial.min_30m_frame,
        "min_momentum_impulse": trial.min_momentum_impulse,
        "min_structure_component": trial.min_structure_component,
        "min_path_efficiency": trial.min_path_efficiency,
        "min_participation_component": trial.min_participation_component,
        "min_volatility_quality": trial.min_volatility_quality,
        "target_r_by_regime": target_map,
        "trail_arm_r_by_regime": trail_map,
        "target_profile": trial.target_profile,
    }


def _regime_allowed(regime_mode: str, regime: str) -> bool:
    if regime_mode == "all":
        return True
    if regime_mode == "trend_neutral":
        return regime in {"TREND", "NEUTRAL"}
    if regime_mode == "trend_only":
        return regime == "TREND"
    return True


def _future_path_arrays(
    future_5m: Any,
) -> tuple[list[float], list[float], list[float]]:
    if isinstance(future_5m, pd.DataFrame):
        return (
            future_5m["high"].astype(float).tolist(),
            future_5m["low"].astype(float).tolist(),
            future_5m["close"].astype(float).tolist(),
        )
    if isinstance(future_5m, dict):
        highs = [float(v) for v in future_5m.get("high", [])]
        lows = [float(v) for v in future_5m.get("low", [])]
        closes = [float(v) for v in future_5m.get("close", [])]
        return highs, lows, closes
    raise TypeError(f"Unsupported future path type: {type(future_5m)!r}")


def _simulate_trade(
    entry_ts: pd.Timestamp,
    entry_price: float,
    future_5m: Any,
    stop_pct: float,
    target_r: float,
    trail_arm_r: float,
    expected_half_life_min: float,
) -> dict[str, Any]:
    highs, lows, closes = _future_path_arrays(future_5m)
    if not closes:
        return {
            "entry_ts": entry_ts.isoformat(),
            "exit_price": round(entry_price, 8),
            "exit_reason": "end_of_window",
            "gross_pct": 0.0,
            "net_pct": -ROUND_TRIP_TAKER_FEE_PCT,
            "won": False,
            "hold_bars": 0,
        }
    risk = entry_price * stop_pct
    stop_price = entry_price - risk
    target_price = entry_price + risk * target_r
    best = entry_price
    trail_active = False
    trail_price = stop_price
    trail_offset = risk * max(0.6, min(target_r, 1.0))
    exit_price = closes[-1]
    exit_reason = "end_of_window"
    hold_bars = len(closes)

    for i, (high, low, close) in enumerate(zip(highs, lows, closes, strict=False), start=1):
        best = max(best, high)

        if low <= stop_price:
            exit_price = stop_price
            exit_reason = "hard_stop"
            hold_bars = i
            break

        if not trail_active and high >= entry_price + risk * trail_arm_r:
            trail_active = True
            trail_price = best - trail_offset

        if high >= target_price:
            exit_price = target_price
            exit_reason = "target_hit"
            hold_bars = i
            break

        if trail_active:
            trail_price = max(trail_price, best - trail_offset)
            if low <= trail_price:
                exit_price = trail_price
                exit_reason = "trailing_stop"
                hold_bars = i
                break

        elapsed_min = i * 5
        progress_r = (close - entry_price) / max(risk, 1e-9)
        if (
            elapsed_min >= expected_half_life_min
            and progress_r < 0.25
            and close < entry_price + 0.10 * risk
        ):
            exit_price = close
            exit_reason = "stagnation_exit"
            hold_bars = i
            break

    gross_pct = (exit_price - entry_price) / max(entry_price, 1e-9)
    net_pct = gross_pct - ROUND_TRIP_TAKER_FEE_PCT
    return {
        "entry_ts": entry_ts.isoformat(),
        "exit_price": round(exit_price, 8),
        "exit_reason": exit_reason,
        "gross_pct": gross_pct,
        "net_pct": net_pct,
        "won": net_pct > 0,
        "hold_bars": hold_bars,
    }


class SpotReplayOptimizer:
    def __init__(
        self,
        symbols: Iterable[str] | None = None,
        days: int = SPOT_REPLAY_LOOKBACK_DAYS,
        objective: str = SPOT_REPLAY_OBJECTIVE,
        eval_timeframe: str = SPOT_REPLAY_EVAL_TIMEFRAME,
    ):
        self.symbols = [_normalize_symbol(s) for s in (symbols or SPOT_SYMBOLS)]
        self.days = int(days)
        self.objective = objective
        self.eval_timeframe = eval_timeframe

    def load_histories(self) -> tuple[dict[str, dict[str, pd.DataFrame]], list[dict[str, Any]]]:
        _ensure_backtest_dir()
        histories: dict[str, dict[str, pd.DataFrame]] = {}
        coverage: list[dict[str, Any]] = []
        for symbol in self.symbols:
            histories[symbol] = {}
            df_5m, meta_5m = ensure_history(symbol, "5m", self.days)
            cache_5m_key = _cache_key_df(df_5m, symbol, "5m_enriched", self.days)
            enriched_5m = _load_cached_frame(cache_5m_key)
            if enriched_5m is None:
                enriched_5m = add_all_indicators(df_5m.copy()) if not df_5m.empty else df_5m
                if enriched_5m is not None and not enriched_5m.empty:
                    _save_cached_frame(cache_5m_key, enriched_5m)
            histories[symbol]["5m"] = enriched_5m
            coverage.append(meta_5m)

            for timeframe in ("30m", "4h", "1d"):
                derived = _resample_ohlcv(df_5m, timeframe)
                cache_tf_key = _cache_key_df(derived, symbol, f"{timeframe}_enriched", self.days)
                enriched = _load_cached_frame(cache_tf_key)
                if enriched is None:
                    enriched = add_all_indicators(derived.copy()) if not derived.empty else derived
                    if enriched is not None and not enriched.empty:
                        _save_cached_frame(cache_tf_key, enriched)
                histories[symbol][timeframe] = enriched
                expected = max(1, math.ceil((self.days * 86_400_000) / TF_MS[timeframe]))
                coverage.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "bars": int(len(derived)),
                        "expected_bars": int(expected),
                        "coverage": round(len(derived) / expected if expected > 0 else 0.0, 4),
                        "fetched": False,
                        "source": "resampled_from_5m",
                    }
                )
        return histories, coverage

    def _symbol_events(self, symbol: str, frames: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
        base = frames[self.eval_timeframe]
        if base is None or base.empty or len(base) < 250:
            return []
        f5 = frames["5m"]
        f30 = frames["30m"]
        f4 = frames["4h"]
        f1 = frames["1d"]
        if any(df is None or df.empty for df in (f5, f30, f4, f1)):
            return []
        f5_high = f5["high"].astype(float).to_numpy()
        f5_low = f5["low"].astype(float).to_numpy()
        f5_close = f5["close"].astype(float).to_numpy()

        events: list[dict[str, Any]] = []
        warmup = 120
        for ts in base.index[warmup:]:
            idx30 = _nearest_idx(f30.index, ts)
            idx4 = _nearest_idx(f4.index, ts)
            idx1 = _nearest_idx(f1.index, ts)
            idx5 = _nearest_idx(f5.index, ts)
            if min(idx5, idx30, idx4, idx1) <= 0:
                continue

            s5 = timeframe_state_from_history(f5.iloc[max(0, idx5 - 160) : idx5 + 1], enriched=True)
            s30 = timeframe_state_from_history(f30.iloc[max(0, idx30 - 120) : idx30 + 1], enriched=True)
            s4 = timeframe_state_from_history(f4.iloc[max(0, idx4 - 120) : idx4 + 1], enriched=True)
            s1 = timeframe_state_from_history(f1.iloc[max(0, idx1 - 120) : idx1 + 1], enriched=True)
            states = {"5m": s5, "30m": s30, "4h": s4, "1d": s1}
            regime = classify_spot_regime(s30, s4)
            setup_candidates = classify_setup_candidates(states, regime)
            setup_family = str(setup_candidates[0]["family"]) if setup_candidates else classify_setup_family(states, regime)
            setup_score = float(setup_candidates[0]["score"]) if setup_candidates else 0.0
            derivative_score = round(
                0.40 * s5["frame_score"]
                + 0.30 * s30["frame_score"]
                + 0.20 * s4["frame_score"]
                + 0.10 * s1["frame_score"],
                2,
            )
            composite_proxy = _composite_proxy(states)
            confirm_count = max(
                s5["structural_confirm_count"],
                s30["structural_confirm_count"],
            )
            spot_state = {
                "frames": states,
                "regime": regime,
                "rv_ratio": s30.get("rv_ratio", 1.0),
            }
            stop_pct = _compute_stop_pct(symbol, spot_state, atr_at_entry=0.0)
            price = float(f5_close[idx5])
            future_end = idx5 + 1 + 96
            future_high = f5_high[idx5 + 1 : future_end]
            future_low = f5_low[idx5 + 1 : future_end]
            future_close = f5_close[idx5 + 1 : future_end]
            if len(future_close) == 0:
                continue

            events.append(
                {
                    "symbol": symbol,
                    "ts": ts,
                    "price": price,
                    "regime": regime,
                    "setup_family": setup_family,
                    "setup_score": setup_score,
                    "states": states,
                    "derivative_score": derivative_score,
                    "composite_proxy": composite_proxy,
                    "confirm_count": int(confirm_count),
                    "stop_pct": float(stop_pct),
                    "expected_half_life_min": max(6.0, min(float(s30.get("ou_halflife_minutes") or 18.0), 45.0)),
                    "future_5m": {
                        "high": future_high.tolist(),
                        "low": future_low.tolist(),
                        "close": future_close.tolist(),
                    },
                }
            )
        return events

    def build_event_set(self, histories: dict[str, dict[str, pd.DataFrame]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for symbol, frames in histories.items():
            events.extend(self._symbol_events(symbol, frames))
        return events

    def build_event_sets_by_symbol(
        self,
        histories: dict[str, dict[str, pd.DataFrame]],
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            symbol: self._symbol_events(symbol, frames)
            for symbol, frames in histories.items()
        }

    def _fit_symbol_from_events(
        self,
        symbol: str,
        events: list[dict[str, Any]],
        *,
        top_n: int,
        min_win_rate: float,
        min_profit_factor: float,
        min_trades: int,
    ) -> dict[str, Any]:
        bank = _symbol_trial_bank(symbol)
        scored = [
            result
            for _, result in self._score_symbol_trials(
                symbol,
                events,
                bank,
                min_win_rate=min_win_rate,
                min_profit_factor=min_profit_factor,
                min_trades=min_trades,
            )
        ]
        scored = sorted(
            scored,
            key=lambda r: (
                1 if r.get("meets_target") else 0,
                float(r.get("win_rate", 0.0)),
                float(r.get("profit_factor", 0.0)),
                float(r.get("net_expectancy_per_trade", -999.0)),
                float(r.get("n_trades", 0)),
            ),
            reverse=True,
        )
        return {
            "symbol": symbol,
            "event_count": len(events),
            "top_trials": scored[:top_n],
            "best_trial": scored[0] if scored else None,
            "meets_target": bool(scored and scored[0].get("meets_target")),
        }

    def _progress_path(self) -> str:
        return os.path.join(BACKTEST_DIR, f"spot_symbol_fit_{self.days}d_progress.json")

    def _symbol_fit_path(self, symbol: str) -> str:
        return os.path.join(BACKTEST_DIR, f"fit_{_normalize_symbol(symbol)}_{self.days}d.json")

    def _symbol_event_cache_path(self, symbol: str) -> str:
        return os.path.join(
            REPLAY_CACHE_DIR,
            f"{_normalize_symbol(symbol)}_events_{self.eval_timeframe}_{self.days}d_{EVENT_CACHE_VERSION}.pkl",
        )

    def _write_fit_progress(
        self,
        *,
        recommendations: dict[str, Any],
        completed_symbols: list[str],
        coverage: list[dict[str, Any]],
        constraints: dict[str, Any],
        status: str = "running",
        current_symbol: str | None = None,
        phase: str | None = None,
    ) -> None:
        payload = {
            "generated_at": _utc_now().isoformat(),
            "status": status,
            "days": self.days,
            "objective": self.objective,
            "symbols": self.symbols,
            "completed_symbols": completed_symbols,
            "remaining_symbols": [s for s in self.symbols if s not in set(completed_symbols)],
            "coverage": coverage,
            "constraints": constraints,
            "recommendations": recommendations,
            "current_symbol": current_symbol,
            "phase": phase,
        }
        _write_json(self._progress_path(), payload)

    def _load_cached_events(self, symbol: str) -> list[dict[str, Any]] | None:
        path = self._symbol_event_cache_path(symbol)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as fh:
                cached = pickle.load(fh)
            if isinstance(cached, list):
                return cached
        except Exception:
            return None
        return None

    def _save_cached_events(self, symbol: str, events: list[dict[str, Any]]) -> None:
        with open(self._symbol_event_cache_path(symbol), "wb") as fh:
            pickle.dump(events, fh, protocol=pickle.HIGHEST_PROTOCOL)

    def _load_or_build_events(
        self,
        symbol: str,
        *,
        recommendations: dict[str, Any] | None = None,
        completed_symbols: list[str] | None = None,
        coverage: list[dict[str, Any]] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        recs = recommendations or {}
        done = completed_symbols or []
        cov = coverage or []
        limits = constraints or {}
        self._write_fit_progress(
            recommendations=recs,
            completed_symbols=done,
            coverage=cov,
            constraints=limits,
            status="running",
            current_symbol=symbol,
            phase="loading_histories",
        )
        histories, symbol_coverage = self.load_histories()
        events = self._load_cached_events(symbol)
        if events is None:
            self._write_fit_progress(
                recommendations=recs,
                completed_symbols=done,
                coverage=cov + symbol_coverage,
                constraints=limits,
                status="running",
                current_symbol=symbol,
                phase="building_events",
            )
            events = self.build_event_sets_by_symbol(histories).get(symbol, [])
            self._save_cached_events(symbol, events)
        return events, symbol_coverage

    def _evaluate_symbol_trial(
        self,
        symbol: str,
        trial: ReplayTrial,
        events: list[dict[str, Any]],
        *,
        min_win_rate: float,
        min_profit_factor: float,
        min_trades: int,
    ) -> dict[str, Any]:
        result = self.evaluate_trial(trial, events)
        result["meets_target"] = _passes_live_gate(
            result,
            min_win_rate=min_win_rate,
            min_profit_factor=min_profit_factor,
            min_trades=min_trades,
        )
        result["utility_score"] = _utility_score(result, min_trades=min_trades)
        result["recommended_live_policy"] = _trial_to_live_policy(symbol, trial)
        result["strategy_role"] = _strategy_role(symbol, result["recommended_live_policy"])
        return result

    def _score_symbol_trials(
        self,
        symbol: str,
        events: list[dict[str, Any]],
        trials: list[ReplayTrial],
        *,
        min_win_rate: float,
        min_profit_factor: float,
        min_trades: int,
    ) -> list[tuple[ReplayTrial, dict[str, Any]]]:
        return [
            (
                trial,
                self._evaluate_symbol_trial(
                    symbol,
                    trial,
                    events,
                    min_win_rate=min_win_rate,
                    min_profit_factor=min_profit_factor,
                    min_trades=min_trades,
                ),
            )
            for trial in trials
        ]

    def _seed_trials_for_symbol(self, symbol: str, events: list[dict[str, Any]]) -> list[ReplayTrial]:
        seeds: list[ReplayTrial] = []
        latest_path = _latest_strategy_extract_path()
        if latest_path and os.path.exists(latest_path):
            try:
                payload = json.load(open(latest_path, encoding="utf-8"))
                rec = payload.get("recommendations", {}).get(_normalize_symbol(symbol), {})
                trial_label = (rec.get("recommended_strategy") or {}).get("trial")
                if trial_label:
                    seeds.append(_trial_from_label(trial_label))
            except Exception:
                pass
        seeds.extend(_symbol_trial_bank(symbol))

        q5 = _event_value_quantiles(events, lambda e: e["states"]["5m"]["frame_score"])
        q30 = _event_value_quantiles(events, lambda e: e["states"]["30m"]["frame_score"])
        qimp = _event_value_quantiles(events, lambda e: e["states"]["5m"].get("momentum_impulse") or 0.0)
        qstruct = _event_value_quantiles(events, lambda e: e["states"]["5m"].get("structure_component") or 0.0)
        qpath = _event_value_quantiles(events, lambda e: e["states"]["5m"].get("path_efficiency") or 0.0)
        qpart = _event_value_quantiles(events, lambda e: e["states"]["5m"].get("participation_component") or 0.0)
        qvol = _event_value_quantiles(events, lambda e: e["states"]["30m"].get("volatility_quality") or 0.0)
        def qpick(vals: list[float], idx: int, default: float) -> float:
            return round(vals[min(max(idx, 0), len(vals) - 1)], 4) if vals else default

        generic = [
            ReplayTrial(
                0.84, 60.0, 60.0, 68.0, "precision",
                "preferred_dynamic", "trend_neutral", 2,
                qpick(q5, 2, 55.0), qpick(q30, 2, 54.0), qpick(qimp, 1, 0.0),
                max(qpick(qstruct, 1, 0.0), 0.0), max(qpick(qpath, 1, 0.18), 0.0),
                qpick(qpart, 1, 0.0), qpick(qvol, 1, -0.05),
            ),
            ReplayTrial(
                0.82, 60.0, 60.0, 68.0, "micro",
                "all", "all", 2,
                qpick(q5, 1, 54.0), qpick(q30, 1, 53.0), qpick(qimp, 0, -0.02),
                max(qpick(qstruct, 0, 0.0), 0.0), max(qpick(qpath, 0, 0.12), 0.0),
                qpick(qpart, 0, -0.05), qpick(qvol, 0, -0.10),
            ),
            ReplayTrial(
                0.80, 61.0, 60.0, 68.0, "precision",
                "momentum_dynamic", "trend_only", 3,
                qpick(q5, 3, 58.0), qpick(q30, 3, 56.0), max(qpick(qimp, 2, 0.02), 0.0),
                max(qpick(qstruct, 2, 0.05), 0.0), max(qpick(qpath, 2, 0.24), 0.0),
                qpick(qpart, 2, 0.0), qpick(qvol, 2, -0.02),
            ),
        ]
        seeds.extend(generic)
        deduped = {trial.label(): trial for trial in seeds}
        return list(deduped.values())

    def _coordinate_optimize_symbol(
        self,
        symbol: str,
        events: list[dict[str, Any]],
        seed_trial: ReplayTrial,
        *,
        min_win_rate: float,
        min_profit_factor: float,
        min_trades: int,
        max_rounds: int = 3,
    ) -> dict[str, Any]:
        cache: dict[str, tuple[ReplayTrial, dict[str, Any]]] = {}

        def score(trial: ReplayTrial) -> dict[str, Any]:
            key = trial.label()
            if key not in cache:
                cache[key] = (
                    trial,
                    self._evaluate_symbol_trial(
                        symbol,
                        trial,
                        events,
                        min_win_rate=min_win_rate,
                        min_profit_factor=min_profit_factor,
                        min_trades=min_trades,
                    ),
                )
            return cache[key][1]

        baseline_result = score(seed_trial)
        current_trial = seed_trial
        current_result = baseline_result
        marginal_effects: list[dict[str, Any]] = []

        ordered_fields = [
            "target_profile",
            "setup_mode",
            "regime_mode",
            "min_confirm_count",
            "neutral_composite_weight",
            "neutral_floor",
            "trend_floor",
            "chop_floor",
            "min_5m_frame",
            "min_30m_frame",
            "min_momentum_impulse",
            "min_structure_component",
            "min_path_efficiency",
            "min_participation_component",
            "min_volatility_quality",
        ]

        for round_idx in range(max_rounds):
            improved = False
            for field in ordered_fields:
                options = _field_candidate_values(field, current=current_trial, events=events)
                best_trial = current_trial
                best_result = current_result
                local_best_gain = None
                for value in options:
                    trial = replace(current_trial, **{field: value})
                    result = score(trial)
                    gain = _candidate_delta_score(result, current_result, min_trades=min_trades)
                    if local_best_gain is None or gain > local_best_gain["gain"]:
                        local_best_gain = {
                            "field": field,
                            "value": value,
                            "gain": gain,
                            "result": result,
                        }
                    candidate_score = _candidate_delta_score(result, baseline_result, min_trades=min_trades)
                    incumbent_score = _candidate_delta_score(best_result, baseline_result, min_trades=min_trades)
                    if candidate_score > incumbent_score:
                        best_trial = trial
                        best_result = result
                if local_best_gain is not None:
                    marginal_effects.append(
                        {
                            "round": round_idx + 1,
                            "field": field,
                            "candidate_value": local_best_gain["value"],
                            "candidate_score_gain": local_best_gain["gain"],
                            "candidate_win_rate": local_best_gain["result"].get("win_rate"),
                            "candidate_profit_factor": local_best_gain["result"].get("profit_factor"),
                            "candidate_expectancy": local_best_gain["result"].get("net_expectancy_per_trade"),
                            "candidate_trades": local_best_gain["result"].get("n_trades"),
                        }
                    )
                if best_trial != current_trial:
                    current_trial = best_trial
                    current_result = best_result
                    improved = True
            if not improved:
                break

        return {
            "baseline_trial": seed_trial,
            "baseline_result": baseline_result,
            "optimized_trial": current_trial,
            "optimized_result": current_result,
            "marginal_effects": sorted(
                marginal_effects,
                key=lambda item: float(item.get("candidate_score_gain", -999.0)),
                reverse=True,
            )[:10],
            "evaluated_trials": len(cache),
        }

    def evaluate_trial(self, trial: ReplayTrial, events: list[dict[str, Any]]) -> dict[str, Any]:
        trades: list[dict[str, Any]] = []
        near_misses = 0
        regime_counts: dict[str, int] = {}
        setup_counts: dict[str, int] = {}

        for event in events:
            regime = str(event["regime"])
            setup_family = str(event["setup_family"])
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            setup_counts[setup_family] = setup_counts.get(setup_family, 0) + 1

            if not _setup_allowed_dynamic(
                trial.setup_mode,
                str(event.get("symbol") or ""),
                setup_family,
                float(event.get("setup_score") or 0.0),
            ):
                continue
            if not _regime_allowed(trial.regime_mode, regime):
                continue

            floor = _dynamic_floor(trial, regime, int(event["confirm_count"]), setup_family)
            final_score = _final_score(
                float(event["composite_proxy"]),
                float(event["derivative_score"]),
                regime,
                trial,
            )
            if abs(final_score - floor) <= 2.0:
                near_misses += 1

            states = event["states"]
            if (
                states["5m"]["v"] <= 0
                or states["5m"]["a"] <= 0
                or states["30m"]["v"] < -0.02
                or int(event["confirm_count"]) < int(trial.min_confirm_count)
                or float(states["5m"]["frame_score"]) < float(trial.min_5m_frame)
                or float(states["30m"]["frame_score"]) < float(trial.min_30m_frame)
                or float(states["5m"].get("momentum_impulse") or 0.0) < float(trial.min_momentum_impulse)
                or float(states["5m"].get("structure_component") or 0.0) < float(trial.min_structure_component)
                or float(states["5m"].get("path_efficiency") or 0.0) < float(trial.min_path_efficiency)
                or float(states["5m"].get("participation_component") or 0.0) < float(trial.min_participation_component)
                or float(states["30m"].get("volatility_quality") or 0.0) < float(trial.min_volatility_quality)
                or final_score < floor
            ):
                continue

            target_r, trail_arm_r = _profile_targets(trial.target_profile, regime)
            sim = _simulate_trade(
                entry_ts=event["ts"],
                entry_price=float(event["price"]),
                future_5m=event["future_5m"],
                stop_pct=float(event["stop_pct"]),
                target_r=target_r,
                trail_arm_r=trail_arm_r,
                expected_half_life_min=float(event["expected_half_life_min"]),
            )
            sim.update(
                {
                    "symbol": event["symbol"],
                    "regime": regime,
                    "setup_family": setup_family,
                    "final_score": final_score,
                    "floor": floor,
                    "target_r": target_r,
                    "trail_arm_r": trail_arm_r,
                }
            )
            trades.append(sim)

        n = len(trades)
        if n == 0:
            return {
                "trial": trial.label(),
                "n_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "net_expectancy_per_trade": -999.0,
                "net_pnl_pct": 0.0,
                "avg_hold_bars": 0.0,
                "near_misses": near_misses,
                "regime_counts": regime_counts,
                "setup_counts": setup_counts,
            }

        wins = [t for t in trades if t["net_pct"] > 0]
        losses = [t for t in trades if t["net_pct"] <= 0]
        gross_wins = sum(t["net_pct"] for t in wins)
        gross_losses = abs(sum(t["net_pct"] for t in losses))
        net_pnl_pct = sum(t["net_pct"] for t in trades)
        expectancy = net_pnl_pct / n
        profit_factor = gross_wins / max(gross_losses, 1e-9)
        avg_hold = sum(int(t["hold_bars"]) for t in trades) / n

        return {
            "trial": trial.label(),
            "n_trades": n,
            "win_rate": round(len(wins) / n, 4),
            "profit_factor": round(profit_factor, 4),
            "net_expectancy_per_trade": round(expectancy, 6),
            "net_pnl_pct": round(net_pnl_pct, 4),
            "avg_hold_bars": round(avg_hold, 2),
            "near_misses": near_misses,
            "regime_counts": regime_counts,
            "setup_counts": setup_counts,
        }

    def run(self, top_n: int = 15) -> dict[str, Any]:
        _ensure_backtest_dir()
        histories, coverage = self.load_histories()
        events = self.build_event_set(histories)
        trials = [_t for _t in _build_trial_grid()]
        results = [self.evaluate_trial(trial, events) for trial in trials]
        results = sorted(
            results,
            key=lambda r: (
                float(r.get(self.objective, -999.0)),
                float(r.get("profit_factor", 0.0)),
                float(r.get("win_rate", 0.0)),
            ),
            reverse=True,
        )
        payload = {
            "generated_at": _utc_now().isoformat(),
            "days": self.days,
            "objective": self.objective,
            "symbols": self.symbols,
            "event_count": len(events),
            "coverage": coverage,
            "top_trials": results[:top_n],
        }
        stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        out_path = os.path.join(BACKTEST_DIR, f"spot_replay_{stamp}.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        payload["output_path"] = out_path
        return payload

    def fit_symbol_strategies(
        self,
        *,
        top_n: int = 5,
        min_win_rate: float = 0.52,
        min_profit_factor: float = 1.05,
        min_trades: int = 12,
    ) -> dict[str, Any]:
        _ensure_backtest_dir()
        constraints = {
            "min_win_rate": min_win_rate,
            "min_profit_factor": min_profit_factor,
            "min_trades": min_trades,
        }
        recommendations: dict[str, Any] = {}
        coverage: list[dict[str, Any]] = []
        completed_symbols: list[str] = []
        self._write_fit_progress(
            recommendations=recommendations,
            completed_symbols=completed_symbols,
            coverage=coverage,
            constraints=constraints,
            status="running",
            phase="starting",
        )

        if len(self.symbols) == 1:
            symbol = self.symbols[0]
            events = self._load_cached_events(symbol)
            if events is None:
                self._write_fit_progress(
                    recommendations=recommendations,
                    completed_symbols=completed_symbols,
                    coverage=coverage,
                    constraints=constraints,
                    status="running",
                    current_symbol=symbol,
                    phase="loading_histories",
                )
                histories, coverage = self.load_histories()
                self._write_fit_progress(
                    recommendations=recommendations,
                    completed_symbols=completed_symbols,
                    coverage=coverage,
                    constraints=constraints,
                    status="running",
                    current_symbol=symbol,
                    phase="building_events",
                )
                event_sets = self.build_event_sets_by_symbol(histories)
                events = event_sets.get(symbol, [])
                self._save_cached_events(symbol, events)
            self._write_fit_progress(
                recommendations=recommendations,
                completed_symbols=completed_symbols,
                coverage=coverage,
                constraints=constraints,
                status="running",
                current_symbol=symbol,
                phase="fitting_trials",
            )
            recommendations[symbol] = self._fit_symbol_from_events(
                symbol,
                events,
                top_n=top_n,
                min_win_rate=min_win_rate,
                min_profit_factor=min_profit_factor,
                min_trades=min_trades,
            )
            completed_symbols.append(symbol)
            _write_json(
                self._symbol_fit_path(symbol),
                recommendations[symbol]["best_trial"] or recommendations[symbol],
            )
            self._write_fit_progress(
                recommendations=recommendations,
                completed_symbols=completed_symbols,
                coverage=coverage,
                constraints=constraints,
                status="running",
                current_symbol=symbol,
                phase="fit_complete",
            )
        else:
            for symbol in self.symbols:
                child = SpotReplayOptimizer(
                    symbols=[symbol],
                    days=self.days,
                    objective=self.objective,
                    eval_timeframe=self.eval_timeframe,
                )
                self._write_fit_progress(
                    recommendations=recommendations,
                    completed_symbols=completed_symbols,
                    coverage=coverage,
                    constraints=constraints,
                    status="running",
                    current_symbol=symbol,
                    phase="loading_histories",
                )
                histories, symbol_coverage = child.load_histories()
                coverage.extend(symbol_coverage)
                events = child._load_cached_events(symbol)
                if events is None:
                    self._write_fit_progress(
                        recommendations=recommendations,
                        completed_symbols=completed_symbols,
                        coverage=coverage,
                        constraints=constraints,
                        status="running",
                        current_symbol=symbol,
                        phase="building_events",
                    )
                    events = child.build_event_sets_by_symbol(histories).get(symbol, [])
                    child._save_cached_events(symbol, events)
                self._write_fit_progress(
                    recommendations=recommendations,
                    completed_symbols=completed_symbols,
                    coverage=coverage,
                    constraints=constraints,
                    status="running",
                    current_symbol=symbol,
                    phase="fitting_trials",
                )
                recommendations[symbol] = self._fit_symbol_from_events(
                    symbol,
                    events,
                    top_n=top_n,
                    min_win_rate=min_win_rate,
                    min_profit_factor=min_profit_factor,
                    min_trades=min_trades,
                )
                completed_symbols.append(symbol)
                _write_json(
                    self._symbol_fit_path(symbol),
                    recommendations[symbol]["best_trial"] or recommendations[symbol],
                )
                self._write_fit_progress(
                    recommendations=recommendations,
                    completed_symbols=completed_symbols,
                    coverage=coverage,
                    constraints=constraints,
                    status="running",
                    current_symbol=symbol,
                    phase="fit_complete",
                )

        payload = {
            "generated_at": _utc_now().isoformat(),
            "status": "complete",
            "days": self.days,
            "objective": self.objective,
            "symbols": self.symbols,
            "coverage": coverage,
            "constraints": constraints,
            "recommendations": recommendations,
        }
        stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        out_path = os.path.join(BACKTEST_DIR, f"spot_symbol_fit_{stamp}.json")
        _write_json(out_path, payload)
        payload["output_path"] = out_path
        return payload

    def optimize_coin_strategies(
        self,
        *,
        top_n: int = 5,
        min_win_rate: float = 0.52,
        min_profit_factor: float = 1.05,
        min_trades: int = 12,
    ) -> dict[str, Any]:
        _ensure_backtest_dir()
        constraints = {
            "min_win_rate": min_win_rate,
            "min_profit_factor": min_profit_factor,
            "min_trades": min_trades,
            "search": "per_coin_coordinate_surgery",
        }
        recommendations: dict[str, Any] = {}
        coverage: list[dict[str, Any]] = []
        completed_symbols: list[str] = []
        self._write_fit_progress(
            recommendations=recommendations,
            completed_symbols=completed_symbols,
            coverage=coverage,
            constraints=constraints,
            status="running",
            phase="coin_strategy_surgery_start",
        )

        for symbol in self.symbols:
            child = SpotReplayOptimizer(
                symbols=[symbol],
                days=self.days,
                objective=self.objective,
                eval_timeframe=self.eval_timeframe,
            )
            events, symbol_coverage = child._load_or_build_events(
                symbol,
                recommendations=recommendations,
                completed_symbols=completed_symbols,
                coverage=coverage,
                constraints=constraints,
            )
            coverage.extend(symbol_coverage)

            seeds = child._seed_trials_for_symbol(symbol, events)
            candidate_runs = [
                child._coordinate_optimize_symbol(
                    symbol,
                    events,
                    seed,
                    min_win_rate=min_win_rate,
                    min_profit_factor=min_profit_factor,
                    min_trades=min_trades,
                )
                for seed in seeds
            ]
            symbol_baseline_run = max(
                candidate_runs,
                key=lambda item: (
                    float(item["baseline_result"].get("utility_score", -999.0)),
                    float(item["baseline_result"].get("net_expectancy_per_trade", -999.0)),
                    float(item["baseline_result"].get("profit_factor", 0.0)),
                    float(item["baseline_result"].get("win_rate", 0.0)),
                    float(item["baseline_result"].get("n_trades", 0)),
                ),
            )
            symbol_baseline_trial = symbol_baseline_run["baseline_trial"]
            symbol_baseline_result = symbol_baseline_run["baseline_result"]
            candidate_runs = sorted(
                candidate_runs,
                key=lambda item: (
                    _candidate_delta_score(item["optimized_result"], symbol_baseline_result, min_trades=min_trades),
                    float(item["optimized_result"].get("utility_score", -999.0)),
                    float(item["optimized_result"].get("net_expectancy_per_trade", -999.0)),
                    float(item["optimized_result"].get("profit_factor", 0.0)),
                    float(item["optimized_result"].get("win_rate", 0.0)),
                ),
                reverse=True,
            )
            best = candidate_runs[0]
            baseline_trial = symbol_baseline_trial
            baseline_result = symbol_baseline_result
            optimized_trial = best["optimized_trial"]
            optimized_result = best["optimized_result"]

            summary_scorecard = {
                "baseline": {
                    "trial": baseline_result.get("trial"),
                    "n_trades": baseline_result.get("n_trades"),
                    "win_rate": baseline_result.get("win_rate"),
                    "profit_factor": baseline_result.get("profit_factor"),
                    "net_expectancy_per_trade": baseline_result.get("net_expectancy_per_trade"),
                    "utility_score": baseline_result.get("utility_score"),
                },
                "optimized": {
                    "trial": optimized_result.get("trial"),
                    "n_trades": optimized_result.get("n_trades"),
                    "win_rate": optimized_result.get("win_rate"),
                    "profit_factor": optimized_result.get("profit_factor"),
                    "net_expectancy_per_trade": optimized_result.get("net_expectancy_per_trade"),
                    "utility_score": optimized_result.get("utility_score"),
                },
                "delta": {
                    "n_trades": int(optimized_result.get("n_trades", 0)) - int(baseline_result.get("n_trades", 0)),
                    "win_rate": round(float(optimized_result.get("win_rate", 0.0)) - float(baseline_result.get("win_rate", 0.0)), 4),
                    "profit_factor": round(float(optimized_result.get("profit_factor", 0.0)) - float(baseline_result.get("profit_factor", 0.0)), 4),
                    "net_expectancy_per_trade": round(
                        float(optimized_result.get("net_expectancy_per_trade", -999.0))
                        - float(baseline_result.get("net_expectancy_per_trade", -999.0)),
                        6,
                    ),
                    "utility_score": round(
                        float(optimized_result.get("utility_score", -999.0))
                        - float(baseline_result.get("utility_score", -999.0)),
                        6,
                    ),
                },
            }

            status = "no_improvement"
            if (
                float(optimized_result.get("net_expectancy_per_trade", -999.0)) > float(baseline_result.get("net_expectancy_per_trade", -999.0))
                and float(optimized_result.get("profit_factor", 0.0)) >= float(baseline_result.get("profit_factor", 0.0))
            ):
                status = "improved_candidate"
            if (
                float(optimized_result.get("net_expectancy_per_trade", -999.0)) > 0.0
                and float(optimized_result.get("profit_factor", 0.0)) >= 1.0
                and int(optimized_result.get("n_trades", 0)) >= min_trades
            ):
                status = "promotable_research_candidate"

            recommendations[symbol] = {
                "symbol": symbol,
                "event_count": len(events),
                "baseline_strategy": baseline_result,
                "optimized_strategy": optimized_result,
                "scorecard": summary_scorecard,
                "tweak_deltas": _tweak_delta_summary(baseline_trial, optimized_trial),
                "marginal_effects": best["marginal_effects"],
                "evaluated_trials": best["evaluated_trials"],
                "seed_count": len(seeds),
                "top_runs": [
                    {
                        "baseline_trial": run["baseline_result"].get("trial"),
                        "optimized_trial": run["optimized_result"].get("trial"),
                        "optimized_win_rate": run["optimized_result"].get("win_rate"),
                        "optimized_profit_factor": run["optimized_result"].get("profit_factor"),
                        "optimized_expectancy": run["optimized_result"].get("net_expectancy_per_trade"),
                        "optimized_trades": run["optimized_result"].get("n_trades"),
                        "delta_score": _candidate_delta_score(
                            run["optimized_result"], symbol_baseline_result, min_trades=min_trades
                        ),
                    }
                    for run in candidate_runs[:top_n]
                ],
                "recommendation_status": status,
            }
            completed_symbols.append(symbol)
            self._write_fit_progress(
                recommendations=recommendations,
                completed_symbols=completed_symbols,
                coverage=coverage,
                constraints=constraints,
                status="running",
                current_symbol=symbol,
                phase="coin_strategy_surgery_complete",
            )

        payload = {
            "generated_at": _utc_now().isoformat(),
            "status": "complete",
            "days": self.days,
            "objective": self.objective,
            "symbols": self.symbols,
            "constraints": constraints,
            "coverage": coverage,
            "recommendations": recommendations,
        }
        stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        out_path = os.path.join(BACKTEST_DIR, f"spot_coin_strategy_surgery_{stamp}.json")
        _write_json(out_path, payload)
        payload["output_path"] = out_path
        return payload

    def extract_optimal_strategies(
        self,
        *,
        top_n: int = 5,
        min_win_rate: float = 0.52,
        min_profit_factor: float = 1.05,
        min_trades: int = 12,
        family_blend: float = 0.30,
        local_blends: tuple[float, ...] = (0.35, 0.65),
    ) -> dict[str, Any]:
        _ensure_backtest_dir()
        constraints = {
            "min_win_rate": min_win_rate,
            "min_profit_factor": min_profit_factor,
            "min_trades": min_trades,
            "family_blend": family_blend,
            "local_blends": list(local_blends),
        }
        recommendations: dict[str, Any] = {}
        completed_symbols: list[str] = []
        coverage: list[dict[str, Any]] = []
        events_by_symbol: dict[str, list[dict[str, Any]]] = {}
        base_scored: dict[str, list[tuple[ReplayTrial, dict[str, Any]]]] = {}
        self._write_fit_progress(
            recommendations=recommendations,
            completed_symbols=completed_symbols,
            coverage=coverage,
            constraints=constraints,
            status="running",
            phase="strategy_extraction_start",
        )

        for symbol in self.symbols:
            child = SpotReplayOptimizer(
                symbols=[symbol],
                days=self.days,
                objective=self.objective,
                eval_timeframe=self.eval_timeframe,
            )
            events, symbol_coverage = child._load_or_build_events(
                symbol,
                recommendations=recommendations,
                completed_symbols=completed_symbols,
                coverage=coverage,
                constraints=constraints,
            )
            coverage.extend(symbol_coverage)
            events_by_symbol[symbol] = events
            base_scored[symbol] = sorted(
                child._score_symbol_trials(
                    symbol,
                    events,
                    _symbol_trial_bank(symbol),
                    min_win_rate=min_win_rate,
                    min_profit_factor=min_profit_factor,
                    min_trades=min_trades,
                ),
                key=lambda item: (
                    float(item[1].get("utility_score", -999.0)),
                    float(item[1].get("net_expectancy_per_trade", -999.0)),
                    float(item[1].get("profit_factor", 0.0)),
                    float(item[1].get("n_trades", 0)),
                ),
                reverse=True,
            )

        family_centroids: dict[str, ReplayTrial] = {}
        for family in sorted({_symbol_family(symbol) for symbol in self.symbols}):
            family_items = [
                base_scored[symbol][0]
                for symbol in self.symbols
                if _symbol_family(symbol) == family and base_scored.get(symbol)
            ]
            if len(family_items) < 2:
                continue
            trials = [trial for trial, _ in family_items]
            weights = [max(float(result.get("utility_score", 0.0)) + 2.0, 0.05) for _, result in family_items]
            family_centroids[family] = _centroid_trial(trials[0], trials, weights)

        for symbol in self.symbols:
            child = SpotReplayOptimizer(
                symbols=[symbol],
                days=self.days,
                objective=self.objective,
                eval_timeframe=self.eval_timeframe,
            )
            base_items = base_scored[symbol]
            anchor_trial = base_items[0][0]
            candidate_trials: list[ReplayTrial] = [trial for trial, _ in base_items]
            if len(base_items) >= 2:
                neighbor_trial = base_items[1][0]
                for alpha in local_blends:
                    candidate_trials.append(_interpolate_trial(anchor_trial, neighbor_trial, alpha))
            family = _symbol_family(symbol)
            if family in family_centroids:
                candidate_trials.append(_interpolate_trial(anchor_trial, family_centroids[family], family_blend))
            deduped = {trial.label(): trial for trial in candidate_trials}
            scored_frontier = [
                result
                for _, result in sorted(
                    child._score_symbol_trials(
                        symbol,
                        events_by_symbol[symbol],
                        list(deduped.values()),
                        min_win_rate=min_win_rate,
                        min_profit_factor=min_profit_factor,
                        min_trades=min_trades,
                    ),
                    key=lambda item: (
                        float(item[1].get("utility_score", -999.0)),
                        float(item[1].get("net_expectancy_per_trade", -999.0)),
                        float(item[1].get("profit_factor", 0.0)),
                        float(item[1].get("n_trades", 0)),
                    ),
                    reverse=True,
                )
            ]
            viable_frontier = [
                result
                for result in scored_frontier
                if int(result.get("n_trades", 0)) >= max(1, min_trades // 2)
            ]
            frontier = viable_frontier or scored_frontier
            best = frontier[0] if frontier else None
            status = "inactive"
            if best:
                if (
                    float(best.get("net_expectancy_per_trade", -999.0)) > 0.0
                    and float(best.get("profit_factor", 0.0)) >= 1.0
                    and int(best.get("n_trades", 0)) >= min_trades
                ):
                    status = "active_candidate"
                elif (
                    float(best.get("net_expectancy_per_trade", -999.0)) > 0.0
                    and float(best.get("profit_factor", 0.0)) >= 1.0
                ):
                    status = "incubate_small_sample"
                elif int(best.get("near_misses", 0)) >= max(25, min_trades):
                    status = "tighten_or_hold"
            recommendations[symbol] = {
                "symbol": symbol,
                "strategy_family": family,
                "event_count": len(events_by_symbol[symbol]),
                "recommended_strategy": best,
                "frontier": frontier[:top_n],
                "viable_frontier_count": len(viable_frontier),
                "family_centroid_used": family in family_centroids,
                "recommendation_status": status,
            }
            completed_symbols.append(symbol)
            self._write_fit_progress(
                recommendations=recommendations,
                completed_symbols=completed_symbols,
                coverage=coverage,
                constraints=constraints,
                status="running",
                current_symbol=symbol,
                phase="strategy_extract_complete",
            )

        payload = {
            "generated_at": _utc_now().isoformat(),
            "status": "complete",
            "days": self.days,
            "objective": self.objective,
            "symbols": self.symbols,
            "constraints": constraints,
            "coverage": coverage,
            "recommendations": recommendations,
        }
        stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        out_path = os.path.join(BACKTEST_DIR, f"spot_strategy_extract_{stamp}.json")
        _write_json(out_path, payload)
        payload["output_path"] = out_path
        return payload


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Research-grade spot scalp replay optimizer")
    parser.add_argument("--symbol", default=None, help="Optional single symbol (BTC, ETH, SOL...)")
    parser.add_argument("--days", type=int, default=SPOT_REPLAY_LOOKBACK_DAYS)
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--fit-symbol-strategies", action="store_true")
    parser.add_argument("--extract-optimal-strategies", action="store_true")
    parser.add_argument("--optimize-coin-strategies", action="store_true")
    parser.add_argument("--min-win-rate", type=float, default=0.52)
    parser.add_argument("--min-profit-factor", type=float, default=1.05)
    parser.add_argument("--min-trades", type=int, default=12)
    args = parser.parse_args()

    symbols = [_normalize_symbol(args.symbol)] if args.symbol else list(SPOT_SYMBOLS)
    optimizer = SpotReplayOptimizer(symbols=symbols, days=args.days)
    if args.fit_symbol_strategies:
        result = optimizer.fit_symbol_strategies(
            top_n=args.top,
            min_win_rate=args.min_win_rate,
            min_profit_factor=args.min_profit_factor,
            min_trades=args.min_trades,
        )
    elif args.optimize_coin_strategies:
        result = optimizer.optimize_coin_strategies(
            top_n=args.top,
            min_win_rate=args.min_win_rate,
            min_profit_factor=args.min_profit_factor,
            min_trades=args.min_trades,
        )
    elif args.extract_optimal_strategies:
        result = optimizer.extract_optimal_strategies(
            top_n=args.top,
            min_win_rate=args.min_win_rate,
            min_profit_factor=args.min_profit_factor,
            min_trades=args.min_trades,
        )
    else:
        result = optimizer.run(top_n=args.top)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _cli()
