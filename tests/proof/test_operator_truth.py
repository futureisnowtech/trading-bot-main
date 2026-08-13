from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
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
    assert "weather_learning" in payload
    assert payload["position_drift"]["has_drift"] is True
    assert payload["position_drift"]["broker_only"][0]["ticker"] == "KXHIGHLAX-26JUN05-B69.5"
    assert payload["position_drift"]["db_only"][0]["ticker"] == "KXHIGHNY-26JUN05-B89.5"
    broker.sync_positions.assert_called_once()


def test_weather_learning_status_uses_governed_rbi2_champion(proof_runtime, monkeypatch):
    import runtime.operator_truth as ot

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)

    payload = ot.get_weather_learning_status(db_path=db)

    assert payload["adaptive_active"] is True
    assert payload["status"] == "rbi2_governed"
    assert payload["disabled_reason"] == ""
    assert payload["champion_artifact_id"] == "rbi2-baseline-60-40"
    assert payload["global_blend"]["segment"] == "GLOBAL"
    assert payload["global_blend"]["gfs_weight"] == 0.60
    assert payload["global_blend"]["ecmwf_weight"] == 0.40
    assert payload["calibration"]["official_outcomes_only"] is True


def test_recent_veto_summary_aggregates_reason_counts(proof_runtime, monkeypatch):
    import forecast.db as fdb
    import runtime.operator_truth as ot

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)
    fdb.init_forecast_db(db_path=db)

    with sqlite3.connect(proof_runtime.db_path) as conn:
        conn.execute(
            """
            INSERT INTO recent_vetoes
                (ts, ticker, strategy_family, side, veto_reason, rank_score, ev, position_contracts, size_usd, details_json)
            VALUES (?, 'KXHIGHNY', 'weather_ensemble', 'YES', 'missing_quotes', 0.0, 0.0, 0, 0.0, '{}')
            """,
            ("2026-06-04T18:57:46+00:00",),
        )
        conn.execute(
            """
            INSERT INTO recent_vetoes
                (ts, ticker, strategy_family, side, veto_reason, rank_score, ev, position_contracts, size_usd, details_json)
            VALUES (?, 'KXLOWNY', 'weather_ensemble', 'NO', 'missing_quotes', 0.0, 0.0, 0, 0.0, '{}')
            """,
            ("2026-06-04T18:58:46+00:00",),
        )
        conn.execute(
            """
            INSERT INTO recent_vetoes
                (ts, ticker, strategy_family, side, veto_reason, rank_score, ev, position_contracts, size_usd, details_json)
            VALUES (?, 'KXRAINNY', 'weather_ensemble', 'YES', 'no_strategy_signal', 0.0, 0.0, 0, 0.0, '{}')
            """,
            ("2026-06-04T18:59:46+00:00",),
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


def test_live_kalshi_status_downgrades_stale_lane_heartbeat(proof_runtime, monkeypatch):
    import forecast.db as fdb
    import runtime.operator_truth as ot
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    fdb.init_forecast_db(db_path=db)

    rs.upsert_lane_state(
        "forecast",
        db_path=db,
        active=1,
        connected=1,
        tradable=1,
        health="OK",
        readiness_state="OPERATIONAL",
        last_heartbeat_at=(datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
    )

    broker = MagicMock()
    broker.is_connected.return_value = True
    broker.get_account_balance.return_value = 164.0
    broker.get_positions.return_value = []

    with patch("execution.kalshi_broker.get_kalshi_broker", return_value=broker):
        payload = ot.get_live_kalshi_status(db_path=db)

    lane = payload["forecast_lane"]
    assert payload["broker_connected"] is True
    assert lane["heartbeat_stale"] is True
    assert lane["active"] == 0
    assert lane["connected"] == 0
    assert lane["readiness_state"] == "STALE_HEARTBEAT"
    assert lane["blocked_reason"] == "stale_runtime_heartbeat"


def test_release_status_surfaces_passing_artifact_and_provider(proof_runtime, monkeypatch):
    import runtime.build_info as bi
    import runtime.incident_tracker as it
    import runtime.operator_truth as ot
    import runtime.release_gate as rg

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)
    monkeypatch.setattr(
        rg,
        "load_release_audit_artifact",
        lambda: {
            "verdict": "READY_FOR_LIVE",
            "audited_sha": "abc123",
            "entries_allowed": True,
            "as_of": "2026-06-05T20:00:00+00:00",
            "last_successful_audit_at": "2026-06-05T20:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        bi,
        "get_build_info",
        lambda: {
            "sha": "abc123",
            "app_version": "19.10.3",
            "metadata_stale": False,
            "deployed_at_utc": "2026-06-05T20:00:00Z",
        },
    )
    monkeypatch.setattr(
        it,
        "get_incident_summary",
        lambda db_path=db: {"total_open": 0, "by_severity": {}},
    )
    monkeypatch.setattr(it, "get_open_incidents", lambda db_path=db: [])
    monkeypatch.setattr(
        ot,
        "get_weather_provider_status",
        lambda db_path=db, contract_limit=8: {
            "data_present": True,
            "provider_mode": "deterministic_multi_model",
            "forecast_source": "open_meteo_deterministic",
            "weather_age_minutes": 14.0,
        },
    )

    payload = ot.get_release_status(
        db_path=db,
        truth={
            "broker_connected": True,
            "broker_error": "",
            "balance_usd": 164.0,
            "active_markets": 12,
            "forecast_lane": {
                "readiness_state": "OPERATIONAL",
                "heartbeat_stale": False,
                "heartbeat_age_seconds": 30.0,
                "buying_power_usd": 164.0,
            },
            "forecast_snapshot": {"equity": 164.0},
            "recent_vetoes": {
                "top_reasons": [
                    {"reason": "no_strategy_signal", "count": 4},
                    {"reason": "missing_quotes", "count": 1},
                ]
            },
        },
    )

    assert payload["current_release_verdict"] == "READY_FOR_LIVE"
    assert payload["entries_allowed"] is True
    assert payload["provider_mode"] == "deterministic_multi_model"
    assert payload["deploy_parity"]["artifact_matches_build"] is True
    assert payload["top_non_blocking_veto_reasons"][0]["reason"] == "no_strategy_signal"


def test_release_status_blocks_balance_truth_mismatch(proof_runtime, monkeypatch):
    import runtime.build_info as bi
    import runtime.incident_tracker as it
    import runtime.operator_truth as ot
    import runtime.release_gate as rg

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)
    monkeypatch.setattr(
        rg,
        "load_release_audit_artifact",
        lambda: {
            "verdict": "READY_FOR_LIVE",
            "audited_sha": "abc123",
            "entries_allowed": True,
            "as_of": "2026-06-05T20:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        bi,
        "get_build_info",
        lambda: {"sha": "abc123", "app_version": "19.10.3", "metadata_stale": False},
    )
    monkeypatch.setattr(
        it,
        "get_incident_summary",
        lambda db_path=db: {"total_open": 0, "by_severity": {}},
    )
    monkeypatch.setattr(it, "get_open_incidents", lambda db_path=db: [])
    monkeypatch.setattr(
        ot,
        "get_weather_provider_status",
        lambda db_path=db, contract_limit=8: {
            "data_present": True,
            "provider_mode": "deterministic_multi_model",
            "weather_age_minutes": 10.0,
        },
    )

    payload = ot.get_release_status(
        db_path=db,
        truth={
            "broker_connected": True,
            "broker_error": "",
            "balance_usd": 164.0,
            "active_markets": 6,
            "forecast_lane": {
                "readiness_state": "OPERATIONAL",
                "heartbeat_stale": False,
                "heartbeat_age_seconds": 15.0,
                "buying_power_usd": 120.0,
            },
            "forecast_snapshot": {"equity": 120.0},
            "recent_vetoes": {"top_reasons": []},
        },
    )

    assert payload["current_release_verdict"] == "BLOCKED"
    assert any(
        "balance_truth_mismatch" in blocker
        for blocker in payload["top_infrastructure_blockers"]
    )


def test_release_status_surfaces_pending_new_build_blocker_without_sha_drift(
    proof_runtime,
    monkeypatch,
):
    import runtime.build_info as bi
    import runtime.incident_tracker as it
    import runtime.operator_truth as ot
    import runtime.release_gate as rg

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)
    monkeypatch.setattr(
        rg,
        "load_release_audit_artifact",
        lambda: {
            "verdict": "BLOCKED",
            "audited_sha": "abc123",
            "entries_allowed": False,
            "as_of": "2026-06-06T17:05:24+00:00",
            "blockers": ["release_audit_pending_new_build"],
        },
    )
    monkeypatch.setattr(
        bi,
        "get_build_info",
        lambda: {"sha": "abc123", "app_version": "19.10.4", "metadata_stale": False},
    )
    monkeypatch.setattr(
        it,
        "get_incident_summary",
        lambda db_path=db: {"total_open": 0, "by_severity": {}},
    )
    monkeypatch.setattr(it, "get_open_incidents", lambda db_path=db: [])
    monkeypatch.setattr(
        ot,
        "get_weather_provider_status",
        lambda db_path=db, contract_limit=8: {
            "data_present": True,
            "provider_mode": "deterministic_multi_model",
            "weather_age_minutes": 10.0,
        },
    )

    payload = ot.get_release_status(
        db_path=db,
        truth={
            "broker_connected": True,
            "broker_error": "",
            "balance_usd": 164.0,
            "active_markets": 6,
            "forecast_lane": {
                "readiness_state": "OPERATIONAL",
                "heartbeat_stale": False,
                "heartbeat_age_seconds": 15.0,
                "buying_power_usd": 164.0,
            },
            "forecast_snapshot": {"equity": 164.0},
            "recent_vetoes": {"top_reasons": []},
        },
    )

    assert payload["current_release_verdict"] == "BLOCKED"
    assert "release_audit_pending_new_build" in payload["top_infrastructure_blockers"]
    assert not any(
        "release_audit_sha_mismatch" in blocker
        for blocker in payload["top_infrastructure_blockers"]
    )


def test_release_status_reconnects_when_same_sha_deploy_is_pending(
    proof_runtime,
    monkeypatch,
):
    import runtime.build_info as bi
    import runtime.incident_tracker as it
    import runtime.operator_truth as ot
    import runtime.release_gate as rg

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)
    monkeypatch.setattr(
        rg,
        "load_release_audit_artifact",
        lambda: {
            "mode": "deploy_pending",
            "verdict": "PASS_WITH_WARNINGS",
            "entries_allowed": True,
            "audited_sha": "abc123",
            "as_of": "2026-08-12T03:36:12Z",
            "last_successful_audit_at": "2026-08-12T03:30:00Z",
            "warnings": ["release_audit_pending_new_build"],
            "details": {
                "build": {
                    "app_version": "19.17.0",
                    "sha": "abc123",
                    "branch": "master",
                    "deployed_at_utc": "2026-08-12T03:36:12Z",
                }
            },
        },
    )
    monkeypatch.setattr(
        bi,
        "get_build_info",
        lambda: {"sha": "abc123", "app_version": "19.17.0", "metadata_stale": False},
    )
    monkeypatch.setattr(
        it,
        "get_incident_summary",
        lambda db_path=db: {"total_open": 0, "by_severity": {}},
    )
    monkeypatch.setattr(it, "get_open_incidents", lambda db_path=db: [])
    monkeypatch.setattr(
        ot,
        "get_live_kalshi_status",
        lambda **kwargs: {
            "broker_connected": kwargs["connect"],
            "broker_error": "",
            "balance_usd": 164.0 if kwargs["connect"] else 0.0,
            "active_markets": 6,
            "forecast_lane": {
                "readiness_state": "OPERATIONAL",
                "heartbeat_stale": False,
                "heartbeat_age_seconds": 15.0,
                "buying_power_usd": 164.0,
            },
            "forecast_snapshot": {"equity": 164.0},
            "recent_vetoes": {"top_reasons": []},
        },
        raising=False,
    )
    monkeypatch.setattr(
        ot,
        "get_weather_provider_status",
        lambda db_path=db, contract_limit=8: {
            "data_present": True,
            "provider_mode": "deterministic_multi_model",
            "weather_age_minutes": 10.0,
        },
    )

    payload = ot.get_release_status(db_path=db)

    assert payload["current_release_verdict"] == "PASS_WITH_WARNINGS"
    assert payload["entries_allowed"] is True
    assert "broker_disconnected" not in payload["top_infrastructure_blockers"]
    assert "balance_truth_mismatch" not in " ".join(payload["top_infrastructure_blockers"])


