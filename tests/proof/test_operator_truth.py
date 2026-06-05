from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch


def test_live_kalshi_status_is_broker_first_and_surfaces_drift(proof_runtime, monkeypatch):
    import forecast.db as fdb
    import runtime.operator_truth as ot
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)

    fdb.init_forecast_db(db_path=db)
    fdb.upsert_market("KXHIGHLAX", "LA High", db_path=db)
    fdb.insert_forecast_position(
        ticker="KXHIGHNY-26JUN05-B89.5",
        qty=3,
        entry_price=0.42,
        side="YES",
        db_path=db,
    )
    rs.upsert_lane_state(
        "forecast",
        db_path=db,
        connected=1,
        tradable=1,
        health="OK",
        readiness_state="OPERATIONAL",
        snapshot_json=json.dumps({"equity": 165.0, "positions": []}),
    )

    broker = MagicMock()
    broker.is_connected.return_value = True
    broker.get_account_balance.return_value = 165.0
    broker.get_positions.return_value = [
        {
            "local_symbol": "KXHIGHLAX-26JUN05-B69.5",
            "side": "YES",
            "right": "C",
            "qty": 43.0,
            "entry_price": 0.16,
            "forecast_yes_prob": 0.74,
        }
    ]

    with patch("execution.kalshi_broker.get_kalshi_broker", return_value=broker):
        payload = ot.get_live_kalshi_status(db_path=db)

    assert payload["broker_connected"] is True
    assert payload["balance_usd"] == 165.0
    assert payload["broker_positions_count"] == 1
    assert payload["db_positions_count"] == 1
    assert payload["forecast_lane"]["readiness_state"] == "OPERATIONAL"
    assert payload["position_drift"]["has_drift"] is True
    assert payload["position_drift"]["broker_only"][0]["ticker"] == "KXHIGHLAX-26JUN05-B69.5"
    assert payload["position_drift"]["db_only"][0]["ticker"] == "KXHIGHNY-26JUN05-B89.5"
    broker.sync_positions.assert_called_once()


def test_recent_veto_summary_aggregates_reason_counts(proof_runtime, monkeypatch):
    import runtime.operator_truth as ot

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)

    with sqlite3.connect(proof_runtime.db_path) as conn:
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?, 'WARNING', 'ForecastRunner', ?)",
            ("2026-06-04T18:57:46+00:00", "[ForecastRunner] KXHIGHNY vetoed: missing_quotes"),
        )
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?, 'WARNING', 'ForecastRunner', ?)",
            ("2026-06-04T18:58:46+00:00", "[ForecastRunner] KXLOWNY vetoed: missing_quotes"),
        )
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?, 'WARNING', 'ForecastRunner', ?)",
            ("2026-06-04T18:59:46+00:00", "[ForecastRunner] KXRAINNY vetoed: no_strategy_signal"),
        )

    summary = ot.get_recent_veto_summary(db_path=db, lookback_hours=50000)

    assert summary["count"] == 3
    assert summary["top_reasons"][0]["reason"] == "missing_quotes"
    assert summary["top_reasons"][0]["count"] == 2


def test_recent_execution_summary_aggregates_execution_failures(proof_runtime, monkeypatch):
    import runtime.operator_truth as ot

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)

    with sqlite3.connect(proof_runtime.db_path) as conn:
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?, 'WARNING', 'ForecastRunner', ?)",
            ("2026-06-04T19:00:46+00:00", "[ForecastRunner] KXHIGHNY execution_result: fill_or_kill_insufficient_resting_volume (depth_slipped_after_submission)"),
        )
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?, 'WARNING', 'ForecastRunner', ?)",
            ("2026-06-04T19:01:46+00:00", "[ForecastRunner] KXLOWNY execution_blocked: market_notional_cap"),
        )

    summary = ot.get_recent_execution_summary(db_path=db, lookback_hours=50000)

    assert summary["count"] == 2
    assert summary["top_outcomes"][0]["outcome"] in {
        "fill_or_kill_insufficient_resting_volume (depth_slipped_after_submission)",
        "market_notional_cap",
    }


def test_agent_tools_live_truth_tool_returns_json(monkeypatch):
    import notifications.agent_tools as tools

    with patch(
        "runtime.operator_truth.get_live_kalshi_status",
        return_value={"balance_usd": 165.0, "broker_connected": True},
    ):
        payload = json.loads(tools.get_live_kalshi_status())

    assert payload["balance_usd"] == 165.0
    assert payload["broker_connected"] is True
