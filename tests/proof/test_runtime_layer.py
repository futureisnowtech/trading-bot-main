"""
tests/proof/test_runtime_layer.py — Runtime truth layer invariants for the lean Kalshi stack.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_ROOT = ROOT / "dashboard"
ENGINE_DOCKERFILE = ROOT / "Dockerfile"
DASHBOARD_DOCKERFILE = ROOT / "Dockerfile.dashboard"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_ROOT))


def _insert_event_raw(db_path: Path, source: str, message: str, level: str = "ERROR") -> None:
    ts = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO system_events (ts, level, source, message) VALUES (?,?,?,?)",
            (ts, level, source, message),
        )


def test_runtime_tables_init(proof_runtime, monkeypatch):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    rs.init_runtime_tables(db_path=db)

    with sqlite3.connect(proof_runtime.db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "system_runtime_state" in tables
    assert "lane_runtime_state" in tables


def test_upsert_system_state_round_trips(proof_runtime, monkeypatch):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    rs.init_runtime_tables(db_path=db)
    rs.upsert_system_state(
        db_path=db,
        process_mode="live",
        active_lanes='["forecast"]',
        launch_readiness_state="READY",
    )

    state = rs.get_system_state(db_path=db)
    assert state["process_mode"] == "live"
    assert "forecast" in state["active_lanes"]
    assert state["launch_readiness_state"] == "READY"


def test_upsert_lane_state_forecast_round_trips(proof_runtime, monkeypatch):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    rs.init_runtime_tables(db_path=db)
    rs.upsert_lane_state(
        "forecast",
        db_path=db,
        enabled=1,
        active=1,
        connected=1,
        tradable=1,
        health="OK",
        readiness_state="OPERATIONAL",
    )

    lane = rs.get_lane_state("forecast", db_path=db)
    assert lane["lane_id"] == "forecast"
    assert lane["connected"] == 1
    assert lane["readiness_state"] == "OPERATIONAL"


def test_upsert_lane_state_bootstraps_runtime_tables(proof_runtime, monkeypatch):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    rs.upsert_lane_state("forecast", db_path=db, enabled=1, active=1)

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            "SELECT lane_id, enabled, active FROM lane_runtime_state WHERE lane_id='forecast'"
        ).fetchone()

    assert row == ("forecast", 1, 1)


def test_mark_lane_heartbeat_updates_timestamp(proof_runtime, monkeypatch):
    import runtime.runtime_state as rs

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    rs.init_runtime_tables(db_path=db)
    rs.upsert_lane_state("forecast", db_path=db, enabled=1)
    rs.mark_lane_heartbeat("forecast", db_path=db)

    lane = rs.get_lane_state("forecast", db_path=db)
    assert lane["last_heartbeat_at"]


def test_incident_ingest_groups_forecast_errors(proof_runtime, monkeypatch):
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)
    it.init_incident_table(db_path=db)

    for _ in range(5):
        _insert_event_raw(
            proof_runtime.db_path,
            source="ForecastRunner",
            message="quote harvest timeout",
            level="ERROR",
        )

    upserted = it.ingest_system_events(lookback_minutes=120, db_path=db)
    incidents = it.get_open_incidents(db_path=db)

    assert upserted == 1
    assert len(incidents) == 1
    assert incidents[0]["lane_id"] == "forecast"
    assert incidents[0]["count"] >= 5


def test_incident_ingest_does_not_recount_same_events(proof_runtime, monkeypatch):
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)
    it.init_incident_table(db_path=db)

    for _ in range(2):
        _insert_event_raw(
            proof_runtime.db_path,
            source="KalshiBroker",
            message="Weather discovery skipped: too_many_requests",
            level="WARNING",
        )

    assert it.ingest_system_events(lookback_minutes=120, db_path=db) == 1
    assert it.ingest_system_events(lookback_minutes=120, db_path=db) == 0
    assert it.get_open_incidents(db_path=db)[0]["count"] == 2

    _insert_event_raw(
        proof_runtime.db_path,
        source="KalshiBroker",
        message="Weather discovery skipped: too_many_requests",
        level="WARNING",
    )
    assert it.ingest_system_events(lookback_minutes=120, db_path=db) == 1
    assert it.get_open_incidents(db_path=db)[0]["count"] == 3


def test_incident_ingest_non_forecast_sources_fall_back_to_system(proof_runtime, monkeypatch):
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)
    it.init_incident_table(db_path=db)

    _insert_event_raw(
        proof_runtime.db_path,
        source="telegram_daemon",
        message="operator command failed",
        level="ERROR",
    )

    it.ingest_system_events(lookback_minutes=120, db_path=db)
    incidents = it.get_open_incidents(db_path=db)

    assert incidents
    assert incidents[0]["lane_id"] == "system"


def test_warn_level_incident_is_classified_as_warning(proof_runtime, monkeypatch):
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)
    it.init_incident_table(db_path=db)

    _insert_event_raw(
        proof_runtime.db_path,
        source="KalshiBroker",
        message="Position sync error: timeout",
        level="WARN",
    )

    it.ingest_system_events(lookback_minutes=120, db_path=db)
    incidents = it.get_open_incidents(db_path=db)

    assert incidents
    assert incidents[0]["severity"] == "WARNING"


def test_incident_sync_notifies_operator_for_rate_limit(proof_runtime, monkeypatch):
    import runtime.incident_tracker as it

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(it, "DB_PATH", db, raising=False)
    it.init_incident_table(db_path=db)

    _insert_event_raw(
        proof_runtime.db_path,
        source="ForecastRunner",
        message="[ForecastRunner] KXHIGHNY execution_result: too_many_requests (local_rate_limit_cooldown)",
        level="WARNING",
    )

    captured = {}

    def fake_notify_system(title, detail, severity="INFO", telegram=False, data=None):
        captured["title"] = title
        captured["detail"] = detail
        captured["severity"] = severity
        captured["telegram"] = telegram
        captured["data"] = data or {}

    monkeypatch.setattr("notifications.notification_engine.notify_system", fake_notify_system)

    result = it.sync_incidents_and_notify(lookback_minutes=120, db_path=db)
    incidents = it.get_open_incidents(db_path=db)

    assert result["alerted"] == 1
    assert captured["telegram"] is True
    assert captured["severity"] == "WARNING"
    assert "too_many_requests" in captured["detail"]
    assert incidents[0]["last_alerted_count"] >= 1


def test_position_reconciliation_logs_completion_event(proof_runtime, monkeypatch):
    import runtime.position_reconciler as pr

    broker = MagicMock()
    broker.connect.return_value = True
    broker.get_positions.return_value = [{"ticker": "KXHIGHNY-26JUN04-B80.5"}]

    with patch("execution.kalshi_broker.get_kalshi_broker", return_value=broker):
        pr.run_reconciliation(db_path=str(proof_runtime.db_path))

    with sqlite3.connect(proof_runtime.db_path) as conn:
        row = conn.execute(
            """
            SELECT source, message
            FROM system_events
            WHERE source='PositionReconciler'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert "Kalshi holdings=1" in row[1]