def test_weather_provider_status_warms_sampled_series_when_process_cache_is_cold(
    proof_runtime,
    monkeypatch,
):
    import forecast.db as fdb
    import runtime.operator_truth as ot

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)

    monkeypatch.setattr(
        fdb,
        "get_active_contracts",
        lambda db_path=db: [
            {
                "local_symbol": "KXHIGHNY-26JUN05-B89.5",
                "contract_name": "NY High",
                "strike": 89.5,
                "resolution_at": "2026-06-05T23:59:59Z",
                "last_trade_at": "2026-06-05T23:59:59Z",
            }
        ],
        raising=False,
    )

    import data.kalshi_weather_monitor as wm

    monkeypatch.setattr(
        wm,
        "_resolve_weather_series",
        lambda ticker: "KXHIGHNY" if str(ticker).startswith("KXHIGHNY") else None,
        raising=False,
    )

    def _get_contract_weather_data(
        ticker,
        *,
        contract_name="",
        strike=None,
        resolution_at="",
        last_trade_at="",
    ):
        return {
            "provider_mode": "deterministic_multi_model",
            "forecast_source": "open_meteo_forecast",
            "timestamp": time.time(),
        }

    monkeypatch.setattr(wm, "get_contract_weather_data", _get_contract_weather_data, raising=False)

    payload = ot.get_weather_provider_status(db_path=db, contract_limit=4)

    assert payload["data_present"] is True
    assert payload["provider_mode"] == "deterministic_multi_model"
    assert payload["checked_contracts"] == 1
    assert payload["hydration"]["mode"] == "read_only_shared_truth"
    assert payload["hydration"]["attempted"] is False


