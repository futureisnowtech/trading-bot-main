"""The boundary auditor must stay able to catch the bugs it was built for.

An auditor that silently stops detecting is worse than none, so these tests
reconstruct each real defect and assert the checks still fire.
"""
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "contract_audit.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(AUDIT), *args],
        capture_output=True, text=True, cwd=str(ROOT),
    )


def test_repo_is_currently_clean():
    r = _run("--strict")
    assert r.returncode == 0, f"boundary audit found regressions:\n{r.stdout}"


def test_audit_reports_every_check():
    """A check that quietly disappears would look like a pass forever."""
    out = _run().stdout
    for n in range(1, 8):
        assert f"CHECK {n}" in out, f"CHECK {n} vanished from the audit"


def test_detects_undefined_config_attribute(tmp_path, monkeypatch):
    """The ML_RETRAIN_MIN_HOURS / MAKER_ENTRY_TIMEOUT_SECONDS class."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import contract_audit as ca

    assert "FUTURES_LANE_ACTIVE" in ca.INTENTIONALLY_ABSENT, (
        "deliberate absences must stay allowlisted or the audit cries wolf"
    )


def test_literal_set_resolution_handles_annotated_frozenset():
    """MAKER_EXIT_ELIGIBLE_REASONS: frozenset[str] = frozenset({...}).

    The annotation contains '=' inside the type, which broke a naive regex and
    made the vocabulary check silently pass on a real unreachable branch.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import contract_audit as ca

    src = "MY_REASONS: frozenset[str] = frozenset({'take_profit', 'scale_out'})\n"
    tmp = ROOT / "_audit_probe_tmp.py"
    tmp.write_text(src)
    try:
        sets = ca._resolve_literal_sets()
        assert sets.get("MY_REASONS") == {"take_profit", "scale_out"}
    finally:
        tmp.unlink()


def test_kalshi_enums_pin_the_american_spelling():
    """good_till_cancelled (two Ls) is the bug that cost 212 taker fills."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import contract_audit as ca

    tif = ca.KALSHI_ENUMS["time_in_force"]
    assert "good_till_canceled" in tif
    assert "good_till_cancelled" not in tif


# --------------------------------------------------------------- watchdog


def test_watchdog_is_edge_triggered(tmp_path, monkeypatch):
    """Alerts fire on change, not on every tick.

    A watchdog that repeats itself every 15 minutes gets muted by the operator,
    and a muted watchdog is the same as none.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "wd", str(ROOT / "scripts" / "watchdog.py"))
    wd = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("WATCHDOG_STATE", str(tmp_path / "s.json"))
    spec.loader.exec_module(wd)
    wd.STATE = tmp_path / "s.json"

    sent = []
    monkeypatch.setattr(wd, "notify", lambda m: sent.append(m))
    monkeypatch.setattr(wd, "collect", lambda: {"stalled": "no entries"})
    monkeypatch.setattr(sys, "argv", ["watchdog"])

    wd.main()
    assert len(sent) == 1 and "stalled" not in sent[0].lower() or sent
    wd.main()
    assert len(sent) == 1, "an unchanged problem must not alert twice"

    monkeypatch.setattr(wd, "collect", lambda: {})
    wd.main()
    assert len(sent) == 2 and "Recovered" in sent[1]


def test_watchdog_checks_the_failure_that_cost_the_most():
    """The enabled-but-inert check must exist by name.

    MAKER_ENTRY_ENABLED was True for months while producing zero attempts, and
    fees ate 313% of gross edge before anyone noticed.
    """
    src = (ROOT / "scripts" / "watchdog.py").read_text()
    assert "maker_inert" in src
    assert "MAKER_ENTRY_ENABLED" in src


def test_watchdog_detects_a_closed_release_gate(tmp_path, monkeypatch):
    """The silent halt that actually happened, and that "no entries" missed.

    A BLOCKED release artifact makes the runner scan normally while entering
    nothing. The stall check only notices after STALL_HOURS, so the gate itself
    has to be read directly.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "wd_gate", str(ROOT / "scripts" / "watchdog.py"))
    wd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wd)

    src = (ROOT / "scripts" / "watchdog.py").read_text()
    assert "gate_blocked" in src
    assert "READY_FOR_LIVE" in src, "must compare against the healthy verdict"
    assert "--no-persist" in src, (
        "the footgun that caused it must stay documented: release_audit.py "
        "--local persists its verdict and can halt live trading"
    )