def test_lane_economics_forecast_uses_live_fee_model():
    from runtime.economics import get_lane_economics, is_trade_viable

    econ = get_lane_economics("forecast")
    assert econ.lane_id == "forecast"
    assert econ.taker_fee_pct == 0.07
    assert econ.maker_fee_pct == 0.0175
    assert round(econ.min_viable_edge_pct, 4) == 0.035
    assert is_trade_viable("forecast", 0.0) is False


def test_execution_engine_uses_long_lived_daemon():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "execution_daemon.py" in compose
    assert "while true; do python3 sniper_cron.py" not in compose


def test_runtime_compose_uses_canonical_images_and_non_root_user():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${IMAGE_NAME:-ghcr.io/futureisnowtech/trading-bot-main}:latest" in compose
    assert "${DASHBOARD_IMAGE_NAME:-ghcr.io/futureisnowtech/trading-bot-main-dashboard}:latest" in compose
    assert compose.count('user: "${ALGO_UID:-1000}:${ALGO_GID:-1000}"') == 2


def test_runtime_images_disable_bytecode_and_drop_root():
    engine = ENGINE_DOCKERFILE.read_text(encoding="utf-8")
    dashboard = DASHBOARD_DOCKERFILE.read_text(encoding="utf-8")

    for text in (engine, dashboard):
        assert "PYTHONDONTWRITEBYTECODE=1" in text
        assert "USER appuser" in text
        assert "useradd --uid 1000 --gid 1000" in text


def test_deploy_script_audits_remote_ownership_and_runs_helper_containers_as_host_user():
    deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    assert "Auditing remote ownership..." in deploy
    assert "remote_ownership_report()" in deploy
    assert 'REMOTE_UID="\\$(id -u)"' in deploy
    assert 'REMOTE_GID="\\$(id -g)"' in deploy
    assert 'docker run --rm -u "\\${REMOTE_UID}:\\${REMOTE_GID}" -v ${PROJECT_DIR}:/workspace alpine:3.20 sh -lc \\' in deploy
    assert 'export ALGO_UID="\\$(id -u)"' in deploy
    assert 'export ALGO_GID="\\$(id -g)"' in deploy
    # Every helper *container* that touches the bind mount runs as the deploy
    # user so bind-mounted files never drift to root ownership.
    assert deploy.count('--user "\\${ALGO_UID}:\\${ALGO_GID}"') >= 1
    # The host service-status artifact is no longer written by a helper
    # container at all -- it is written natively by the deploy user, on a timer,
    # so it stays fresh between deploys instead of going stale after 30 minutes.
    assert "refresh_host_service_status.sh" in deploy
    assert 'Deploy introduced non-${NYC_USER} ownership drift.' in deploy


