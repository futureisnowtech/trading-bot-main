from __future__ import annotations

from unittest.mock import MagicMock, patch


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
