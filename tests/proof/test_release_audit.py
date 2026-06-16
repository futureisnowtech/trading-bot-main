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
            "entry_scope": "HOURLY_ONLY",
            "infrastructure_rejections": [],
            "systematic_thin_liquidity": False,
        },
        active_markets=10,
        strict_runtime=True,
    )

    assert blockers == []
    assert warnings == ["no_entry_scope_inventory (HOURLY_ONLY)"]


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
            return None

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


def test_scan_live_market_surface_warms_weather_truth_for_weather_candidates(monkeypatch):
    import scripts.release_audit as ra

    monkeypatch.setattr(
        ra,
        "get_active_contracts",
        lambda db_path=None: [
            {
                "id": 1,
                "market_id": 9,
                "local_symbol": "KXTEMPNYCH-26JUN0522-T75.99",
                "contract_name": "Will the temp in New York City be above 75.99° on Jun 5, 2026 at 10pm EST?",
                "right": "C",
                "strike": 75.99,
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
                ticker="KXTEMPNYCH-26JUN0522-T75.99",
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
    assert payload["entry_scope"] == "HOURLY_ONLY"
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
