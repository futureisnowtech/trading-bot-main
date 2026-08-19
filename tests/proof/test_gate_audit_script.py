from scripts.gate_audit import (
    _coerce_event_ts,
    parse_shadow_block_line,
    parse_veto_message,
    reason_family,
)


def test_parse_veto_message_extracts_ticker_and_reason():
    rec = parse_veto_message(
        1717540000.0,
        "[ForecastRunner] KXHIGHNY-04JUN26-T85 vetoed: market_truth_veto (Divergence=22.0% > 20%)",
    )

    assert rec is not None
    assert rec.ticker == "KXHIGHNY-04JUN26-T85"
    assert rec.reason == "market_truth_veto (Divergence=22.0% > 20%)"
    assert rec.family == "market_truth_veto"


def test_reason_family_normalizes_suffixes():
    assert reason_family("LOW_CONVICTION_ALPHA (Net_EV=0.0110 < 0.05)") == "LOW_CONVICTION_ALPHA"
    assert reason_family("stale_market_data") == "stale_market_data"


def test_parse_shadow_block_line_extracts_order_shape():
    attempt = parse_shadow_block_line(
        "[Kalshi] SHADOW MODE: Blocked POST /trade-api/v2/portfolio/orders "
        "body={'ticker': 'KXHIGHNY-04JUN26-T85', 'action': 'buy', 'side': 'yes', 'count': 4, 'type': 'limit'}"
    )

    assert attempt is not None
    assert attempt.ticker == "KXHIGHNY-04JUN26-T85"
    assert attempt.action == "BUY"
    assert attempt.side == "YES"
    assert attempt.count == 4
    assert attempt.order_type == "LIMIT"


def test_parse_shadow_block_line_ignores_noise():
    assert parse_shadow_block_line("ordinary log line") is None


def test_coerce_event_ts_accepts_isoformat_strings():
    ts = _coerce_event_ts("2026-06-04T22:30:00+00:00")

    assert ts is not None
    assert ts > 0


def test_gate_audit_script_execution_with_skip_run(tmp_path):
    """gate_audit.py must run cleanly with --skip-run from any CWD without ModuleNotFoundError."""
    import pathlib
    import subprocess
    import sys

    root = pathlib.Path(__file__).resolve().parents[2]
    script = root / "scripts" / "gate_audit.py"

    res = subprocess.run(
        [sys.executable, str(script), "--skip-run"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )

    assert res.returncode == 0, f"gate_audit.py failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert "KALSHI GATE AUDIT" in res.stdout
    assert "ModuleNotFoundError" not in res.stderr


def test_scripts_sys_path_safety_and_execution_all_audits(tmp_path):
    """Scripts gate_audit.py, watchdog.py, release_audit.py must be sys.path safe when called from any directory."""
    import os
    import pathlib
    import sqlite3
    import subprocess
    import sys

    root = pathlib.Path(__file__).resolve().parents[2]
    scripts_dir = root / "scripts"

    # 1. gate_audit.py --skip-run
    gate_script = scripts_dir / "gate_audit.py"
    res_gate = subprocess.run(
        [sys.executable, str(gate_script), "--skip-run"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert res_gate.returncode == 0, f"gate_audit.py failed:\n{res_gate.stderr}"
    assert "ModuleNotFoundError" not in res_gate.stderr

    # 2. watchdog.py --dry-run
    watchdog_script = scripts_dir / "watchdog.py"
    db_file = tmp_path / "trades.db"
    sqlite3.connect(str(db_file)).close()
    env = dict(os.environ, WATCHDOG_DB=str(db_file))
    res_wd = subprocess.run(
        [sys.executable, str(watchdog_script), "--dry-run"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_wd.returncode == 0, f"watchdog.py failed:\n{res_wd.stderr}"
    assert "ModuleNotFoundError" not in res_wd.stderr

    # 3. release_audit.py --help
    release_script = scripts_dir / "release_audit.py"
    res_rel = subprocess.run(
        [sys.executable, str(release_script), "--help"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert res_rel.returncode == 0, f"release_audit.py failed:\n{res_rel.stderr}"
    assert "ModuleNotFoundError" not in res_rel.stderr