def test_approval_queue_promote_release_uses_canonical_command(tmp_path, monkeypatch):
    import config
    import runtime.approvals as approvals

    db = str(tmp_path / "approvals.db")
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(
        "notifications.agent_tools.run_release_audit",
        lambda command: f"ran {command}",
    )

    approvals.request_change("promote_release")
    pending = approvals.list_pending()

    assert pending
    assert approvals.resolve(int(pending[0]["id"]), approve=True) == "ran promote"


def test_approval_queue_can_create_cerebro_experiments(tmp_path, monkeypatch):
    import config
    import runtime.approvals as approvals

    db = str(tmp_path / "approvals_cerebro.db")
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(
        "intelligence.cerebro.create_experiment_from_insight",
        lambda insight_id, approved_by="": {
            "experiment_id": "ce-test",
            "insight_id": insight_id,
            "status": "APPROVED_FOR_SHADOW",
            "approved_by": approved_by,
        },
    )

    approvals.request_change("create_cerebro_experiment", {"insight_id": "ci-test"}, "selected from archive")
    pending = approvals.list_pending()

    assert pending
    resolved = approvals.resolve(int(pending[0]["id"]), approve=True)
    assert "ce-test" in resolved


def test_approval_queue_dedupes_pending_identical_requests(tmp_path, monkeypatch):
    import config
    import runtime.approvals as approvals

    db = str(tmp_path / "approvals_dedupe.db")
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)

    first = approvals.request_change(
        "create_cerebro_experiment",
        {"insight_id": "ci-test"},
        "selected from archive",
        dedupe_pending=True,
    )
    second = approvals.request_change(
        "create_cerebro_experiment",
        {"insight_id": "ci-test"},
        "selected from archive",
        dedupe_pending=True,
    )
    pending = approvals.list_pending()

    assert "Proposal #1 queued" in first
    assert "already pending" in second
    assert len(pending) == 1


def test_agent_read_file_can_read_repo_files_without_name_error():
    import notifications.agent_tools as tools

    content = tools.read_file(str(ROOT / "VERSION.py"), start_line=1, end_line=8)

    assert "VERSION" in content


def test_jarvis_open_positions_uses_canonical_broker_side(monkeypatch):
    import dashboard.jarvis_brain as jb

    monkeypatch.setattr(
        "runtime.operator_truth.get_live_kalshi_status",
        lambda: {
            "broker_positions": [
                {"ticker": "KXLOWNY-12AUG26-T70", "side": "NO", "qty": 4, "entry_price": 0.37}
            ],
            "position_drift": {"has_drift": False},
        },
    )

    output = jb.get_open_positions()

    assert "KXLOWNY-12AUG26-T70 (NO)" in output


def test_operator_brief_summarizes_live_truth_in_plain_english(monkeypatch):
    import dashboard.jarvis_brain as jb

    monkeypatch.setattr(
        "runtime.operator_truth.get_live_kalshi_status",
        lambda: {
            "broker_connected": True,
            "broker_positions_count": 2,
            "active_markets": 11,
            "position_drift": {"has_drift": True},
        },
    )
    monkeypatch.setattr(
        "runtime.operator_truth.get_release_status",
        lambda truth=None: {
            "entries_allowed": False,
            "current_release_verdict": "BLOCKED",
            "top_infrastructure_blockers": ["release_audit_pending_new_build"],
            "critical_incidents": [],
            "open_incidents": {"total_open": 1},
            "provider_mode": "deterministic_multi_model",
        },
    )
    monkeypatch.setattr("runtime.approvals.list_pending", lambda: [{"id": 17}])

    output = jb.get_operator_brief()

    assert "Trading status: PAUSED (BLOCKED)" in output
    assert "Book sync: broker and database do NOT agree." in output
    assert "Pending change requests: 1" in output
    assert "Main blocker: release_audit_pending_new_build" in output


