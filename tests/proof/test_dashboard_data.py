"""
tests/proof/test_dashboard_data.py — Invariant proof tests for dashboard/data/health.py.

These tests exist because dashboard/data/ had zero coverage, which meant changes
to get_error_rate_1h(), get_recent_errors_detail(), and get_health_check_failures()
could silently break consistency between the banner count and the breakdown panel.

The invariants tested here are the exact ones that were missed and caused the user
to see "20 errors in last hour" from stale health_check records:

  1. get_error_rate_1h()       — must exclude health_check source
  2. get_recent_errors_detail() — must exclude health_check source
  3. get_health_check_failures() — must reflect current live state (latest event only)
  4. Banner no_errors logic     — must be False when health is degraded, even if
                                  error_rate == 0 (the exact tunnel-vision bug)

If any of these tests fail, the dashboard will show stale or inconsistent error data.
"""

from __future__ import annotations

import sqlite3

import pytest

from support import insert_system_event


# ══════════════════════════════════════════════════════════════════════════════
# get_error_rate_1h — source exclusion invariants
# ══════════════════════════════════════════════════════════════════════════════


def test_error_rate_excludes_health_check(proof_runtime):
    """
    The exact bug: 20 health_check ERRORs were inflating the banner count.
    health_check errors are surfaced via get_health_check_failures() instead;
    they must never contribute to get_error_rate_1h().
    """
    for i in range(20):
        insert_system_event(
            proof_runtime.db_path,
            level="ERROR",
            source="health_check",
            message=f"Health 4/6 [UNHEALTHY] | FAIL: stagnant: ZRO({i + 100}m)",
        )
    from dashboard.data.health import get_error_rate_1h

    assert get_error_rate_1h() == 0, (
        "health_check ERRORs must not count — they are shown via get_health_check_failures()"
    )


def test_error_rate_counts_real_runtime_errors(proof_runtime):
    """Non-health_check errors from bot components must be counted."""
    insert_system_event(
        proof_runtime.db_path,
        level="ERROR",
        source="scanner",
        message="Kraken fetch timeout",
    )
    insert_system_event(
        proof_runtime.db_path,
        level="ERROR",
        source="v10_runner",
        message="Entry failed: size returned zero",
    )
    from dashboard.data.health import get_error_rate_1h

    assert get_error_rate_1h() == 2


def test_error_rate_mixed_sources_counts_only_real_errors(proof_runtime):
    """
    15 health_check ERRORs + 1 perps_engine ERROR must yield count=1, not 16.
    """
    for _ in range(15):
        insert_system_event(
            proof_runtime.db_path,
            level="ERROR",
            source="health_check",
            message="Health 3/6 [UNHEALTHY] | FAIL: stagnant: AXS(5000m)",
        )
    insert_system_event(
        proof_runtime.db_path,
        level="ERROR",
        source="perps_engine",
        message="close_position failed: connection reset",
    )
    from dashboard.data.health import get_error_rate_1h

    assert get_error_rate_1h() == 1


def test_error_rate_ignores_info_and_warning(proof_runtime):
    """INFO and WARNING rows must never be counted regardless of source."""
    insert_system_event(
        proof_runtime.db_path, level="INFO", source="scanner", message="scan ok"
    )
    insert_system_event(
        proof_runtime.db_path,
        level="WARNING",
        source="risk_manager",
        message="low margin",
    )
    from dashboard.data.health import get_error_rate_1h

    assert get_error_rate_1h() == 0


# ══════════════════════════════════════════════════════════════════════════════
# get_recent_errors_detail — source exclusion + classification invariants
# ══════════════════════════════════════════════════════════════════════════════


def test_error_detail_excludes_health_check(proof_runtime):
    """
    health_check ERROR rows must produce an empty breakdown panel.
    Before the fix, 20 health_check rows were showing as 'Stagnant Positions'
    even after the actual positions had been closed.
    """
    for _ in range(5):
        insert_system_event(
            proof_runtime.db_path,
            level="ERROR",
            source="health_check",
            message="Health 4/6 [UNHEALTHY] | FAIL: stagnant: XRP(6000m)",
        )
    from dashboard.data.health import get_recent_errors_detail

    assert get_recent_errors_detail() == [], (
        "health_check source must be excluded — use get_health_check_failures() for those"
    )


