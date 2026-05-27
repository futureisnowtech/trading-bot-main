from __future__ import annotations

import sqlite3

from tests.proof.support import build_candidate, build_features


def test_replay_harness_runs_full_pipeline_and_writes_attribution(proof_runtime):
    from verification.replay import run_replay

    result = run_replay(
        candidate=build_candidate(primary_setup=""),
        features=build_features(),
        account_balance=5_000.0,
        current_balance=5_000.0,
        deployed_usd=250.0,
        margin_usd=500.0,
        live_trade_days=0,
        kelly_fraction=0.33,
        exit_price=104.0,
        exit_reason="target_hit",
    )

    assert result["signal"]["approved"] is True
    assert result["economics"]["approved"] is True
    assert result["sizing"]["position_usd"] > 0
    assert result["risk"]["approved"] is True
    assert result["staged"] is True
    assert result["attribution"]["attr_id"] > 0

    with sqlite3.connect(proof_runtime.db_path) as conn:
        attributed = conn.execute(
            "SELECT COUNT(*) FROM trade_attribution WHERE source='replay_harness'"
        ).fetchone()[0]
    assert attributed == 1


def test_replay_harness_stops_before_attribution_when_economics_vetoes(proof_runtime):
    from verification.replay import run_replay

    result = run_replay(
        candidate=build_candidate(
            symbol="LOWVOLUSDT",
            volume_24h_usd=1_000_000.0,
            vol_usd=1_000_000.0,
        ),
        features=build_features(),
        account_balance=5_000.0,
        current_balance=5_000.0,
        kelly_fraction=0.33,
        exit_price=104.0,
    )

    assert result["signal"]["approved"] is True
    assert result["economics"]["approved"] is False
    assert result["staged"] is False
    assert result["attribution"] is None