def test_trading_readiness_summary_explains_blockers_in_plain_english(monkeypatch):
    import dashboard.jarvis_brain as jb

    monkeypatch.setattr(
        "runtime.operator_truth.get_live_kalshi_status",
        lambda: {
            "forecast_lane": {"blocked_reason": "stale_runtime_heartbeat"},
            "recent_vetoes": {"top_reasons": [{"reason": "missing_quotes", "count": 3}]},
            "recent_execution": {"top_outcomes": [{"outcome": "too_many_requests", "count": 2}]},
        },
    )
    monkeypatch.setattr(
        "runtime.operator_truth.get_release_status",
        lambda truth=None: {
            "entries_allowed": False,
            "top_infrastructure_blockers": ["broker_disconnected"],
        },
    )
    monkeypatch.setattr("runtime.approvals.list_pending", lambda: [])

    output = jb.get_trading_readiness_summary()

    assert "blocked from placing new trades" in output
    assert "Main hard blocker: broker_disconnected" in output
    assert "Lane-level block reason: stale_runtime_heartbeat" in output
    assert "Most common recent pass/fail filter: missing_quotes (3 times)." in output
    assert "Most common recent execution issue: too_many_requests (2 times)." in output


def test_jarvis_preloaded_prompts_are_plain_english_and_operator_focused():
    # The labels and prompts now live in dashboard/briefing_cache.py, which is the
    # single source shared by the orb chips and the cached briefing tabs. Asserting
    # against a second copy in streamlit_app.py is what let them drift before.
    from dashboard.briefing_cache import BRIEFING_LABELS, PROMPTS_BY_LABEL

    assert "🧭 What Needs Attention?" in BRIEFING_LABELS
    assert "🛑 Why Isn't It Trading?" in BRIEFING_LABELS
    assert "💸 Are Fees Hurting Us?" in BRIEFING_LABELS
    assert "Why No Trades?" not in BRIEFING_LABELS

    prompts = " ".join(PROMPTS_BY_LABEL.values())
    assert "Call get_operator_brief" in prompts
    assert "Call get_trading_readiness_summary" in prompts

    # And the cockpit must actually consume that source rather than redefining it.
    text = (ROOT / "dashboard" / "streamlit_app.py").read_text(encoding="utf-8")
    assert "from dashboard.briefing_cache import" in text
    assert "PROMPTS_BY_LABEL" in text


def test_telegram_surface_exposes_plain_english_operator_shortcuts():
    text = (ROOT / "notifications" / "telegram_bot.py").read_text(encoding="utf-8")

    assert 'CommandHandler("brief", brief_command)' in text
    assert 'CommandHandler("why", why_command)' in text
    assert 'CommandHandler("changes", changes_command)' in text
    assert "/brief - plain-English health summary and what needs attention" in text


def test_execution_cycle_stamps_lane_heartbeat_before_the_entry_gate(
    proof_runtime, monkeypatch
):
    """A slow cycle must not age out its own liveness stamp.

    The release gate reads last_heartbeat_at from inside run_strategy_cycle. While the
    only writer was run_position_monitor -- which run_execution_cycle calls *after* the
    strategy pass, and whose 30s schedule never fires under execution_daemon.py -- any
    cycle slower than FORECAST_HEARTBEAT_STALE_SECONDS refused its own entries with
    stale_runtime_heartbeat while the broker was connected and healthy.
    """
    import config
    import forecast.runner as fr
    import runtime.runtime_state as rs
    from runtime.operator_truth import is_lane_heartbeat_fresh

    db = str(proof_runtime.db_path)
    monkeypatch.setattr(rs, "DB_PATH", db, raising=False)
    monkeypatch.setattr(config, "KALSHI_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "FORECAST_LANE_ACTIVE", True, raising=False)

    # Two hours stale: only a write made earlier in this same cycle can let the
    # gate's read pass.
    rs.upsert_lane_state(
        "forecast",
        db_path=db,
        enabled=1,
        active=1,
        last_heartbeat_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )

    class _StubBroker:
        def is_connected(self):
            return True

        def connect(self):
            return True

        def get_positions(self):
            return []

        def get_account_balance(self):
            return 250.0

    seen: dict[str, object] = {}

    def _strategy(bankroll: float = 100.0):
        lane = rs.get_lane_state("forecast", db_path=db)
        seen["fresh_at_gate"] = is_lane_heartbeat_fresh(lane["last_heartbeat_at"])
        return []

    monkeypatch.setattr(fr, "_get_broker", lambda: _StubBroker(), raising=False)
    monkeypatch.setattr(fr, "run_discovery_cycle", lambda: {}, raising=False)
    monkeypatch.setattr(fr, "run_strategy_cycle", _strategy, raising=False)
    monkeypatch.setattr(fr, "run_position_monitor", lambda: None, raising=False)
    monkeypatch.setattr(fr, "_cache_forecast_state", lambda: {}, raising=False)

    summary = fr.run_execution_cycle(
        bankroll=100.0, refresh_quotes=False, sync_resolutions=False
    )

    assert summary["broker_connected"] is True
    assert seen.get("fresh_at_gate") is True, (
        "entry gate read a stale heartbeat inside the cycle that was supposed to "
        "have refreshed it"
    )
