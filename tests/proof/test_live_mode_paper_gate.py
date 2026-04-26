"""
tests/proof/test_live_mode_paper_gate.py

Invariant proof tests for the live-mode / paper-gate bug class.

Root cause: dashboard used hardcoded paper=1 SQL and config.PAPER_TRADING
instead of reading system_runtime_state.process_mode (the runtime source
of truth). This allowed paper-era open positions and P&L to bleed into the
live dashboard even after go_live.py completed.

Each test names the exact invariant it protects. If any test fails, the
dashboard will show stale paper data on a live account.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from support import insert_open_position, insert_trade, upsert_runtime_state


# ══════════════════════════════════════════════════════════════════════════════
# _runtime_paper_flag — the single source-of-truth helper in db.py
# ══════════════════════════════════════════════════════════════════════════════


def test_runtime_paper_flag_live_db_overrides_paper_config(proof_runtime, monkeypatch):
    """
    THE KEY INVARIANT.

    If system_runtime_state says 'live', _runtime_paper_flag() must return 0
    even if config.PAPER_TRADING is True (stale config from a previous restart).

    This is the exact scenario after go_live.py: the .env is updated but the
    dashboard process may have cached config.PAPER_TRADING=True at import time.
    The runtime DB must win.
    """
    import config

    monkeypatch.setattr(config, "PAPER_TRADING", True, raising=False)
    upsert_runtime_state(proof_runtime.db_path, process_mode="live")

    from db import _runtime_paper_flag

    assert _runtime_paper_flag() == 0, (
        "_runtime_paper_flag() must return 0 when DB says 'live', "
        "regardless of config.PAPER_TRADING value"
    )


def test_runtime_paper_flag_paper_db_returns_1(proof_runtime):
    """
    When system_runtime_state says 'paper', flag must be 1.
    (Existing tests already rely on this via the config fallback; this
    tests the explicit DB path.)
    """
    upsert_runtime_state(proof_runtime.db_path, process_mode="paper")

    from db import _runtime_paper_flag

    assert _runtime_paper_flag() == 1


def test_runtime_paper_flag_falls_back_to_config_when_no_db_row(
    proof_runtime, monkeypatch
):
    """
    When system_runtime_state has no rows (fresh DB, table may not exist),
    _runtime_paper_flag() must fall back to config.PAPER_TRADING.
    The conftest patches config.PAPER_TRADING=True so fallback returns 1.
    """
    # Do NOT call upsert_runtime_state — leave table absent
    from db import _runtime_paper_flag

    assert _runtime_paper_flag() == 1, (
        "With no runtime state row and config.PAPER_TRADING=True (patched by fixture), "
        "flag must be 1 (paper)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# get_open_positions — paper=1 positions must not appear in live dashboard
# ══════════════════════════════════════════════════════════════════════════════


def test_paper_open_positions_not_shown_in_live_dashboard(proof_runtime):
    """
    After go_live.py, 18 paper=1 positions remain in open_positions from
    the paper trading phase.  With runtime=live, get_open_positions() must
    return an empty list so they do not appear in the live dashboard.
    """
    insert_open_position(proof_runtime.db_path, symbol="BTCUSDT", paper=1)
    insert_open_position(
        proof_runtime.db_path, symbol="ETHUSDT", strategy="crypto_perp2", paper=1
    )
    upsert_runtime_state(proof_runtime.db_path, process_mode="live")

    from data.positions import get_open_positions

    positions = get_open_positions()
    assert positions == [], (
        f"Live dashboard must not show paper=1 positions. Got: {positions}"
    )


def test_live_open_positions_appear_in_live_dashboard(proof_runtime, monkeypatch):
    """
    A live position must appear in the live dashboard when the broker snapshot
    confirms it. DB metadata may enrich the row, but the exchange snapshot is
    the gating truth.
    """
    insert_open_position(proof_runtime.db_path, symbol="BTCUSDT", paper=0)
    upsert_runtime_state(proof_runtime.db_path, process_mode="live")

    from data import positions as positions_mod

    monkeypatch.setattr(
        positions_mod,
        "_get_live_coinbase_perp_positions",
        lambda: {
            "BTCUSDT": {
                "direction": "LONG",
                "qty": 1.0,
                "entry_price": 10000.0,
                "current_price": 10100.0,
                "unrealized_pnl": 100.0,
            }
        },
        raising=False,
    )
    monkeypatch.setattr(
        positions_mod, "_get_live_coinbase_spot_positions", lambda: [], raising=False
    )

    positions = positions_mod.get_open_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTCUSDT"
    assert positions[0]["paper"] == 0


def test_paper_positions_still_shown_in_paper_dashboard(proof_runtime):
    """
    In paper mode, paper=1 positions must still appear (regression guard).
    """
    insert_open_position(proof_runtime.db_path, symbol="XRPUSDT", paper=1)
    upsert_runtime_state(proof_runtime.db_path, process_mode="paper")

    from data.positions import get_open_positions

    positions = get_open_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "XRPUSDT"


def test_live_perp_positions_fail_closed_when_broker_snapshot_unavailable(
    proof_runtime, monkeypatch
):
    """
    Live perp dashboard truth must never fall back to stale DB rows when the
    broker snapshot is unavailable. Unknown is safer than phantom.
    """
    insert_open_position(
        proof_runtime.db_path, symbol="SOL", strategy="v10_perp", paper=0
    )
    upsert_runtime_state(proof_runtime.db_path, process_mode="live")

    from data import positions as positions_mod

    monkeypatch.setattr(
        positions_mod, "_get_live_coinbase_perp_positions", lambda: None, raising=False
    )
    monkeypatch.setattr(
        positions_mod, "_crypto_live_snapshot_enabled", lambda: True, raising=False
    )

    assert positions_mod.get_perp_positions() == []


# ══════════════════════════════════════════════════════════════════════════════
# No cross-contamination: live realized P&L must not mix with paper positions
# ══════════════════════════════════════════════════════════════════════════════


def test_live_account_unrealized_pnl_excludes_paper_positions(proof_runtime):
    """
    get_account() computes unrealized PnL from get_open_positions().
    With runtime=live, paper=1 positions must not contribute to unrealized PnL
    even if they exist in the DB.

    This is the exact mixing bug: live realized=0, paper unrealized=+$500 →
    dashboard shows a phantom $500 gain that isn't real.
    """
    # Insert a paper=1 position (large hypothetical gain if priced)
    insert_open_position(
        proof_runtime.db_path,
        symbol="BTCUSDT",
        paper=1,
        entry=10000.0,  # would show big gain at any real price
        qty=1.0,
    )
    # No live trades — realized PnL = 0
    upsert_runtime_state(proof_runtime.db_path, process_mode="live")

    from data.account import get_account

    equity, is_paper, base = get_account()
    # equity = base + realized(0) + unrealized(0, because open_positions returns empty)
    # Allow small floating point delta; should be ≈ base with no phantom gain
    assert abs(equity - base) < 1.0, (
        f"Paper=1 open position must not contribute to live account equity. "
        f"equity={equity}, base={base}"
    )


def test_live_performance_stats_exclude_paper_trades(proof_runtime):
    """
    get_performance_stats() must not include paper=1 trades when runtime=live.
    Before the fix, all 923 paper trades leaked into the live stats.
    """
    insert_trade(
        proof_runtime.db_path,
        pnl_usd=100.0,
        won=1,
        paper=1,
        source="clean_paper_v10",
    )
    upsert_runtime_state(proof_runtime.db_path, process_mode="live")

    from data.performance import get_performance_stats

    stats = get_performance_stats()
    assert stats["closes"] == 0, (
        f"paper=1 trades must not appear in live performance stats. "
        f"Got closes={stats['closes']}"
    )


def test_live_performance_stats_include_live_trades(proof_runtime):
    """
    get_performance_stats() must include paper=0 trades when runtime=live.
    """
    insert_trade(
        proof_runtime.db_path,
        pnl_usd=50.0,
        won=1,
        paper=0,
        source="live_v10",
    )
    upsert_runtime_state(proof_runtime.db_path, process_mode="live")

    from data.performance import get_performance_stats

    stats = get_performance_stats()
    assert stats["closes"] == 1
    assert stats["wins"] == 1


def test_live_trade_log_excludes_paper_trades(proof_runtime):
    """
    get_trade_log() must not include paper=1 rows in live mode.
    """
    insert_trade(proof_runtime.db_path, pnl_usd=25.0, won=1, paper=1)
    upsert_runtime_state(proof_runtime.db_path, process_mode="live")

    from data.account import get_trade_log

    log = get_trade_log()
    assert log == [], "paper=1 trades must not appear in live trade log"
