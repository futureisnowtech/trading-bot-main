from __future__ import annotations

import sqlite3


def test_analyze_closed_trade_updates_attribution_and_signal_stats(proof_runtime):
    """
    v14.0: source must be a trusted live/paper source for Bayesian signal_stats
    to be updated.  'replay_harness' is intentionally excluded (fail-closed on
    replay/synthetic sources).  Use 'clean_paper_v10' to test the Bayesian path.
    """
    from learning.post_trade_analyzer import analyze_closed_trade

    result = analyze_closed_trade(
        symbol="BTCUSDT",
        strategy="v10_perp",
        entry_price=100.0,
        exit_price=104.0,
        qty=1.0,
        fee_usd=0.5,
        entry_ts="2026-04-10T09:30:00+00:00",
        exit_ts="2026-04-10T10:30:00+00:00",
        exit_reason="target_hit",
        market_data_at_entry={
            "regime": "TRENDING",
            "primary_setup": "squeeze_breakout",
            "conviction_score": 72.0,
        },
        source="clean_paper_v10",  # trusted source — must reach Bayesian weights
        composite_score=72.0,
    )

    assert result["won"] is True
    assert "squeeze_breakout" in result["active_signals"]
    assert result["attr_id"] > 0

    with sqlite3.connect(proof_runtime.db_path) as conn:
        trade_attribution_row = conn.execute(
            "SELECT symbol, exit_reason FROM trade_attribution ORDER BY id DESC LIMIT 1"
        ).fetchone()
        signal_rows = conn.execute(
            """
            SELECT signal_name, regime, fires
            FROM signal_stats
            WHERE signal_name='squeeze_breakout'
            ORDER BY regime
            """
        ).fetchall()

    assert trade_attribution_row == ("BTCUSDT", "target_hit")
    assert ("squeeze_breakout", "trending", 1) in signal_rows
    assert ("squeeze_breakout", "any", 1) in signal_rows


def test_replay_source_blocked_from_signal_stats(proof_runtime):
    """
    v18.16 invariant: 'replay_harness' source must NOT update signal_stats.
    Attribution row is written (for audit) but Bayesian weights are not touched.
    """
    from learning.post_trade_analyzer import analyze_closed_trade

    result = analyze_closed_trade(
        symbol="ETHUSDT",
        strategy="v10_perp",
        entry_price=200.0,
        exit_price=210.0,
        qty=1.0,
        fee_usd=0.5,
        entry_ts="2026-04-10T09:30:00+00:00",
        exit_ts="2026-04-10T10:30:00+00:00",
        exit_reason="target_hit",
        market_data_at_entry={
            "regime": "TRENDING",
            "primary_setup": "wae_explosion",
        },
        source="replay_harness",  # must be excluded — no live weight update
        composite_score=70.0,
    )

    # Attribution row is still written for audit trail
    assert result["attr_id"] > 0

    with sqlite3.connect(proof_runtime.db_path) as conn:
        signal_rows = conn.execute(
            "SELECT signal_name FROM signal_stats WHERE signal_name='wae_explosion'"
        ).fetchall()

    assert len(signal_rows) == 0, (
        "replay_harness source must never update signal_stats — "
        "synthetic/replay data must not contaminate live Bayesian weights"
    )