def test_error_detail_includes_scanner_errors(proof_runtime):
    """Scanner errors must appear in the breakdown with category and fix_prompt."""
    insert_system_event(
        proof_runtime.db_path,
        level="ERROR",
        source="scanner",
        message="Binance fetch failed: ReadTimeout",
    )
    from dashboard.data.health import get_recent_errors_detail

    errors = get_recent_errors_detail()
    assert len(errors) == 1
    assert errors[0]["source"] == "scanner"
    assert errors[0].get("category"), "Every error must have a category"
    assert errors[0].get("fix_prompt"), "Every error must have a fix_prompt"
    assert errors[0].get("fix_type") in ("Claude Code", "Codex")


def test_error_detail_deduplicates_by_fingerprint(proof_runtime):
    """
    Repeated similar messages (numbers differ) must be grouped into one entry.
    Without deduplication, a scanner retry loop writes 30 near-identical rows
    that would each render as a separate card.
    """
    for i in range(8):
        insert_system_event(
            proof_runtime.db_path,
            level="ERROR",
            source="scanner",
            message=f"Binance timeout after {i + 1}s attempt {i}",
        )
    from dashboard.data.health import get_recent_errors_detail

    errors = get_recent_errors_detail()
    assert len(errors) == 1, "8 similar scanner errors must collapse to 1 group"
    assert errors[0]["count"] == 8


def test_error_detail_separates_different_sources(proof_runtime):
    """Errors from different sources must appear as separate entries."""
    insert_system_event(
        proof_runtime.db_path,
        level="ERROR",
        source="scanner",
        message="Kraken timeout",
    )
    insert_system_event(
        proof_runtime.db_path,
        level="ERROR",
        source="perps_engine",
        message="close_position failed",
    )
    from dashboard.data.health import get_recent_errors_detail

    errors = get_recent_errors_detail()
    sources = {e["source"] for e in errors}
    assert "scanner" in sources
    assert "perps_engine" in sources
    assert "health_check" not in sources


# ══════════════════════════════════════════════════════════════════════════════
# get_health_check_failures — live state parsing invariants
# ══════════════════════════════════════════════════════════════════════════════


def test_health_failures_empty_when_latest_event_is_healthy(proof_runtime):
    """
    Old UNHEALTHY events must be ignored once the latest event is HEALTHY.
    This is the 'XRP still showing after close' scenario — old records in DB
    must not bleed into the live display.
    """
    # Write old unhealthy events
    for _ in range(5):
        insert_system_event(
            proof_runtime.db_path,
            level="ERROR",
            source="health_check",
            message="Health 4/6 [UNHEALTHY] | FAIL: stagnant: XRP(6364m)",
        )
    # Write a newer HEALTHY event
    insert_system_event(
        proof_runtime.db_path,
        level="INFO",
        source="health_check",
        message="Health 6/6 [HEALTHY]",
    )
    from dashboard.data.health import get_health_check_failures

    assert get_health_check_failures() == [], (
        "Old UNHEALTHY records must not show once latest event is HEALTHY"
    )


def test_health_failures_parses_multiple_failing_checks(proof_runtime):
    """A health message with 2 failing checks must produce 2 failure entries."""
    insert_system_event(
        proof_runtime.db_path,
        level="ERROR",
        source="health_check",
        message=(
            "Health 4/6 [UNHEALTHY] | FAIL: "
            "stagnant: Stagnant positions: ZRO(4253m) | "
            "scan_liveness: Last heartbeat 1200s ago (threshold 900s)"
        ),
    )
    from dashboard.data.health import get_health_check_failures

    failures = get_health_check_failures()
    assert len(failures) == 2
    check_names = {f["source"] for f in failures}
    assert "stagnant" in check_names
    assert "scan_liveness" in check_names