def test_weather_freshness_limit_is_tight_for_hourly_and_wide_for_daily():
    from config import (
        KALSHI_DATA_FRESHNESS_MINUTES_DAILY,
        KALSHI_DATA_FRESHNESS_MINUTES_HOURLY,
    )
    from forecast.weather_contracts import weather_freshness_limit_minutes

    hourly = weather_freshness_limit_minutes("KXTEMPNYCH-26JUN0522-T75.99")
    daily = weather_freshness_limit_minutes(
        "KXHIGHNY-26JUN05-B89.5", contract_name="NY High"
    )

    assert hourly == KALSHI_DATA_FRESHNESS_MINUTES_HOURLY
    assert daily == KALSHI_DATA_FRESHNESS_MINUTES_DAILY
    assert hourly < daily


def _stale_gate_truth() -> dict:
    return {
        "broker_connected": True,
        "broker_error": "",
        "balance_usd": 164.0,
        "active_markets": 6,
        "forecast_lane": {
            "readiness_state": "OPERATIONAL",
            "heartbeat_stale": False,
            "heartbeat_age_seconds": 15.0,
            "buying_power_usd": 164.0,
        },
        "forecast_snapshot": {"equity": 164.0},
        "recent_vetoes": {"top_reasons": []},
    }


def _patch_passing_release_context(monkeypatch, db, provider_status):
    import runtime.build_info as bi
    import runtime.incident_tracker as it
    import runtime.operator_truth as ot
    import runtime.release_gate as rg

    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)
    monkeypatch.setattr(
        rg,
        "load_release_audit_artifact",
        lambda: {
            "verdict": "READY_FOR_LIVE",
            "audited_sha": "abc123",
            "entries_allowed": True,
            "as_of": "2026-06-05T20:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        bi,
        "get_build_info",
        lambda: {"sha": "abc123", "app_version": "19.10.3", "metadata_stale": False},
    )
    monkeypatch.setattr(
        it,
        "get_incident_summary",
        lambda db_path=db: {"total_open": 0, "by_severity": {}},
    )
    monkeypatch.setattr(it, "get_open_incidents", lambda db_path=db: [])
    monkeypatch.setattr(
        ot,
        "get_weather_provider_status",
        lambda db_path=db, contract_limit=8: provider_status,
        raising=False,
    )


def test_release_status_blocks_when_every_sampled_series_is_stale(
    proof_runtime,
    monkeypatch,
):
    """40-minute-old data is fine for a daily high and stale for an hourly temp.

    The legacy gate compared every contract against the 90-minute fallback, so
    this case passed the release gate while the strategy engine was vetoing it.
    Here the hourly series is the whole sample, so the provider is systemically
    stale and the gate closes.
    """
    import runtime.operator_truth as ot

    db = str(proof_runtime.db_path)
    _patch_passing_release_context(
        monkeypatch,
        db,
        {
            "data_present": True,
            "provider_mode": "deterministic_multi_model",
            "sample_ticker": "KXTEMPNYCH-26JUN0522-T75.99",
            "weather_age_minutes": 40.0,
            "freshness_limit_minutes": 25,
            "series_freshness": [
                {
                    "ticker": "KXTEMPNYCH-26JUN0522-T75.99",
                    "hourly": True,
                    "age_minutes": 40.0,
                    "limit_minutes": 25,
                }
            ],
            "stale_series": [
                {
                    "ticker": "KXTEMPNYCH-26JUN0522-T75.99",
                    "hourly": True,
                    "age_minutes": 40.0,
                    "limit_minutes": 25,
                }
            ],
        },
    )

    payload = ot.get_release_status(db_path=db, truth=_stale_gate_truth())

    assert payload["current_release_verdict"] == "BLOCKED"
    assert payload["entries_allowed"] is False
    blocker = next(
        item
        for item in payload["top_infrastructure_blockers"]
        if item.startswith("stale_ensemble_data")
    )
    assert "25m limit" in blocker
    assert "KXTEMPNYCH-26JUN0522-T75.99" in blocker


def test_release_status_allows_daily_series_at_the_same_age(proof_runtime, monkeypatch):
    import runtime.operator_truth as ot

    db = str(proof_runtime.db_path)
    _patch_passing_release_context(
        monkeypatch,
        db,
        {
            "data_present": True,
            "provider_mode": "deterministic_multi_model",
            "sample_ticker": "KXHIGHNY-26JUN05-B89.5",
            "weather_age_minutes": 40.0,
            "freshness_limit_minutes": 90,
            "series_freshness": [
                {
                    "ticker": "KXHIGHNY-26JUN05-B89.5",
                    "hourly": False,
                    "age_minutes": 40.0,
                    "limit_minutes": 90,
                }
            ],
            "stale_series": [],
        },
    )

    payload = ot.get_release_status(db_path=db, truth=_stale_gate_truth())

    assert payload["current_release_verdict"] == "READY_FOR_LIVE"
    assert payload["entries_allowed"] is True
    assert not any(
        item.startswith("stale_ensemble_data")
        for item in payload["top_infrastructure_blockers"]
    )


def test_release_status_warns_instead_of_blocking_on_partial_staleness(
    proof_runtime,
    monkeypatch,
):
    """One stale hourly lane must not halt the fresh daily lanes.

    The release gate is a global kill switch, so partial provider staleness is a
    warning -- the per-contract entry veto is what keeps the stale contract
    itself untradable.
    """
    import runtime.operator_truth as ot

    db = str(proof_runtime.db_path)
    stale_hourly = {
        "ticker": "KXTEMPNYCH-26JUN0522-T75.99",
        "hourly": True,
        "age_minutes": 40.0,
        "limit_minutes": 25,
    }
    fresh_daily = {
        "ticker": "KXHIGHNY-26JUN05-B89.5",
        "hourly": False,
        "age_minutes": 40.0,
        "limit_minutes": 90,
    }
    _patch_passing_release_context(
        monkeypatch,
        db,
        {
            "data_present": True,
            "provider_mode": "deterministic_multi_model",
            "sample_ticker": "KXHIGHNY-26JUN05-B89.5",
            "weather_age_minutes": 40.0,
            "freshness_limit_minutes": 90,
            "series_freshness": [fresh_daily, stale_hourly],
            "stale_series": [stale_hourly],
        },
    )

    payload = ot.get_release_status(db_path=db, truth=_stale_gate_truth())

    assert payload["entries_allowed"] is True
    assert not any(
        item.startswith("stale_ensemble_data")
        for item in payload["top_infrastructure_blockers"]
    )
    warning = next(
        item for item in payload["warnings"] if item.startswith("partial_stale_ensemble_data")
    )
    assert "1/2 series" in warning
    assert "KXTEMPNYCH-26JUN0522-T75.99" in warning


def test_provider_staleness_findings_handle_legacy_flat_artifact_shape():
    """Artifacts written before per-series freshness carry only a headline sample."""
    import runtime.operator_truth as ot

    stale_blocker, stale_warning = ot.get_provider_staleness_findings(
        {
            "data_present": True,
            "sample_ticker": "KXTEMPNYCH-26JUN0522-T75.99",
            "weather_age_minutes": 40.0,
        }
    )
    fresh_blocker, fresh_warning = ot.get_provider_staleness_findings(
        {
            "data_present": True,
            "sample_ticker": "KXHIGHNY-26JUN05-B89.5",
            "weather_age_minutes": 40.0,
        }
    )

    # A one-series sample that breaches is systemic by definition.
    assert stale_blocker.startswith("stale_ensemble_data")
    assert "25m limit" in stale_blocker
    assert stale_warning == ""
    assert (fresh_blocker, fresh_warning) == ("", "")


def test_weather_provider_status_flags_stale_hourly_series_worst_first(
    proof_runtime,
    monkeypatch,
):
    import forecast.db as fdb
    import runtime.operator_truth as ot

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)

    contracts = [
        {
            "local_symbol": "KXHIGHNY-26JUN05-B89.5",
            "contract_name": "NY High",
            "strike": 89.5,
            "resolution_at": "2026-06-05T23:59:59Z",
            "last_trade_at": "2026-06-05T23:59:59Z",
        },
        {
            "local_symbol": "KXTEMPNYCH-26JUN0522-T75.99",
            "contract_name": "NYC temp at 10pm",
            "strike": 75.99,
            "resolution_at": "2026-06-05T23:59:59Z",
            "last_trade_at": "2026-06-05T23:59:59Z",
        },
    ]
    monkeypatch.setattr(fdb, "get_active_contracts", lambda db_path=db: contracts, raising=False)

    import data.kalshi_weather_monitor as wm

    monkeypatch.setattr(
        wm,
        "_resolve_weather_series",
        lambda ticker: str(ticker).split("-")[0],
        raising=False,
    )

    ages_by_ticker = {
        "KXHIGHNY-26JUN05-B89.5": 40.0,
        "KXTEMPNYCH-26JUN0522-T75.99": 61.0,
    }

    def _get_contract_weather_data(ticker, **kwargs):
        return {
            "provider_mode": "deterministic_multi_model",
            "forecast_source": "open_meteo_forecast",
            "timestamp": time.time() - ages_by_ticker[ticker] * 60.0,
        }

    monkeypatch.setattr(wm, "get_contract_weather_data", _get_contract_weather_data, raising=False)

    payload = ot.get_weather_provider_status(db_path=db, contract_limit=4)

    assert payload["data_present"] is True
    assert payload["checked_contracts"] == 2
    # The daily high sits inside its 90m window; only the hourly temp breaches.
    assert [item["ticker"] for item in payload["stale_series"]] == [
        "KXTEMPNYCH-26JUN0522-T75.99"
    ]
    assert payload["stale_series"][0]["limit_minutes"] == 25
    assert payload["hydration"]["attempted"] is False


