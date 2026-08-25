from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace


def test_market_scan_findings_blocks_infra_cluster():
    import scripts.release_audit as ra

    blockers, warnings = ra._market_scan_findings(
        {
            "sample_size": 10,
            "infrastructure_rejections": [{"reason": "missing_quotes", "count": 4}],
            "systematic_thin_liquidity": False,
        },
        active_markets=10,
        strict_runtime=True,
    )

    assert blockers == ["quote_ingestion_failure (4/10 infrastructure vetoes)"]
    assert warnings == []


def test_market_scan_findings_warns_on_systematic_thin_liquidity():
    import scripts.release_audit as ra

    blockers, warnings = ra._market_scan_findings(
        {
            "sample_size": 8,
            "infrastructure_rejections": [],
            "systematic_thin_liquidity": True,
        },
        active_markets=8,
        strict_runtime=True,
    )

    assert blockers == []
    assert warnings == ["systematic_thin_liquidity"]


def test_market_scan_findings_warns_when_no_true_hourly_inventory_is_present():
    import scripts.release_audit as ra

    blockers, warnings = ra._market_scan_findings(
        {
            "sample_size": 0,
            "scope_active_contracts": 0,
            "entry_scope": "ALL_WEATHER_LANES",
            "infrastructure_rejections": [],
            "systematic_thin_liquidity": False,
        },
        active_markets=10,
        strict_runtime=True,
    )

    assert blockers == []
    assert warnings == ["no_entry_scope_inventory (ALL_WEATHER_LANES)"]


def test_market_scan_findings_never_calls_zero_inventory_ready():
    import scripts.release_audit as ra

    blockers, warnings = ra._market_scan_findings(
        {
            "sample_size": 0,
            "scope_active_contracts": 0,
            "entry_scope": "ALL_WEATHER_LANES",
            "infrastructure_rejections": [],
            "systematic_thin_liquidity": False,
        },
        active_markets=0,
        strict_runtime=True,
    )

    assert blockers == []
    assert warnings == ["no_active_market_inventory"]


def test_build_identity_findings_warn_locally_and_block_hosted():
    import scripts.release_audit as ra

    stale = {
        "sha": "newsha",
        "metadata_stale": True,
        "build_sha_mismatch": True,
    }

    blockers, warnings = ra._build_identity_findings(stale, strict=False)
    assert blockers == []
    assert warnings == [
        "deploy_runtime_metadata_stale",
        "deploy_runtime_build_sha_mismatch",
    ]

    blockers, warnings = ra._build_identity_findings(stale, strict=True)
    assert blockers == [
        "deploy_runtime_metadata_stale",
        "deploy_runtime_build_sha_mismatch",
    ]
    assert warnings == []


