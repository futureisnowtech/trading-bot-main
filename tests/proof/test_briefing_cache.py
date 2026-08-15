"""Proof: the cockpit's standing briefings cache, expire, and stay read-only.

These answers are regenerated unattended whenever they age past a 4-hour TTL, so
the surface they run on must not be able to reach a write-tier tool.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import dashboard.briefing_cache as bc
from runtime import brain


def test_briefing_surface_is_read_only():
    """A timer-driven refresh must never be able to patch code or change params."""
    assert bc.BRIEFING_SURFACE not in brain._WRITE_SURFACES
    tools = brain.tools_for(bc.BRIEFING_SURFACE)
    registry = brain.get_registry()
    write_names = {name for name, tool in registry.items() if tool.tier == brain.WRITE}
    exposed = {getattr(t, "__name__", "") for t in tools}
    assert not (exposed & write_names), f"write tools exposed to briefings: {exposed & write_names}"


def test_ttl_is_four_hours():
    assert bc.BRIEFING_TTL_SECONDS == 4 * 60 * 60


def test_five_briefings_with_unique_labels():
    assert len(bc.BRIEFINGS) == 5
    assert len(set(bc.BRIEFING_LABELS)) == 5
    assert set(bc.PROMPTS_BY_LABEL) == set(bc.BRIEFING_LABELS)


def test_ungenerated_briefings_report_as_stale(tmp_path):
    db = str(tmp_path / "t.db")
    out = bc.get_briefings(db)
    assert set(out) == set(bc.BRIEFING_LABELS)
    for label in bc.BRIEFING_LABELS:
        assert out[label]["never_generated"] is True
        assert out[label]["stale"] is True
        assert out[label]["answer"] == ""


def test_refresh_stores_answer_and_clears_staleness(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    label = bc.BRIEFING_LABELS[0]
    monkeypatch.setattr(brain, "ask", lambda messages, surface="": "All systems nominal.", raising=False)

    result = bc.refresh_briefing(label, db_path=db)
    assert result["ok"] is True

    out = bc.get_briefings(db)[label]
    assert out["answer"] == "All systems nominal."
    assert out["stale"] is False
    assert out["never_generated"] is False


def test_answer_older_than_the_ttl_reads_stale(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    label = bc.BRIEFING_LABELS[0]
    monkeypatch.setattr(brain, "ask", lambda messages, surface="": "cached", raising=False)
    bc.refresh_briefing(label, db_path=db)

    old = (datetime.now(timezone.utc) - timedelta(seconds=bc.BRIEFING_TTL_SECONDS + 60)).isoformat()
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE cockpit_briefings SET generated_at=? WHERE label=?", (old, label))

    out = bc.get_briefings(db)[label]
    assert out["stale"] is True
    assert out["answer"] == "cached", "a stale answer is still shown, just flagged"


def test_refresh_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    label = bc.BRIEFING_LABELS[0]

    def _boom(messages, surface=""):
        raise RuntimeError("provider down")

    monkeypatch.setattr(brain, "ask", _boom, raising=False)
    result = bc.refresh_briefing(label, db_path=db)
    assert result["ok"] is False
    assert "provider down" in result["error"]
    assert "provider down" in bc.get_briefings(db)[label]["error"]


def test_degraded_answer_is_not_marked_ok(tmp_path, monkeypatch):
    """A '⚠️ Brain is inactive' string is a failure, not an answer."""
    db = str(tmp_path / "t.db")
    label = bc.BRIEFING_LABELS[0]
    monkeypatch.setattr(
        brain, "ask",
        lambda messages, surface="": "⚠️ Brain is inactive: DEEPSEEK_API_KEY is not set.",
        raising=False,
    )
    assert bc.refresh_briefing(label, db_path=db)["ok"] is False


def test_refresh_all_covers_every_label(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    asked = []

    def _ask(messages, surface=""):
        asked.append(messages[0]["content"])
        return "ok"

    monkeypatch.setattr(brain, "ask", _ask, raising=False)
    results = bc.refresh_all_briefings(db_path=db)
    assert len(results) == 5
    assert set(asked) == set(bc.PROMPTS_BY_LABEL.values())


def test_refresh_stale_skips_fresh_answers(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(brain, "ask", lambda messages, surface="": "fresh", raising=False)
    bc.refresh_all_briefings(db_path=db)

    calls = []
    monkeypatch.setattr(
        brain, "ask",
        lambda messages, surface="": calls.append(1) or "again",
        raising=False,
    )
    assert bc.refresh_stale_briefings(db_path=db) == []
    assert calls == []


def test_refresh_is_wired_into_the_daemon_that_production_runs():
    """The wiring lived in forecast.runner's scheduler first, where it was dead code.

    execution_daemon.py is the container's command; it imports run_execution_cycle
    directly and never calls start_forecast_lane, so anything registered on that
    scheduler never fires on the droplet.
    """
    daemon_src = (_ROOT / "execution_daemon.py").read_text(encoding="utf-8")
    assert "_start_briefing_refresh_if_due()" in daemon_src
    assert "refresh_stale_briefings" in daemon_src

    runner_src = (_ROOT / "forecast" / "runner.py").read_text(encoding="utf-8")
    assert "schedule.every(4).hours" not in runner_src, (
        "briefing refresh must not be scheduled in start_forecast_lane; "
        "production never calls it"
    )


def test_daemon_refresh_thread_does_not_stack(monkeypatch):
    import execution_daemon as ed

    ed._briefing_refresh_running.clear()
    started = []
    monkeypatch.setattr(
        ed.threading, "Thread",
        lambda *a, **k: type("T", (), {"start": lambda self: started.append(1)})(),
        raising=False,
    )

    ed._start_briefing_refresh_if_due()
    ed._start_briefing_refresh_if_due()
    assert started == [1], "a second refresh must not start while one is in flight"

    ed._briefing_refresh_running.clear()
    ed._start_briefing_refresh_if_due()
    assert started == [1, 1]
    ed._briefing_refresh_running.clear()
