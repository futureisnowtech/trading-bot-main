"""
tests/proof/test_crypto_tradeability.py — Proof suite for shared crypto tradeability engine (v16.14).

Invariants proved:
  CT-01  BTC LONG with spot lane active → lane=spot
  CT-02  ETH LONG with spot lane active → lane=spot
  CT-03  BTC SHORT never routes to spot → lane=perp or blocked
  CT-04  ETH SHORT never routes to spot → lane=perp or blocked
  CT-05  SOL LONG with spot lane active → lane=spot
  CT-06  XRP LONG with spot lane active → lane=spot
  CT-07  Unknown symbol → blocked with specific reason
  CT-08  SPOT_LANE_ACTIVE=False BTC LONG falls to perp
  CT-09  Spot position already open blocks spot, tries perp
  CT-10  Engine error returns execution_policy_unavailable
  CT-11  Return dict has all 11 required keys
"""

from __future__ import annotations

import os
import sys
import sqlite3
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_REQUIRED_KEYS = {
    "symbol",
    "underlying",
    "lane",
    "recommended_lane",
    "status",
    "auto_executable",
    "manual_executable",
    "blocked_reason",
    "size_block_reason",
    "source_reason",
    "display_label",
}


def _fresh_db(tmp_path) -> str:
    """Create a minimal trades.db with open_positions table."""
    db = str(tmp_path / "trades.db")
    with sqlite3.connect(db) as c:
        c.execute(
            """CREATE TABLE open_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, strategy TEXT, qty REAL, entry REAL,
                stop REAL DEFAULT 0, target REAL DEFAULT 0,
                direction TEXT DEFAULT 'LONG',
                paper INTEGER DEFAULT 1, ts_entry TEXT
            )"""
        )
    return db


# ── CT-01: BTC LONG with spot lane active → lane=spot ─────────────────────────


