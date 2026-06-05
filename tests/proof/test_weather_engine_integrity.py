from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch


def test_evaluate_all_contracts_routes_no_side_to_put_contract():
    import forecast.strategy_engine as se

    yes_contract = {
        "id": 1,
        "market_id": 7,
        "local_symbol": "KXLOWNY-26JUN06-T70",
        "right": "C",
        "strike": 70.0,
        "last_trade_at": "20260606",
    }
    no_contract = {
        "id": 2,
        "market_id": 7,
        "local_symbol": "KXLOWNY-26JUN06-T70",
        "right": "P",
        "strike": 70.0,
        "last_trade_at": "20260606",
    }

    result = se.StrategyResult(
        strategy_family="weather_ensemble",
        side="NO",
        q_hat=0.40,
        ev=0.11,
        ev_yes=-0.02,
        ev_no=0.11,
        confidence=0.78,
        uncertainty_penalty=0.0,
        econ_approved=True,
        veto_reason="",
        position_fraction=0.02,
        position_contracts=2,
        top_factors=["mock"],
    )

    with patch.object(se, "evaluate_contract", return_value=result):
        ranked = se.evaluate_all_contracts(
            active_contracts=[yes_contract, no_contract],
            get_bars_fn=lambda *_args, **_kwargs: [],
            get_quotes_fn=lambda *_args, **_kwargs: {
                "yes_quote": {"ask": 0.41, "mid": 0.40, "spread": 0.02, "ts": datetime.now(timezone.utc).isoformat()},
                "no_quote": {"ask": 0.59, "mid": 0.60, "spread": 0.02, "ts": datetime.now(timezone.utc).isoformat()},
            },
            open_positions=[],
        )

    assert ranked
    assert ranked[0]["result"].side == "NO"
    assert ranked[0]["contract"]["right"] == "P"


def test_evaluate_all_contracts_applies_no_side_hedge_guard():
    import forecast.strategy_engine as se

    yes_contract = {
        "id": 1,
        "market_id": 7,
        "local_symbol": "KXLOWNY-26JUN06-T70",
        "right": "C",
        "strike": 70.0,
        "last_trade_at": "20260606",
    }
    no_contract = {
        "id": 2,
        "market_id": 7,
        "local_symbol": "KXLOWNY-26JUN06-T70",
        "right": "P",
        "strike": 70.0,
        "last_trade_at": "20260606",
    }

    result = se.StrategyResult(
        strategy_family="weather_ensemble",
        side="NO",
        q_hat=0.40,
        ev=0.11,
        ev_yes=-0.02,
        ev_no=0.11,
        confidence=0.78,
        uncertainty_penalty=0.0,
        econ_approved=True,
        veto_reason="",
        position_fraction=0.02,
        position_contracts=2,
        top_factors=["mock"],
    )

    with patch.object(se, "evaluate_contract", return_value=result):
        ranked = se.evaluate_all_contracts(
            active_contracts=[yes_contract, no_contract],
            get_bars_fn=lambda *_args, **_kwargs: [],
            get_quotes_fn=lambda *_args, **_kwargs: {
                "yes_quote": {"ask": 0.41, "mid": 0.40, "spread": 0.02, "ts": datetime.now(timezone.utc).isoformat()},
                "no_quote": {"ask": 0.59, "mid": 0.60, "spread": 0.02, "ts": datetime.now(timezone.utc).isoformat()},
            },
            open_positions=[{"local_symbol": "KXLOWNY-26JUN06-T70", "side": "YES"}],
        )

    assert ranked
    assert ranked[0]["result"].econ_approved is False
    assert "hedge_guard" in ranked[0]["result"].veto_reason


def test_get_paired_quotes_uses_latest_per_side(proof_runtime):
    from forecast.db import init_forecast_db, insert_quote, upsert_contract, upsert_market
    from forecast.quote_harvester import get_paired_quotes

    db = str(proof_runtime.db_path)
    init_forecast_db(db_path=db)
    market_id = upsert_market("KXHIGHNY", "NY High", db_path=db)
    yes_id = upsert_contract(
        market_id=market_id,
        local_symbol="KXHIGHNY-26JUN05-T85",
        right="C",
        strike=85.0,
        last_trade_at="20260605",
        db_path=db,
    )
    no_id = upsert_contract(
        market_id=market_id,
        local_symbol="KXHIGHNY-26JUN05-T85",
        right="P",
        strike=85.0,
        last_trade_at="20260605",
        db_path=db,
    )

    insert_quote(yes_id, "2026-06-04T10:00:00+00:00", 0.40, 0.42, 10, 10, 0.41, 0.02, 0.41, "YES", db_path=db)
    insert_quote(yes_id, "2026-06-04T10:02:00+00:00", 0.44, 0.46, 10, 10, 0.45, 0.02, 0.45, "YES", db_path=db)
    insert_quote(no_id, "2026-06-04T10:01:00+00:00", 0.54, 0.56, 10, 10, 0.55, 0.02, 0.55, "NO", db_path=db)

    pair = get_paired_quotes(market_id, 85.0, "20260605", db_path=db)

    assert pair["yes_quote"]["mid"] == 0.45
    assert pair["no_quote"]["mid"] == 0.55


def test_time_decay_exit_requires_little_remaining_edge():
    from forecast.runner import _should_time_decay_exit

    assert _should_time_decay_exit(12.0, 0.82, 0.01) is True
    assert _should_time_decay_exit(36.0, 0.82, 0.01) is False
    assert _should_time_decay_exit(12.0, 0.55, 0.01) is False


def test_model_invalidation_exit_requires_adverse_delta_and_thin_edge():
    from forecast.runner import _should_model_invalidation_exit

    assert _should_model_invalidation_exit(0.72, 0.58, 0.01) is True
    assert _should_model_invalidation_exit(0.72, 0.66, 0.01) is False
    assert _should_model_invalidation_exit(0.72, 0.58, 0.05) is False
