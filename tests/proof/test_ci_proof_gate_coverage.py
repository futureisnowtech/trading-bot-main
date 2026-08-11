"""Meta-proof: a green GitHub check must mean the whole proof suite passed.

This guards the two structural failures that let broken proofs ship silently:

  * ci.yml naming a curated list of proof files, and narrowing even that list
    with a ``-k`` filter, so newly added or newly failing proofs are never run.
  * ci.yml and deploy-nyc.yml trusting different branches, so the branch that
    gets deployed is not the branch that gets proven.
"""

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


def test_proof_gate_runs_the_whole_directory():
    text = _read(CI_WORKFLOW)
    assert "python -m pytest tests/proof" in text, (
        "CI must invoke the tests/proof directory as a whole, not a hand-picked list."
    )
    named = sorted(set(re.findall(r"tests/proof/test_[A-Za-z0-9_]+\.py", text)))
    assert not named, (
        "ci.yml names individual proof files, so any proof outside the list can "
        f"fail without turning the check red: {named}"
    )


def test_proof_gate_applies_no_keyword_or_marker_filter():
    offenders = []
    for line in _read(CI_WORKFLOW).splitlines():
        # Strip the interpreter's own module flag: `python -m pytest` is not a
        # marker filter, but it matches the same shape.
        stripped = re.sub(r"\bpython[0-9.]*\s+-m\b", "", line)
        if re.search(r"(?:^|\s)-(?:k|m)\s+\S", stripped):
            offenders.append(line.strip())
    assert not offenders, (
        f"ci.yml narrows the proof suite with a deselecting filter: {offenders}"
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
