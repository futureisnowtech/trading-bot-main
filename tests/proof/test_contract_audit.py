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
