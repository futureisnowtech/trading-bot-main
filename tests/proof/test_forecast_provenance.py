"""Proof that the forecast justifying an entry is persisted with it.

forecast_resolutions has carried q_hat columns for a long time and they are NULL
on all 76,458 rows, because resolution_sync runs off weather observations and
never sees the model output. Without the forecast stored next to the entry,
"were our entries actually calibrated?" is unanswerable -- which is why every
threshold decision to date has been made blind.
"""
import sqlite3


def test_entry_persists_the_forecast_that_justified_it(tmp_path):
    """q_hat on the position is what makes entry calibration computable."""
    from forecast.db import init_forecast_db, sync_open_forecast_position

    db = str(tmp_path / "t.db")
    init_forecast_db(db)

    sync_open_forecast_position(
        ticker="KXHIGHNY-26JUN01-T75", qty=3, entry_price=0.62, side="NO",
        q_hat=0.0821, ev_at_entry=0.1382, db_path=db,
    )
    with sqlite3.connect(db) as c:
        row = c.execute(
            "SELECT q_hat, ev_at_entry FROM forecast_positions WHERE ticker=?",
            ("KXHIGHNY-26JUN01-T75",),
        ).fetchone()
    assert row == (0.0821, 0.1382)


def test_reconciliation_cannot_blank_out_a_recorded_forecast(tmp_path):
    """Adopting a broker position must not erase the strategy's q_hat.

    The reconciler upserts the same ticker with no forecast attached; a naive
    upsert would overwrite q_hat with NULL and silently destroy the record.
    """
    from forecast.db import init_forecast_db, sync_open_forecast_position

    db = str(tmp_path / "t.db")
    init_forecast_db(db)

    sync_open_forecast_position(
        ticker="KXLOWTDAL-26AUG17-T81", qty=5, entry_price=0.37, side="NO",
        q_hat=0.44, ev_at_entry=0.13, db_path=db,
    )
    # Reconciler path: same ticker, no forecast.
    sync_open_forecast_position(
        ticker="KXLOWTDAL-26AUG17-T81", qty=5, entry_price=0.38, side="NO",
        basis_quality="ESTIMATED", db_path=db,
    )
    with sqlite3.connect(db) as c:
        q_hat, ev, price = c.execute(
            "SELECT q_hat, ev_at_entry, entry_price FROM forecast_positions WHERE ticker=?",
            ("KXLOWTDAL-26AUG17-T81",),
        ).fetchone()
    assert q_hat == 0.44, "reconciliation erased the forecast"
    assert ev == 0.13
    assert price == 0.38, "reconciliation should still refresh the broker-truth price"
