"""Meta-proof: CI and protected deploy must prove and ship the same thing."""

from __future__ import annotations

import re
from pathlib import Path

PROOF_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROOF_DIR.parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-nyc.yml"
PYTEST_INI = REPO_ROOT / "pytest.ini"


def _read(path: Path) -> str:
    assert path.exists(), f"Expected {path} to exist."
    return path.read_text(encoding="utf-8")


def _assert_workflow_runs_the_whole_proof_directory(path: Path, label: str) -> None:
    text = _read(path)
    assert "python -m pytest tests/proof" in text, (
        f"{label} must invoke the tests/proof directory as a whole, not a hand-picked list."
    )
    named = sorted(set(re.findall(r"tests/proof/test_[A-Za-z0-9_]+\.py", text)))
    assert not named, (
        f"{label} names individual proof files, so any proof outside the list can "
        f"fail without turning the check red: {named}"
    )


def _assert_workflow_applies_no_keyword_or_marker_filter(path: Path, label: str) -> None:
    offenders = []
    for line in _read(path).splitlines():
        # Strip the interpreter's own module flag: `python -m pytest` is not a
        # marker filter, but it matches the same shape.
        stripped = re.sub(r"\bpython[0-9.]*\s+-m\b", "", line)
        if re.search(r"(?:^|\s)-(?:k|m)\s+\S", stripped):
            offenders.append(line.strip())
    assert not offenders, (
        f"{label} narrows the proof suite with a deselecting filter: {offenders}"
    )


def _extract_test_env(path: Path) -> dict[str, str]:
    match = re.search(
        r"cat > \.env << 'EOF'\n(?P<body>.*?)\n\s*EOF",
        _read(path),
        re.DOTALL,
    )
    assert match, f"Could not parse the test .env block from {path.name}."

    parsed: dict[str, str] = {}
    for raw_line in match.group("body").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def test_ci_proof_gate_runs_the_whole_directory():
    _assert_workflow_runs_the_whole_proof_directory(CI_WORKFLOW, "ci.yml")


def test_protected_deploy_proof_gate_runs_the_whole_directory():
    _assert_workflow_runs_the_whole_proof_directory(
        DEPLOY_WORKFLOW,
        "deploy-nyc.yml",
    )


def test_ci_proof_gate_applies_no_keyword_or_marker_filter():
    _assert_workflow_applies_no_keyword_or_marker_filter(CI_WORKFLOW, "ci.yml")


def test_protected_deploy_proof_gate_applies_no_keyword_or_marker_filter():
    _assert_workflow_applies_no_keyword_or_marker_filter(
        DEPLOY_WORKFLOW,
        "deploy-nyc.yml",
    )


def test_pytest_ini_does_not_deselect_proofs():
    addopts = re.search(r"^addopts\s*=\s*(.+)$", _read(PYTEST_INI), re.MULTILINE)
    assert addopts, "pytest.ini must declare addopts explicitly."
    for flag in (" -k ", " -m ", "--ignore", "--deselect"):
        assert flag not in f" {addopts.group(1)} ", (
            f"pytest.ini addopts silently drops proofs via {flag.strip()!r}."
        )


def test_ci_and_protected_deploy_trust_the_same_branch():
    ci_groups = re.findall(r"branches:\s*\[([^\]]+)\]", _read(CI_WORKFLOW))
    ci_branches = {
        name.strip().strip("\"'") for group in ci_groups for name in group.split(",")
    }
    assert ci_branches, "Could not parse the branches ci.yml triggers on."

    deployed = set(
        re.findall(r"head_branch\s*==\s*'([^']+)'", _read(DEPLOY_WORKFLOW))
    )
    assert deployed, "Could not parse the branch deploy-nyc.yml trusts."

    assert deployed <= ci_branches, (
        f"Protected deploy ships {sorted(deployed)} but CI only runs on "
        f"{sorted(ci_branches)}. The deployed branch is not the proven branch."
    )


def test_ci_and_protected_deploy_pin_identical_test_env():
    assert _extract_test_env(DEPLOY_WORKFLOW) == _extract_test_env(CI_WORKFLOW), (
        "deploy-nyc.yml must pin the exact same test environment as ci.yml so "
        "protected deploy proves the same runtime posture that the GitHub check proves."
    )


def test_protected_deploy_uses_blessed_deploy_script():
    text = _read(DEPLOY_WORKFLOW)
    assert "./deploy.sh" in text, (
        "deploy-nyc.yml must route production deploys through ./deploy.sh so "
        "manual and workflow deploys share the same guarded path."
    )


def test_protected_deploy_verifies_live_service_names():
    text = _read(DEPLOY_WORKFLOW)
    assert 'grep "execution-engine"' in text
    assert 'grep "kalshi-cockpit"' in text
    assert 'grep "telegram-oracle"' not in text, (
        "deploy-nyc.yml still verifies a retired service name, so a correct deploy "
        "would fail its own post-deploy verification."
    )