def test_health_failures_every_entry_has_fix_prompt(proof_runtime):
    """Every failure parsed from the health_check event must have a non-empty fix_prompt."""
    insert_system_event(
        proof_runtime.db_path,
        level="ERROR",
        source="health_check",
        message=(
            "Health 3/6 [UNHEALTHY] | FAIL: "
            "stagnant: Stagnant positions: AXS(5000m) | "
            "scan_liveness: Last heartbeat 999s ago (threshold 900s) | "
            "error_rate: 12 errors in last hour"
        ),
    )
    from dashboard.data.health import get_health_check_failures

    for f in get_health_check_failures():
        assert f.get("fix_prompt"), f"Missing fix_prompt on check '{f['source']}'"
        assert f.get("fix_type") in ("Claude Code", "Codex"), (
            f"fix_type must be 'Claude Code' or 'Codex', got: {f.get('fix_type')}"
        )


def test_health_failures_are_flagged_live(proof_runtime):
    """All health_check failures must carry live=True for the LIVE badge in the UI."""
    insert_system_event(
        proof_runtime.db_path,
        level="WARNING",
        source="health_check",
        message="Health 5/6 [DEGRADED] | FAIL: stagnant: Stagnant positions: PEPE(100m)",
    )
    from dashboard.data.health import get_health_check_failures

    for f in get_health_check_failures():
        assert f.get("live") is True, (
            f"live=True missing on '{f['source']}' — UI won't show LIVE badge"
        )


def test_health_failures_empty_when_no_events(proof_runtime):
    """No health_check events at all must return empty list (not raise)."""
    from dashboard.data.health import get_health_check_failures

    assert get_health_check_failures() == []


# ══════════════════════════════════════════════════════════════════════════════
# Banner no_errors consistency — the exact tunnel-vision invariant
# ══════════════════════════════════════════════════════════════════════════════


def test_banner_no_errors_false_when_health_degraded_and_no_runtime_errors(
    proof_runtime,
):
    """
    THE KEY INVARIANT. This is the exact class of bug that was missed:
    error_rate == 0 (no non-health_check errors) but health is DEGRADED.
    no_errors must be False so the banner shows ERRORS DETECTED.

    Without this test, a change to get_error_rate_1h() that excludes health_check
    could silently make the banner go green while health failures still exist.
    """
    insert_system_event(
        proof_runtime.db_path,
        level="WARNING",
        source="health_check",
        message="Health 5/6 [DEGRADED] | FAIL: stagnant: ZRO(4253m)",
    )
    from dashboard.data.health import get_error_rate_1h, get_health_check_failures

    error_rate = get_error_rate_1h()
    health_issues = get_health_check_failures()
    no_errors = error_rate == 0 and not health_issues

    assert error_rate == 0, "health_check rows must not inflate error_rate"
    assert len(health_issues) == 1, (
        "health degradation must be surfaced via get_health_check_failures"
    )
    assert no_errors is False, (
        "Banner must show ERRORS DETECTED when health is degraded, "
        "even if no runtime error rows exist in DB"
    )


def test_banner_no_errors_true_when_all_clear(proof_runtime):
    """Green banner requires both error_rate==0 AND no health failures."""
    insert_system_event(
        proof_runtime.db_path,
        level="INFO",
        source="health_check",
        message="Health 6/6 [HEALTHY]",
    )
    insert_system_event(
        proof_runtime.db_path,
        level="INFO",
        source="heartbeat",
        message="scan ok: 42 candidates → 1 entries",
    )
    from dashboard.data.health import get_error_rate_1h, get_health_check_failures

    error_rate = get_error_rate_1h()
    health_issues = get_health_check_failures()
    no_errors = error_rate == 0 and not health_issues

    assert no_errors is True, "All-clear state must produce no_errors=True"


def test_banner_no_errors_false_when_runtime_errors_exist(proof_runtime):
    """Runtime errors (non-health_check) alone must trigger ERRORS DETECTED."""
    insert_system_event(
        proof_runtime.db_path,
        level="INFO",
        source="health_check",
        message="Health 6/6 [HEALTHY]",
    )
    insert_system_event(
        proof_runtime.db_path,
        level="ERROR",
        source="scanner",
        message="All 3 exchange fetches failed",
    )
    from dashboard.data.health import get_error_rate_1h, get_health_check_failures

    error_rate = get_error_rate_1h()
    health_issues = get_health_check_failures()
    no_errors = error_rate == 0 and not health_issues

    assert error_rate == 1
    assert health_issues == []
    assert no_errors is False
