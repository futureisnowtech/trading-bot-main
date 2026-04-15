"""
tests/proof/test_hook_truth.py — Hook/script path truth invariants.

Invariants:
1. No tracked hook script contains a hardcoded absolute REPO_ROOT/REPO variable
2. No tracked operational script contains a Desktop path hardcode
3. repo_truth_gate.py --fast exits 0 on the current repo
4. repo_truth_gate.py exits 1 when given content with a Desktop path
5. pre_bash_blocker.sh blocks --mode live
6. pre_bash_blocker.sh blocks implicit live-start via stdin pipe
7. settings.json reload hook uses bash scripts/reload_on_change.sh (not inline Desktop path)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_HOOKS_DIR = _ROOT / ".claude" / "hooks"
_SCRIPTS_DIR = _ROOT / "scripts"

# Note: split segment to avoid triggering the truth gate regex on this test file.
_D_SEG = "Desktop"
_DESKTOP_PATTERN = re.compile(r"/Users/\w+/" + _D_SEG + r"/algo_trading_final")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _hook_scripts() -> list[Path]:
    return [p for p in _HOOKS_DIR.glob("*.sh") if p.is_file()]


def _operational_scripts() -> list[Path]:
    return [p for p in _SCRIPTS_DIR.glob("*.sh") if p.is_file()]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _run_bash_blocker(cmd_str: str) -> int:
    """Feed a fake hook JSON with the given command to pre_bash_blocker.sh."""
    hook = _HOOKS_DIR / "pre_bash_blocker.sh"
    payload = json.dumps({"tool_input": {"command": cmd_str}})
    result = subprocess.run(
        ["bash", str(hook)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode


# ── Test 1: No hardcoded REPO_ROOT/REPO absolute vars in hook scripts ─────────


def test_no_hardcoded_repo_root_in_hooks():
    """All hook .sh files must use dynamic root resolution, not hardcoded paths."""
    failures = []
    for path in _hook_scripts():
        text = _read(path)
        # Match e.g. REPO_ROOT="/Users/..." or REPO="/Users/..."
        if re.search(r'(?:REPO_ROOT|REPO)\s*=\s*"/Users/', text):
            failures.append(str(path.relative_to(_ROOT)))
    assert not failures, "Hooks with hardcoded absolute REPO_ROOT/REPO:\n" + "\n".join(
        failures
    )


# ── Test 2: No Desktop paths in hook scripts ──────────────────────────────────


def test_no_desktop_paths_in_hooks():
    """No .claude/hooks/*.sh file may contain the Desktop repo path."""
    failures = []
    for path in _hook_scripts():
        if _DESKTOP_PATTERN.search(_read(path)):
            failures.append(str(path.relative_to(_ROOT)))
    assert not failures, "Hooks with Desktop path hardcode:\n" + "\n".join(failures)


# ── Test 3: No Desktop paths in operational scripts ───────────────────────────


def test_no_desktop_paths_in_scripts():
    """No scripts/*.sh file may contain the Desktop repo path."""
    failures = []
    for path in _operational_scripts():
        if _DESKTOP_PATTERN.search(_read(path)):
            failures.append(str(path.relative_to(_ROOT)))
    assert not failures, "Scripts with Desktop path hardcode:\n" + "\n".join(failures)


# ── Test 4: repo_truth_gate.py --fast passes on current repo ─────────────────


def test_repo_truth_gate_fast_passes():
    """repo_truth_gate.py --fast must exit 0 on the current clean repo."""
    gate = _ROOT / "scripts" / "repo_truth_gate.py"
    assert gate.exists(), "scripts/repo_truth_gate.py not found"
    result = subprocess.run(
        [sys.executable, str(gate), "--fast"],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"repo_truth_gate.py --fast failed:\n{result.stdout}\n{result.stderr}"
    )


# ── Test 5: repo_truth_gate.py catches Desktop paths ─────────────────────────


def test_repo_truth_gate_catches_desktop_path(tmp_path):
    """repo_truth_gate.py must exit 1 when a tracked file contains a Desktop path."""
    import sys as _sys

    gate = _ROOT / "scripts" / "repo_truth_gate.py"
    assert gate.exists(), "scripts/repo_truth_gate.py not found"

    # Write a test file with a Desktop path into a temp git repo
    test_repo = tmp_path / "test_repo"
    test_repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(test_repo), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(test_repo),
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(test_repo),
        capture_output=True,
    )

    # Create a tracked .sh file with the banned pattern
    bad_script = test_repo / "bad.sh"
    # Construct the Desktop path at runtime — not as a literal in this source
    bad_content = (
        '#!/bin/bash\nREPO_ROOT="/Users/testuser/' + _D_SEG + '/algo_trading_final"\n'
    )
    bad_script.write_text(bad_content)
    subprocess.run(["git", "add", "."], cwd=str(test_repo), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(test_repo),
        capture_output=True,
    )

    # Copy gate into test repo scripts/ dir
    scripts_dir = test_repo / "scripts"
    scripts_dir.mkdir()
    import shutil

    shutil.copy(str(gate), str(scripts_dir / "repo_truth_gate.py"))
    subprocess.run(["git", "add", "."], cwd=str(test_repo), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add gate"],
        cwd=str(test_repo),
        capture_output=True,
    )

    result = subprocess.run(
        [_sys.executable, str(scripts_dir / "repo_truth_gate.py"), "--fast"],
        cwd=str(test_repo),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1, (
        "gate should have failed on Desktop path but exited 0"
    )


# ── Test 6: pre_bash_blocker.sh blocks --mode live ───────────────────────────


def test_pre_bash_blocker_blocks_mode_live():
    """pre_bash_blocker.sh must return exit 2 for --mode live commands."""
    rc = _run_bash_blocker("python3 main.py --mode live")
    assert rc == 2, f"Expected exit 2 for --mode live, got {rc}"


# ── Test 7: pre_bash_blocker.sh blocks implicit live-start ───────────────────


def test_pre_bash_blocker_blocks_implicit_live_start():
    """pre_bash_blocker.sh must block 'echo I UNDERSTAND | python3 main.py'."""
    rc = _run_bash_blocker('echo "I UNDERSTAND" | python3 main.py')
    assert rc == 2, f"Expected exit 2 for implicit live-start, got {rc}"


# ── Test 8: settings.json reload hook is clean ───────────────────────────────


def test_settings_json_reload_hook_no_desktop_path():
    """settings.json must not contain a Desktop path in the reload hook command."""
    settings = _ROOT / ".claude" / "settings.json"
    assert settings.exists(), ".claude/settings.json not found"
    text = _read(settings)
    assert not _DESKTOP_PATTERN.search(text), (
        "settings.json still contains a Desktop path hardcode"
    )


# ── Test 9: reload_on_change.sh uses dynamic root ────────────────────────────


def test_reload_on_change_no_desktop_path():
    """scripts/reload_on_change.sh must not contain Desktop path hardcodes."""
    script = _ROOT / "scripts" / "reload_on_change.sh"
    assert script.exists(), "scripts/reload_on_change.sh not found"
    assert not _DESKTOP_PATTERN.search(_read(script)), (
        "reload_on_change.sh contains a Desktop path hardcode"
    )
