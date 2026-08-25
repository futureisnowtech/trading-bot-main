from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_fill_recovery_uses_current_v2_fields_and_keeps_yes_leg_basis(monkeypatch):
    import forecast.db as fdb

    broker = MagicMock()
    broker._request.return_value = {
        "fills": [
            {
                "ticker": "KXLOWTOKC-26AUG24-T78",
                "action": "buy",
                "outcome_side": "no",
                "yes_price_dollars": "0.6200",
                "no_price_dollars": "0.3800",
                "count_fp": "1.00",
            },
            # An opposite-outcome close/hedge must not contaminate the NO basis.
            {
                "ticker": "KXLOWTOKC-26AUG24-T78",
                "action": "buy",
                "outcome_side": "yes",
                "yes_price_dollars": "0.7000",
                "no_price_dollars": "0.3000",
                "count_fp": "4.00",
            },
        ]
    }
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

    price = fdb._fetch_confirmed_entry_price_from_fills(
        broker, "KXLOWTOKC-26AUG24-T78", "NO"
    )

    assert price == 0.62


def test_fill_recovery_derives_yes_leg_from_no_price_when_needed(monkeypatch):
    import forecast.db as fdb

    broker = MagicMock()
    broker._request.return_value = {
        "fills": [
            {
                "action": "buy",
                "outcome_side": "no",
                "no_price_dollars": "0.3800",
                "count_fp": "2.00",
            }
        ]
    }
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

    price = fdb._fetch_confirmed_entry_price_from_fills(
        broker, "KXLOWTOKC-26AUG24-T78", "NO"
    )

    assert price == 0.62


def test_reconcile_forecast_positions_adopts_and_closes(proof_runtime):
    import forecast.db as fdb

    db = str(proof_runtime.db_path)
    fdb.init_forecast_db(db_path=db)
    fdb.insert_forecast_position(
        ticker="KXHIGHOLD-26JUN05-B80.5",
        qty=2,
        entry_price=0.33,
        side="YES",
        db_path=db,
    )

    summary = fdb.reconcile_forecast_positions(
        [
            {
                "local_symbol": "KXHIGHLAX-26JUN05-B69.5",
                "qty": 43.0,
                "entry_price": 0.16,
                "side": "YES",
            }
        ],
        db_path=db,
    )

    open_positions = fdb.get_open_forecast_positions(db_path=db)
    assert summary["adopted"] == 1
    assert summary["closed"] == 1
    assert len(open_positions) == 1
    assert open_positions[0]["ticker"] == "KXHIGHLAX-26JUN05-B69.5"
    assert open_positions[0]["qty"] == 43.0


def test_run_reconciliation_syncs_broker_positions_into_db(proof_runtime, monkeypatch):
    import forecast.db as fdb
    import runtime.position_reconciler as pr

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(pr, "DB_PATH", db, raising=False)
    fdb.init_forecast_db(db_path=db)

    broker = MagicMock()
    broker.is_connected.return_value = True
    broker.get_positions.return_value = [
        {
            "local_symbol": "KXHIGHLAX-26JUN05-B69.5",
            "qty": 43.0,
            "entry_price": 0.16,
            "side": "YES",
        }
    ]

    with patch("execution.kalshi_broker.get_kalshi_broker", return_value=broker):
        pr.run_reconciliation(db_path=db)

    open_positions = fdb.get_open_forecast_positions(db_path=db)
    assert len(open_positions) == 1
    assert open_positions[0]["ticker"] == "KXHIGHLAX-26JUN05-B69.5"
    broker.sync_positions.assert_called_once()
