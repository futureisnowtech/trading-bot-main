from __future__ import annotations

from tests.proof.support import insert_trade, insert_trade_attribution


def test_net_truth_audit_excludes_test_closes_and_dirty_sources_from_headline(
    proof_runtime,
):
    from scripts.truth_audit_lib import build_net_truth_audit

    insert_trade(
        proof_runtime.db_path,
        symbol="BTCUSDT",
        source="clean_paper_v10",
        pnl_usd=5.0,
        fee_usd=1.0,
        notes="close partial=100% reason=trailing_stop setup=supertrend_cross_long regime=TRENDING",
    )
    insert_trade(
        proof_runtime.db_path,
        symbol="BTCUSDT",
        source="clean_paper_v10",
        pnl_usd=10.0,
        fee_usd=1.0,
        notes="close partial=100% reason=force_test_close setup=supertrend_cross_long regime=TRENDING",
    )
    insert_trade(
        proof_runtime.db_path,
        symbol="ETHUSDT",
        source="paper_v10",
        pnl_usd=-50.0,
        fee_usd=1.0,
        notes="close partial=100% reason=hard_stop setup=wae_explosion_short regime=RANGING",
    )

    audit = build_net_truth_audit(str(proof_runtime.db_path))

    assert audit["headline"]["overall"]["trade_count"] == 1
    assert audit["headline"]["overall"]["net_pnl"] == 4.0
    assert audit["headline"]["raw_trade_surface"]["trade_count"] == 2
    assert audit["headline"]["raw_trade_surface"]["net_pnl"] == -47.0
    assert audit["trust_counts"]["closed_trades"]["synthetic_test"] == 1
    assert audit["trust_counts"]["closed_trades"]["low_trust_source"] == 1


def test_net_truth_audit_counts_short_winners_and_fee_drag_and_exit_rollups(
    proof_runtime,
):
    from scripts.truth_audit_lib import build_net_truth_audit

    insert_trade(
        proof_runtime.db_path,
        symbol="ETHUSDT",
        action="BUY",
        source="clean_paper_v10",
        pnl_usd=5.0,
        fee_usd=0.5,
        notes="close partial=100% reason=trailing_stop setup=ichimoku_cloud_breakout_short regime=TRENDING",
    )
    insert_trade(
        proof_runtime.db_path,
        symbol="BTCUSDT",
        action="SELL",
        source="clean_paper_v10",
        pnl_usd=-2.0,
        fee_usd=0.5,
        notes="close partial=100% reason=hard_stop setup=supertrend_cross_long regime=TRENDING",
    )

    audit = build_net_truth_audit(str(proof_runtime.db_path))
    by_direction = {row["direction"]: row for row in audit["headline"]["by_direction"]}
    by_exit = {row["exit_type"]: row for row in audit["headline"]["by_exit_type"]}

    assert by_direction["SHORT"]["net_pnl"] == 4.5
    assert by_direction["LONG"]["net_pnl"] == -2.5
    assert round(audit["headline"]["overall"]["fee_drag_pct"], 2) == 14.29
    assert by_exit["trailing_stop"]["net_pnl"] == 4.5
    assert by_exit["hard_stop"]["net_pnl"] == -2.5


def test_net_truth_audit_shows_attribution_contamination_delta(proof_runtime):
    from scripts.truth_audit_lib import build_net_truth_audit

    insert_trade(proof_runtime.db_path, source="clean_paper_v10", pnl_usd=2.0, fee_usd=0.5)

    insert_trade_attribution(
        proof_runtime.db_path,
        source="clean_paper_v10",
        trade_ref="trade:1",
        pnl_usd=4.0,
        fee_usd=0.5,
        pnl_pct=0.04,
        signals_json='{"squeeze_breakout": true}',
        lesson="Trusted row",
    )
    insert_trade_attribution(
        proof_runtime.db_path,
        source="clean_paper_v10",
        trade_ref="",
        pnl_usd=-1.0,
        fee_usd=0.5,
        pnl_pct=-0.01,
        signals_json='{"squeeze_breakout": true}',
        lesson="Missing lineage",
    )
    insert_trade_attribution(
        proof_runtime.db_path,
        source="replay_harness",
        trade_ref="trade:replay",
        pnl_usd=10.0,
        fee_usd=0.5,
        pnl_pct=0.10,
        signals_json='{"squeeze_breakout": true}',
        lesson="Replay row",
    )
    insert_trade_attribution(
        proof_runtime.db_path,
        source="clean_paper_v10",
        trade_ref="trade:outlier",
        pnl_usd=500.0,
        fee_usd=0.5,
        pnl_pct=5.0,
        signals_json='{"squeeze_breakout": true}',
        lesson="Outlier row",
    )
    audit = build_net_truth_audit(str(proof_runtime.db_path))
    strict = audit["diagnostics"]["strict_signal_diagnostics"]
    relaxed = audit["diagnostics"]["relaxed_signal_diagnostics"]
    raw_attr = audit["diagnostics"]["raw_attribution_surface"]

    assert strict["count"] == 1
    assert strict["overall"]["net_pnl"] == 3.5
    assert relaxed["count"] == 2
    assert raw_attr["count"] == 3
    assert audit["trust_counts"]["trade_attribution"]["missing_trade_ref"] >= 1
    assert audit["trust_counts"]["trade_attribution"]["synthetic_replay"] >= 1