def test_release_status_prefers_passing_artifact_truth_when_one_shot_process_is_blind(
    proof_runtime,
    monkeypatch,
):
    import runtime.build_info as bi
    import runtime.incident_tracker as it
    import runtime.operator_truth as ot
    import runtime.release_gate as rg

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(ot, "DB_PATH", db, raising=False)
    monkeypatch.setattr(
        rg,
        "load_release_audit_artifact",
        lambda: {
            "verdict": "READY_FOR_LIVE",
            "audited_sha": "abc123",
            "entries_allowed": True,
            "as_of": "2026-06-05T20:00:00+00:00",
            "last_successful_audit_at": "2026-06-05T20:00:00+00:00",
            "details": {
                "live_truth": {
                    "broker_connected": True,
                    "broker_error": "",
                    "balance_usd": 164.0,
                    "active_markets": 12,
                    "lane": {
                        "readiness_state": "OPERATIONAL",
                        "heartbeat_stale": False,
                        "heartbeat_age_seconds": 30.0,
                    },
                },
                "provider_status": {
                    "data_present": True,
                    "provider_mode": "deterministic_multi_model",
                    "forecast_source": "open_meteo_forecast",
                    "weather_age_minutes": 9.0,
                },
                "balance_truth": {
                    "broker_balance_usd": 164.0,
                    "runtime_balance_usd": 164.0,
                    "comparison_available": True,
                    "delta_usd": 0.0,
                    "tolerance_usd": 1.0,
                    "balance_ok": True,
                },
            },
        },
    )
    monkeypatch.setattr(
        bi,
        "get_build_info",
        lambda: {"sha": "abc123", "app_version": "19.10.3", "metadata_stale": False},
    )
    monkeypatch.setattr(
        it,
        "get_incident_summary",
        lambda db_path=db: {"total_open": 0, "by_severity": {}},
    )
    monkeypatch.setattr(it, "get_open_incidents", lambda db_path=db: [])
    monkeypatch.setattr(
        ot,
        "get_live_kalshi_status",
        lambda **kwargs: {
            "broker_connected": False,
            "broker_error": "",
            "balance_usd": 0.0,
            "active_markets": 0,
            "forecast_lane": {
                "readiness_state": "OPERATIONAL",
                "heartbeat_stale": False,
                "heartbeat_age_seconds": 30.0,
                "buying_power_usd": 164.0,
            },
            "forecast_snapshot": {"equity": 164.0},
            "recent_vetoes": {"top_reasons": []},
        },
        raising=False,
    )
    monkeypatch.setattr(
        ot,
        "get_weather_provider_status",
        lambda db_path=db, contract_limit=8: {
            "data_present": False,
            "provider_mode": "",
            "weather_age_minutes": None,
        },
        raising=False,
    )

    payload = ot.get_release_status(db_path=db)

    assert payload["current_release_verdict"] == "READY_FOR_LIVE"
    assert payload["entries_allowed"] is True
    assert payload["provider_mode"] == "deterministic_multi_model"
    assert "broker_disconnected" not in payload["top_infrastructure_blockers"]
