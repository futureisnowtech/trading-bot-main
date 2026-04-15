from __future__ import annotations

from tests.proof.support import insert_trade, insert_trade_attribution


def _seed_strict_attr_rows(db_path, count: int) -> None:
    for i in range(count):
        insert_trade_attribution(
            db_path,
            trade_ref=f"trade:{i}",
            symbol="BTCUSDT",
            source="clean_paper_v10",
            pnl_usd=1.0,
            pnl_pct=0.01,
            fee_usd=0.1,
            signals_json='{"squeeze_breakout": true}',
            lesson="Trusted row",
            exit_type="target_hit",
        )


def test_go_live_audit_flags_negative_sample_and_short_drag(proof_runtime):
    from scripts.truth_audit_lib import build_go_live_audit

    for _ in range(5):
        insert_trade(
            proof_runtime.db_path,
            symbol="GOOD",
            action="SELL",
            source="clean_paper_v10",
            pnl_usd=1.0,
            fee_usd=0.1,
            notes="close partial=100% reason=trailing_stop setup=supertrend_cross_long regime=TRENDING",
        )
    for _ in range(5):
        insert_trade(
            proof_runtime.db_path,
            symbol="BAD",
            action="BUY",
            source="clean_paper_v10",
            pnl_usd=-1.0,
            fee_usd=0.1,
            notes="close partial=100% reason=hard_stop setup=wae_explosion_short regime=TRENDING",
        )
    for _ in range(20):
        insert_trade(
            proof_runtime.db_path,
            symbol="MEH",
            action="SELL",
            source="clean_paper_v10",
            pnl_usd=-0.2,
            fee_usd=0.1,
            notes="close partial=100% reason=thesis_invalidated setup= regime=RANGING",
        )

    audit = build_go_live_audit(str(proof_runtime.db_path))
    codes = {item["code"] for item in audit["go_live"]["recommendations"]}

    assert audit["go_live"]["primary_recommendation"] == "continue_paper_only"
    assert "suppress_or_deweight_shorts" in codes
    assert "hard_stop_is_destructive" not in codes  # sample below threshold
    assert "thesis_invalidated_not_trustworthy" in codes
    assert "do_not_promote_live_weights" in codes


def test_go_live_audit_can_return_keep_as_is_on_clear_positive_truth(proof_runtime):
    from scripts.truth_audit_lib import build_go_live_audit

    for _ in range(30):
        insert_trade(
            proof_runtime.db_path,
            symbol="BTCUSDT",
            action="SELL",
            source="clean_paper_v10",
            pnl_usd=1.0,
            fee_usd=0.05,
            notes="close partial=100% reason=trailing_stop setup=supertrend_cross_long regime=TRENDING",
        )

    _seed_strict_attr_rows(proof_runtime.db_path, 25)

    audit = build_go_live_audit(str(proof_runtime.db_path))

    assert audit["go_live"]["primary_recommendation"] == "keep_as_is"
    assert audit["go_live"]["status"] == "GREEN"