def test_ct01_btc_long_prefers_spot_when_eligible(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_STRATEGY_SYMBOLS", ["BTC", "ETH"], raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    # Create minimal DB with no open positions
    _fresh_db(tmp_path)

    result = ct.get_crypto_tradeability("BTC", "LONG", live=False, manual=False)
    assert result["lane"] == "spot", (
        f"Expected spot, got {result['lane']} (reason={result['blocked_reason']})"
    )
    assert result["status"] == "executable"
    assert result["underlying"] == "BTC"


# ── CT-02: ETH LONG → lane=spot ───────────────────────────────────────────────


def test_ct02_eth_long_prefers_spot_when_eligible(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_STRATEGY_SYMBOLS", ["BTC", "ETH"], raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    _fresh_db(tmp_path)

    result = ct.get_crypto_tradeability("ETHUSDT", "LONG", live=False, manual=False)
    assert result["lane"] == "spot", f"Expected spot, got {result['lane']}"
    assert result["underlying"] == "ETH"


# ── CT-03: BTC SHORT never routes to spot ─────────────────────────────────────


def test_ct03_btc_short_never_spot(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    _fresh_db(tmp_path)

    result = ct.get_crypto_tradeability("BTC", "SHORT", live=False, manual=False)
    assert result["lane"] != "spot", "SHORT must never route to spot"
    # Paper mode with no existing positions: should be perp-eligible
    assert result["lane"] in ("perp", "blocked")


# ── CT-04: ETH SHORT never routes to spot ─────────────────────────────────────


def test_ct04_eth_short_never_spot(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    _fresh_db(tmp_path)

    result = ct.get_crypto_tradeability("ETH", "SHORT", live=False, manual=False)
    assert result["lane"] != "spot", "SHORT must never route to spot"


# ── CT-05: SOL LONG → spot when spot universe includes SOL ────────────────────


def test_ct05_sol_long_prefers_spot(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_STRATEGY_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    _fresh_db(tmp_path)

    result = ct.get_crypto_tradeability("SOL", "LONG", live=False, manual=False)
    assert result["lane"] == "spot", (
        f"Expected spot for SOL long, got {result['lane']} (reason={result['blocked_reason']})"
    )


# ── CT-06: XRP LONG → spot when spot universe includes XRP ────────────────────


def test_ct06_xrp_long_prefers_spot(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_STRATEGY_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    _fresh_db(tmp_path)

    result = ct.get_crypto_tradeability("XRP", "LONG", live=False, manual=False)
    assert result["lane"] == "spot"


# ── CT-07: Unknown/unsupported symbol → blocked ───────────────────────────────


def test_ct07_unknown_symbol_blocked(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    _fresh_db(tmp_path)

    # PEPE is not in the supported spot scalp universe and not in core perps.
    result = ct.get_crypto_tradeability("PEPE", "LONG", live=False, manual=False)
    assert result["status"] == "blocked"
    assert result["lane"] == "blocked"
    assert result["blocked_reason"] in (
        "perp_symbol_not_supported",
        "unknown_symbol_mapping",
        "spot_symbol_not_allowed",
    ), f"Unexpected reason: {result['blocked_reason']}"


# ── CT-08: SPOT_LANE_ACTIVE=False BTC LONG falls to perp ─────────────────────


def test_ct08_spot_disabled_falls_to_perp(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", False, raising=False)
    monkeypatch.setattr(config, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_STRATEGY_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    _fresh_db(tmp_path)

    result = ct.get_crypto_tradeability("BTC", "LONG", live=False, manual=False)
    # Spot disabled → falls back to perp
    assert result["lane"] == "perp", (
        f"Expected perp fallback when spot disabled, got {result['lane']}"
    )
    assert result["status"] == "executable"


# ── CT-09: Spot position already open blocks spot, tries perp ────────────────


def test_ct09_spot_position_already_open_blocks_spot(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_STRATEGY_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    db = _fresh_db(tmp_path)
    # Seed an open spot BTC position ()
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO open_positions (symbol, strategy, qty, entry, paper) VALUES (?,?,?,?,?)",
            ("BTC", "spot_btc", 0.001, 85000.0, 1),
        )

    result = ct.get_crypto_tradeability("BTC", "LONG", live=False, manual=False, paper=True)
    # Spot is blocked (already open), should fall back to perp
    assert result["lane"] in ("perp", "blocked"), (
        f"Expected perp or blocked, got {result['lane']}"
    )
    if result["lane"] == "perp":
        assert result["status"] == "executable"


def test_ct12_cross_lane_underlying_blocked_when_spot_open(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_STRATEGY_SYMBOLS", ["BTC", "ETH", "SOL", "XRP"], raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False)

    db = _fresh_db(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute(
            "INSERT INTO open_positions (symbol, strategy, qty, entry, paper) VALUES (?,?,?,?,?)",
            ("BTC", "spot_btc", 0.001, 85000.0, 0),
        )

    result = ct.get_crypto_tradeability("BTC", "SHORT", live=True, manual=False)
    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "underlying_exposure_already_open"


# ── CT-10: Engine error returns execution_policy_unavailable ──────────────────


def test_ct10_engine_error_returns_policy_unavailable(monkeypatch):
    import runtime.crypto_tradeability as ct

    # Make config import fail inside the engine
    original_eval = ct._evaluate_tradeability

    def _explode(*args, **kwargs):
        raise RuntimeError("simulated config failure")

    monkeypatch.setattr(ct, "_evaluate_tradeability", _explode, raising=False)

    result = ct.get_crypto_tradeability("BTC", "LONG", live=False, manual=False)
    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "execution_policy_unavailable"
    assert result["lane"] == "blocked"


# ── CT-11: Return dict has all 11 required keys ───────────────────────────────


def test_ct11_return_dict_has_all_required_keys(tmp_path, monkeypatch):
    import config
    import runtime.crypto_tradeability as ct

    monkeypatch.setattr(config, "SPOT_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(config, "SPOT_MAX_DEPLOYED_PCT", 0.40, raising=False)
    monkeypatch.setattr(config, "SPOT_MIN_ORDER_USD", 10.0, raising=False)
    monkeypatch.setattr(
        config,
        "AUTONOMOUS_LIVE_PERP_SYMBOLS",
        ["BTC", "ETH", "SOL", "XRP"],
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "CORE_EXECUTION_UNDERLYINGS",
        {"BTC", "ETH", "SOL", "XRP"},
        raising=False,
    )
    monkeypatch.setattr(
        ct, "_db_path", lambda: str(tmp_path / "trades.db"), raising=False
    )

    _fresh_db(tmp_path)

    for sym, dirn in [
        ("BTC", "LONG"),
        ("ETH", "SHORT"),
        ("SOL", "LONG"),
        ("DOGE", "LONG"),
    ]:
        result = ct.get_crypto_tradeability(sym, dirn, live=False, manual=False)
        missing = _REQUIRED_KEYS - set(result.keys())
        assert not missing, f"{sym} {dirn} result missing keys: {missing}"