def test_local_audit_blocks_a_dirty_worktree(monkeypatch, tmp_path):
    import scripts.release_audit as ra

    monkeypatch.setattr(ra, "get_build_info", lambda: {"sha": "abc123"}, raising=False)
    monkeypatch.setattr(
        ra,
        "_git_worktree_state",
        lambda: {
            "available": True,
            "dirty": True,
            "paths": [" M forecast/runner.py"],
            "changed_path_count": 1,
            "error": "",
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "_run_command",
        lambda label, command: {"label": label, "ok": True, "returncode": 0},
        raising=False,
    )
    monkeypatch.setattr(ra, "run_health_check", lambda force=True: {"healthy": True}, raising=False)
    monkeypatch.setattr(ra, "runtime_storage_status", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(ra, "DB_PATH", str(tmp_path / "audit.db"), raising=False)
    monkeypatch.setattr(ra, "get_open_forecast_positions", lambda db_path=None: [], raising=False)
    monkeypatch.setattr(
        ra,
        "_scan_live_market_surface",
        lambda **kwargs: {
            "sample_size": 1,
            "markets_scanned": 1,
            "scope_active_contracts": 1,
            "rows": [{"ticker": "KXHIGHCHI-TEST"}],
            "infrastructure_rejections": [],
            "systematic_thin_liquidity": False,
        },
        raising=False,
    )

    payload = ra._run_local_audit(scan_limit=1)

    assert payload["verdict"] == "BLOCKED"
    assert payload["entries_allowed"] is False
    assert "working_tree_dirty" in payload["blockers"]
    assert payload["details"]["worktree"]["changed_path_count"] == 1


def test_render_markdown_report_contains_verdict_and_counts():
    import scripts.release_audit as ra

    markdown = ra._render_markdown_report(
        {
            "mode": "remote_hosted",
            "verdict": "READY_FOR_LIVE",
            "entries_allowed": True,
            "audited_sha": "abc123",
            "as_of": "2026-06-05T20:00:00+00:00",
            "blockers": [],
            "warnings": ["systematic_thin_liquidity"],
            "details": {
                "provider_status": {"provider_mode": "deterministic_multi_model"},
                "live_truth": {"broker_connected": True, "active_markets": 12},
                "release_status": {"heartbeat_fresh": True},
                "market_scan": {
                    "markets_scanned": 12,
                    "approved_candidates": 2,
                    "execution_ready": 1,
                    "thin_liquidity_count": 1,
                },
            },
        }
    )

    assert "# Release Audit" in markdown
    assert "`READY_FOR_LIVE`" in markdown
    assert "Thin Liquidity Count" in markdown


def test_strategy_cycle_blocks_new_entries_when_release_gate_closed(monkeypatch):
    import config
    import forecast.db as fdb
    import forecast.runner as fr
    import runtime.operator_truth as ot

    events: list[tuple[str, str, str]] = []

    class BrokerStub:
        def is_connected(self):
            return True

        def sync_positions(self):
            return True

        def has_fresh_position_snapshot(self):
            return True

        def position_snapshot_status(self):
            return {"authoritative": True, "fresh": True}

        def get_account_balance(self):
            return 164.0

        def get_positions(self):
            return []

    monkeypatch.setattr(config, "KALSHI_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "FORECAST_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(fr, "_get_broker", lambda: BrokerStub(), raising=False)
    monkeypatch.setattr(
        fdb,
        "get_active_contracts",
        lambda db_path=None: [
            {
                "id": 1,
                "market_id": 7,
                "local_symbol": "KXLOWNY-26JUN06-T70",
                "contract_name": "NY Low",
                "right": "C",
                "strike": 70.0,
                "last_trade_at": "20260606",
                "resolution_at": "2026-06-06T04:59:00Z",
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(
        ot,
        "get_release_status",
        lambda: {
            "entries_allowed": False,
            "current_release_verdict": "BLOCKED",
            "top_infrastructure_blockers": ["release_audit_missing"],
        },
        raising=False,
    )
    monkeypatch.setattr(
        fr,
        "log_event",
        lambda level, source, message: events.append((level, source, message)),
        raising=False,
    )

    result = fr.run_strategy_cycle(bankroll=164.0)

    assert result == []
    assert any("entry_gate_blocked" in message for _level, _source, message in events)


def test_strategy_cycle_with_open_release_gate_handles_empty_candidates(monkeypatch):
    import config
    import forecast.db as fdb
    import forecast.market_snapshot as fms
    import forecast.runner as fr
    import forecast.strategy_engine as fse
    import runtime.operator_truth as ot

    class BrokerStub:
        def is_connected(self):
            return True

        def sync_positions(self):
            return True

        def has_fresh_position_snapshot(self):
            return True

        def position_snapshot_status(self):
            return {"authoritative": True, "fresh": True}

        def get_account_balance(self):
            return 164.0

        def get_positions(self):
            return []

    monkeypatch.setattr(config, "KALSHI_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "FORECAST_LANE_ACTIVE", True, raising=False)
    monkeypatch.setattr(fr, "_get_broker", lambda: BrokerStub(), raising=False)
    monkeypatch.setattr(
        fdb,
        "get_active_contracts",
        lambda db_path=None: [
            {
                "id": 1,
                "market_id": 7,
                "local_symbol": "KXLOWNY-26JUN06-T70",
                "contract_name": "NY Low",
                "right": "C",
                "strike": 70.0,
                "last_trade_at": "20260606",
                "resolution_at": "2026-06-06T04:59:00Z",
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(fdb, "get_bars", lambda *args, **kwargs: [], raising=False)
    monkeypatch.setattr(fms, "build_market_snapshots", lambda *args, **kwargs: [], raising=False)
    monkeypatch.setattr(
        fse,
        "evaluate_market_snapshots",
        lambda **kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(
        ot,
        "get_release_status",
        lambda: {
            "entries_allowed": True,
            "current_release_verdict": "READY_FOR_LIVE",
            "top_infrastructure_blockers": [],
        },
        raising=False,
    )

    result = fr.run_strategy_cycle(bankroll=164.0)

    assert result == []


def test_scan_live_market_surface_warms_weather_truth_for_weather_candidates(monkeypatch):
    import scripts.release_audit as ra

    monkeypatch.setattr(
        ra,
        "get_active_contracts",
        lambda db_path=None: [
            {
                "id": 1,
                "market_id": 9,
                "local_symbol": "KXHIGHCHI-26JUN05-T75",
                "contract_name": "Will the high temperature in Chicago be above 75° on Jun 5, 2026?",
                "right": "C",
                "strike": 75.0,
                "last_trade_at": "2026-06-05T23:59:59Z",
                "resolution_at": "2026-06-05T23:59:59Z",
            }
        ],
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "build_market_snapshots",
        lambda *args, **kwargs: [
            SimpleNamespace(
                ticker="KXHIGHCHI-26JUN05-T75",
                yes_quote={},
                no_quote={},
            )
        ],
        raising=False,
    )

    def _evaluate_market_snapshots(**kwargs):
        return []

    monkeypatch.setattr(ra, "evaluate_market_snapshots", _evaluate_market_snapshots, raising=False)
    monkeypatch.setattr(
        ra,
        "_warm_weather_truth",
        lambda tickers: {
            "mode": "shared_truth_hydration",
            "attempted": True,
            "requested_tickers": len(list(tickers)),
            "requested_series": 1,
            "refreshed_series": 1,
        },
        raising=False,
    )

    payload = ra._scan_live_market_surface(
        bankroll=164.0,
        open_positions=[],
        scan_limit=4,
    )

    assert payload["weather_warmup"]["mode"] == "shared_truth_hydration"
    assert payload["weather_warmup"]["attempted"] is True
    assert payload["weather_warmup"]["refreshed_series"] == 1
    assert payload["entry_scope"] == "DAILY_HIGH+DAILY_LOW"
    assert payload["scope_active_contracts"] == 1


def test_run_remote_audit_parses_json_after_stdout_noise(monkeypatch):
    import scripts.release_audit as ra

    monkeypatch.setattr(ra, "_git_head_sha", lambda: "abc123", raising=False)
    monkeypatch.setattr(
        ra.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "[KalshiBroker] Connected (LIVE) ✅ | Balance: $240.34\n"
                '{"audited_sha":"abc123","verdict":"PASS_WITH_WARNINGS","blockers":[],"warnings":["docker_service_check_skipped_in_container_mode"]}'
            ),
            stderr="",
        ),
        raising=False,
    )

    payload = ra._run_remote_audit(scan_limit=12, soak_seconds=10)

    assert payload["verdict"] == "PASS_WITH_WARNINGS"
    assert payload["blockers"] == []
    assert payload["details"]["remote_payload"]["audited_sha"] == "abc123"


def test_run_remote_audit_times_out_fail_closed(monkeypatch):
    import subprocess

    import scripts.release_audit as ra

    monkeypatch.setattr(ra, "_git_head_sha", lambda: "abc123", raising=False)

    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=kwargs["timeout"],
            output="partial remote audit output",
            stderr="ssh still waiting",
        )

    monkeypatch.setattr(ra.subprocess, "run", _timeout, raising=False)

    payload = ra._run_remote_audit(scan_limit=12, soak_seconds=10)

    assert payload["verdict"] == "BLOCKED"
    assert payload["entries_allowed"] is False
    assert payload["blockers"] == ["remote_release_audit_timeout (130s)"]
    assert payload["details"]["timeout_seconds"] == 130
    assert payload["details"]["stderr_tail"] == ["ssh still waiting"]


def test_release_audit_json_mode_suppresses_noisy_stdout(monkeypatch, capsys):
    import scripts.release_audit as ra

    monkeypatch.setattr(
        sys,
        "argv",
        ["release_audit.py", "--local", "--format", "json"],
        raising=False,
    )

    def _noisy_selected_mode(_args):
        print("[KalshiBroker] Connected (LIVE) ✅ | Balance: $240.34")
        return {
            "mode": "local",
            "as_of": "2026-06-06T00:00:00+00:00",
            "audited_sha": "abc123",
            "verdict": "PASS_WITH_WARNINGS",
            "entries_allowed": True,
            "blockers": [],
            "warnings": [],
            "details": {},
        }

    monkeypatch.setattr(ra, "_run_selected_mode", _noisy_selected_mode, raising=False)
    monkeypatch.setattr(ra, "_render_markdown_report", lambda payload: "# ok\n", raising=False)
    monkeypatch.setattr(ra, "write_release_audit_artifact", lambda payload, markdown="": {}, raising=False)

    rc = ra.main()
    out = capsys.readouterr().out

    assert rc == 0
    assert out.lstrip().startswith("{")
    assert "[KalshiBroker] Connected" not in out


def test_docker_service_status_uses_host_artifact_when_docker_unavailable(monkeypatch):
    import scripts.release_audit as ra

    def _raise_no_docker(*args, **kwargs):
        raise RuntimeError("docker unavailable in container")

    monkeypatch.setattr(ra.subprocess, "check_output", _raise_no_docker, raising=False)
    monkeypatch.setattr(
        ra,
        "load_host_service_status_artifact",
        lambda: {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "audited_sha": "abc123",
            "services": {
                "execution-engine": {"up": True, "status": "Up 12 seconds"},
                "kalshi-cockpit": {"up": True, "status": "Up 12 seconds"},
            },
        },
        raising=False,
    )

    payload = ra._docker_service_status("abc123")

    assert payload["source"] == "host_service_status_artifact"
    assert payload["artifact_usable"] is True
    assert payload["services"]["execution-engine"]["up"] is True
    assert payload["docker_error"] == "docker unavailable in container"


def test_run_remote_hosted_audit_uses_host_service_artifact_without_skip_warning(
    monkeypatch,
):
    import scripts.release_audit as ra

    monkeypatch.setattr(ra, "get_build_info", lambda: {"sha": "abc123"}, raising=False)
    monkeypatch.setattr(ra, "init_incident_table", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(ra, "ingest_system_events", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(ra, "run_health_check", lambda force=True: {"healthy": True}, raising=False)
    monkeypatch.setattr(
        ra,
        "get_live_kalshi_status",
        lambda **kwargs: {
            "broker_connected": True,
            "broker_positions": [],
            "db_positions": [],
            "balance_usd": 164.0,
            "active_markets": 4,
            "forecast_lane": {"heartbeat_stale": False},
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "get_weather_provider_status",
        lambda db_path=None: {
            "data_present": True,
            "provider_mode": "deterministic_multi_model",
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "get_balance_truth_status",
        lambda **kwargs: {
            "balance_ok": True,
            "comparison_available": True,
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "_docker_service_status",
        lambda expected_sha="": {
            "source": "host_service_status_artifact",
            "artifact_usable": True,
            "services": {
                "execution-engine": {"up": True, "status": "Up 12 seconds"},
                "kalshi-cockpit": {"up": True, "status": "Up 12 seconds"},
            },
        },
        raising=False,
    )
    monkeypatch.setattr(ra, "_cockpit_health", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(ra, "probe_reasoning_model", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(ra, "_running_in_container", lambda: True, raising=False)
    monkeypatch.setattr(
        ra,
        "_scan_live_market_surface",
        lambda **kwargs: {
            "sample_size": 4,
            "markets_scanned": 4,
            "approved_candidates": 0,
            "execution_ready": 0,
            "infrastructure_rejections": [],
            "systematic_thin_liquidity": False,
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "_warm_weather_truth",
        lambda *args, **kwargs: {"mode": "shared_truth_hydration", "attempted": True},
        raising=False,
    )
    monkeypatch.setattr(ra, "_market_scan_findings", lambda *args, **kwargs: ([], []), raising=False)
    monkeypatch.setattr(ra, "runtime_storage_status", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(
        ra,
        "get_incident_summary",
        lambda db_path=None: {"by_severity": {"CRITICAL": 0}},
        raising=False,
    )

    payload = ra._run_remote_hosted_audit(scan_limit=4, soak_seconds=0)

    assert payload["verdict"] == "READY_FOR_LIVE"
    assert payload["blockers"] == []
    assert "docker_service_check_skipped_in_container_mode" not in payload["warnings"]


def test_run_remote_hosted_audit_rechecks_runtime_after_soak(monkeypatch):
    import scripts.release_audit as ra

    monkeypatch.setattr(ra, "get_build_info", lambda: {"sha": "abc123"}, raising=False)
    monkeypatch.setattr(ra, "init_incident_table", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(ra, "ingest_system_events", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(ra, "run_health_check", lambda force=True: {"healthy": True}, raising=False)
    monkeypatch.setattr(
        ra,
        "_docker_service_status",
        lambda expected_sha="": {
            "source": "host_service_status_artifact",
            "artifact_usable": True,
            "services": {
                "execution-engine": {"up": True, "status": "Up 12 seconds"},
                "kalshi-cockpit": {"up": True, "status": "Up 12 seconds"},
            },
        },
        raising=False,
    )
    monkeypatch.setattr(ra, "_cockpit_health", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(ra, "probe_reasoning_model", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(ra, "_running_in_container", lambda: True, raising=False)
    monkeypatch.setattr(ra, "runtime_storage_status", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(
        ra,
        "get_incident_summary",
        lambda db_path=None: {"by_severity": {"CRITICAL": 0}},
        raising=False,
    )
    monkeypatch.setattr(ra, "get_open_incidents", lambda db_path=None: [], raising=False)
    monkeypatch.setattr(
        ra,
        "get_release_status",
        lambda **kwargs: {"current_release_verdict": "READY_FOR_LIVE", "entries_allowed": True},
        raising=False,
    )
    monkeypatch.setattr(ra.time, "sleep", lambda *_args, **_kwargs: None, raising=False)

    runtime_states = iter(
        [
            {
                "truth": {
                    "broker_connected": True,
                    "broker_error": "",
                    "broker_positions": [],
                    "db_positions": [],
                    "balance_usd": 164.0,
                    "active_markets": 4,
                    "forecast_lane": {
                        "heartbeat_stale": True,
                        "health": "WARN",
                        "readiness_state": "STALE_HEARTBEAT",
                        "blocked_reason": "stale_runtime_heartbeat",
                    },
                },
                "provider_status": {
                    "data_present": True,
                    "provider_mode": "deterministic_multi_model",
                },
                "balance_truth": {
                    "balance_ok": False,
                    "comparison_available": True,
                    "delta_usd": 12.0,
                },
                "market_scan": {
                    "sample_size": 0,
                    "scope_active_contracts": 12,
                    "markets_scanned": 0,
                    "approved_candidates": 0,
                    "execution_ready": 0,
                    "infrastructure_rejections": [],
                    "systematic_thin_liquidity": False,
                    "entry_scope": "ALL_WEATHER_LANES",
                },
            },
            {
                "truth": {
                    "broker_connected": True,
                    "broker_error": "",
                    "broker_positions": [],
                    "db_positions": [],
                    "balance_usd": 164.0,
                    "active_markets": 4,
                    "forecast_lane": {
                        "heartbeat_stale": False,
                        "health": "OK",
                        "readiness_state": "OPERATIONAL",
                        "blocked_reason": "",
                    },
                },
                "provider_status": {
                    "data_present": True,
                    "provider_mode": "deterministic_multi_model",
                },
                "balance_truth": {
                    "balance_ok": True,
                    "comparison_available": True,
                    "delta_usd": 0.0,
                },
                "market_scan": {
                    "sample_size": 4,
                    "scope_active_contracts": 12,
                    "markets_scanned": 4,
                    "approved_candidates": 1,
                    "execution_ready": 1,
                    "infrastructure_rejections": [],
                    "systematic_thin_liquidity": False,
                    "entry_scope": "ALL_WEATHER_LANES",
                },
            },
        ]
    )
    monkeypatch.setattr(
        ra,
        "_collect_runtime_audit_state",
        lambda **kwargs: next(runtime_states),
        raising=False,
    )

    payload = ra._run_remote_hosted_audit(scan_limit=4, soak_seconds=600)

    assert payload["verdict"] == "READY_FOR_LIVE"
    assert payload["blockers"] == []
    assert payload["details"]["live_truth"]["lane"]["heartbeat_stale"] is False
    assert payload["details"]["balance_truth"]["balance_ok"] is True
    assert payload["details"]["market_scan"]["sample_size"] == 4
    assert payload["details"]["startup_runtime"]["market_scan"]["sample_size"] == 0


def test_run_remote_hosted_audit_blocks_when_host_service_artifact_missing(
    monkeypatch,
):
    import scripts.release_audit as ra

    monkeypatch.setattr(ra, "get_build_info", lambda: {"sha": "abc123"}, raising=False)
    monkeypatch.setattr(ra, "init_incident_table", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(ra, "ingest_system_events", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(ra, "run_health_check", lambda force=True: {"healthy": True}, raising=False)
    monkeypatch.setattr(
        ra,
        "get_live_kalshi_status",
        lambda **kwargs: {
            "broker_connected": True,
            "broker_positions": [],
            "db_positions": [],
            "balance_usd": 164.0,
            "active_markets": 4,
            "forecast_lane": {"heartbeat_stale": False},
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "get_weather_provider_status",
        lambda db_path=None: {
            "data_present": True,
            "provider_mode": "deterministic_multi_model",
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "get_balance_truth_status",
        lambda **kwargs: {
            "balance_ok": True,
            "comparison_available": True,
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "_docker_service_status",
        lambda expected_sha="": {
            "source": "host_service_status_artifact",
            "artifact_usable": False,
            "artifact_reason": "missing",
            "services": {
                "execution-engine": {"up": False, "status": ""},
                "kalshi-cockpit": {"up": False, "status": ""},
            },
        },
        raising=False,
    )
    monkeypatch.setattr(ra, "_cockpit_health", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(ra, "probe_reasoning_model", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(ra, "_running_in_container", lambda: True, raising=False)
    monkeypatch.setattr(
        ra,
        "_scan_live_market_surface",
        lambda **kwargs: {
            "sample_size": 4,
            "markets_scanned": 4,
            "approved_candidates": 0,
            "execution_ready": 0,
            "infrastructure_rejections": [],
            "systematic_thin_liquidity": False,
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "_warm_weather_truth",
        lambda *args, **kwargs: {"mode": "shared_truth_hydration", "attempted": True},
        raising=False,
    )
    monkeypatch.setattr(ra, "_market_scan_findings", lambda *args, **kwargs: ([], []), raising=False)
    monkeypatch.setattr(ra, "runtime_storage_status", lambda: {"ok": True}, raising=False)
    monkeypatch.setattr(
        ra,
        "get_incident_summary",
        lambda db_path=None: {"by_severity": {"CRITICAL": 0}},
        raising=False,
    )

    payload = ra._run_remote_hosted_audit(scan_limit=4, soak_seconds=0)

    assert payload["verdict"] == "BLOCKED"
    assert "host_service_status_artifact_missing" in payload["blockers"]


def test_build_deploy_pending_artifact_blocks_new_build_despite_prior_pass():
    from runtime.release_gate import build_deploy_pending_artifact

    payload = build_deploy_pending_artifact(
        prior_release={
            "audited_sha": "oldsha",
            "verdict": "READY_FOR_LIVE",
            "entries_allowed": True,
            "as_of": "2026-08-12T03:30:00Z",
            "last_successful_audit_at": "2026-08-12T03:30:00Z",
            "details": {
                "live_truth": {"broker_connected": True, "balance_usd": 164.0},
                "provider_status": {"data_present": True, "provider_mode": "deterministic_multi_model"},
                "balance_truth": {"balance_ok": True, "delta_usd": 0.0},
            },
        },
        audited_sha="newsha",
        app_version="19.17.0",
        branch="master",
        deployed_at_utc="2026-08-12T03:36:12Z",
    )

    assert payload["mode"] == "deploy_pending"
    assert payload["verdict"] == "BLOCKED"
    assert payload["entries_allowed"] is False
    assert payload["blockers"] == ["release_audit_pending_new_build"]
    assert payload["warnings"] == []
    assert payload["details"]["prior_release"]["verdict"] == "READY_FOR_LIVE"


def test_build_deploy_pending_artifact_keeps_existing_blocked_state():
    from runtime.release_gate import build_deploy_pending_artifact

    payload = build_deploy_pending_artifact(
        prior_release={
            "audited_sha": "oldsha",
            "verdict": "BLOCKED",
            "entries_allowed": False,
            "as_of": "2026-08-12T03:30:00Z",
            "blockers": ["broker_disconnected"],
        },
        audited_sha="newsha",
        app_version="19.17.0",
        branch="master",
        deployed_at_utc="2026-08-12T03:36:12Z",
    )

    assert payload["verdict"] == "BLOCKED"
    assert payload["entries_allowed"] is False
    assert payload["blockers"] == ["release_audit_pending_new_build"]


def test_deploy_script_preserves_prior_release_and_runs_immediate_audit():
    from pathlib import Path

    deploy = Path("deploy.sh").read_text(encoding="utf-8")

    assert 'PRE_DEPLOY_RELEASE_JSON' in deploy
    assert 'PRE_DEPLOY_RELEASE_B64' in deploy
    assert 'build_deploy_pending_artifact' in deploy
    assert '--remote-hosted --scan-limit 12 --soak-seconds 0 --no-persist' in deploy
    assert 'Immediate audit still warming up' in deploy


def test_deploy_soak_window_covers_a_full_execution_cycle():
    """The soak must outlast one execution cycle or it stops proving anything.

    Production's cadence is execution_daemon's loop, which sleeps
    SNIPER_SLEEP_SECONDS (default 300s) and writes the lane heartbeat at the end
    of each cycle via run_position_monitor. Below 300s the audit can re-collect
    runtime state before any cycle has completed, which turns the release gate
    into a slow restart check.

    Not to be confused with forecast.runner's schedule.every(...) jobs: those
    only fire when forecast/runner.py is run directly, never in production.
    """
    import re
    from pathlib import Path

    deploy = Path("deploy.sh").read_text(encoding="utf-8")

    match = re.search(r'RELEASE_AUDIT_SOAK_SECONDS="\$\{RELEASE_AUDIT_SOAK_SECONDS:-(\d+)\}"', deploy)
    assert match, "deploy.sh must define a default RELEASE_AUDIT_SOAK_SECONDS"
    assert int(match.group(1)) >= 300

    parser_default = re.search(r'"--soak-seconds", type=int, default=(\d+)', Path("scripts/release_audit.py").read_text(encoding="utf-8"))
    assert parser_default, "release_audit.py must define a --soak-seconds default"
    assert int(parser_default.group(1)) >= 300


def test_release_audit_main_no_persist_skips_artifact_write(monkeypatch):
    import scripts.release_audit as ra

    writes: list[tuple[dict, str]] = []

    monkeypatch.setattr(
        ra,
        "_run_selected_mode",
        lambda args: {
            "mode": "remote_hosted",
            "verdict": "BLOCKED",
            "entries_allowed": False,
            "audited_sha": "abc123",
            "as_of": "2026-08-14T21:30:00+00:00",
            "blockers": ["quote_ingestion_failure (9/12 infrastructure vetoes)"],
            "warnings": [],
            "details": {},
        },
        raising=False,
    )
    monkeypatch.setattr(
        ra,
        "write_release_audit_artifact",
        lambda payload, markdown="": writes.append((payload, markdown)),
        raising=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_audit.py",
            "--remote-hosted",
            "--scan-limit",
            "12",
            "--soak-seconds",
            "0",
            "--no-persist",
        ],
        raising=False,
    )

    exit_code = ra.main()

    assert exit_code == 1
    assert writes == []


def test_deploy_script_never_lets_docker_exec_eat_the_remote_script():
    """`docker exec -i` inside the remote heredoc silently truncates the deploy.

    The remote block is piped into `bash -s`, so a container attached to stdin
    consumes the rest of the script as its own input. Bash then reaches EOF and
    exits 0, skipping the soak audit and ownership re-audit while still
    reporting success. Every docker exec must therefore read from /dev/null.
    """
    import re
    from pathlib import Path

    deploy = Path("deploy.sh").read_text(encoding="utf-8")

    assert "docker exec -i" not in deploy, (
        "docker exec -i inside the remote heredoc will consume the deploy script"
    )
    for match in re.finditer(r"docker exec\b.*?(?=\n\s*\n)", deploy, re.DOTALL):
        assert "/dev/null" in match.group(0), (
            f"docker exec without a /dev/null stdin redirect:\n{match.group(0)}"
        )


def test_host_service_status_artifact_is_refreshed_on_a_timer():
    """Writing it only at deploy time closes the release gate 30 minutes later.

    The in-container audit cannot see Docker, so it trusts this host-written
    artifact and rejects it past HOST_SERVICE_ARTIFACT_MAX_AGE_SECONDS. Without
    a refresh timer the engine's own periodic audit reports
    `host_service_status_artifact_stale` and entries stop until the next deploy.
    """
    import os
    from pathlib import Path

    import scripts.release_audit as ra

    refresher = Path("scripts/refresh_host_service_status.sh")
    assert refresher.exists(), "host service-status refresher script is missing"
    assert os.access(refresher, os.X_OK), "refresher must be executable for cron"
    assert "write_host_service_status_artifact" in refresher.read_text(encoding="utf-8")

    deploy = Path("deploy.sh").read_text(encoding="utf-8")
    assert "refresh_host_service_status.sh" in deploy
    assert "crontab -" in deploy, "deploy must install the refresh timer"

    # The timer has to run comfortably inside the window the audit enforces.
    cron_period_seconds = 5 * 60
    assert cron_period_seconds < ra.HOST_SERVICE_ARTIFACT_MAX_AGE_SECONDS
    assert "*/5 * * * *" in deploy


def test_deploy_script_verifies_the_remote_block_ran_to_completion():
    from pathlib import Path

    deploy = Path("deploy.sh").read_text(encoding="utf-8")

    assert "REMOTE_COMPLETION_SENTINEL" in deploy
    # Emitted as the final remote statement, then verified on the local side.
    assert 'echo "${REMOTE_COMPLETION_SENTINEL}"' in deploy
    assert 'grep -q "^${REMOTE_COMPLETION_SENTINEL}\\$" "${REMOTE_LOG}"' in deploy
