"""
tests/proof/test_hook_truth.py — Hook/script path truth invariants.

Invariants:
1.  No tracked hook script contains a hardcoded absolute REPO_ROOT/REPO variable
2.  No tracked operational script contains a Desktop path hardcode (absolute form)
3.  repo_truth_gate.py --fast exits 0 on the current repo
4.  repo_truth_gate.py exits 1 when given content with a Desktop path
5.  pre_bash_blocker.sh blocks --mode live
6.  pre_bash_blocker.sh blocks implicit live-start via stdin pipe
7.  settings.json reload hook uses bash scripts/reload_on_change.sh (not inline Desktop path)
8.  settings.json reload hook is clean (no Desktop path)
9.  reload_on_change.sh uses dynamic root
10. repo_truth_gate.py rejects tilde-form ~/Desktop path
11. repo_truth_gate.py ACTIVE_EXTS includes .md (covers markdown instruction surfaces)
12. .claude/commands/self-audit.md has no Desktop path
13. scripts/iphone.sh has no Desktop path (tilde or absolute)
14. install_hooks.sh pre-commit includes repo_truth_gate.py --fast
15. settings.json hook commands use $CLAUDE_PROJECT_DIR-rooted paths
16. .version is treated as a generated local artifact, not tracked source
17. pre_bash_blocker.sh allows the controlled go_live.py launcher
18. pre_bash_blocker.sh allows the controlled go_paper.py launcher
19. boot.py supports controlled live/paper mode selection
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
_DESKTOP_TILDE_PATTERN = re.compile(r"~/" + _D_SEG + r"/algo_trading_final")


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


# ── Test 10: truth gate rejects tilde-form ~/Desktop path ────────────────────


def test_repo_truth_gate_catches_tilde_desktop_path(tmp_path):
    """repo_truth_gate.py must exit 1 when a tracked file contains ~/Desktop/algo_trading_final."""
    gate = _ROOT / "scripts" / "repo_truth_gate.py"
    assert gate.exists(), "scripts/repo_truth_gate.py not found"

    test_repo = tmp_path / "test_repo_tilde"
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

    # Create a tracked .sh file with the tilde banned pattern (split to avoid self-match)
    bad_script = test_repo / "bad_tilde.sh"
    bad_content = (
        "#!/bin/bash\n# Usage: bash ~/"
        + _D_SEG
        + "/algo_trading_final/scripts/run.sh\n"
    )
    bad_script.write_text(bad_content)
    subprocess.run(["git", "add", "."], cwd=str(test_repo), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(test_repo),
        capture_output=True,
    )

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
        [sys.executable, str(scripts_dir / "repo_truth_gate.py"), "--fast"],
        cwd=str(test_repo),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1, (
        "gate should have failed on tilde Desktop path but exited 0\n" + result.stdout
    )


# ── Test 11: truth gate ACTIVE_EXTS includes .md ─────────────────────────────


def test_repo_truth_gate_active_exts_includes_md():
    """repo_truth_gate.py must include .md in ACTIVE_EXTS to cover markdown surfaces."""
    gate = _ROOT / "scripts" / "repo_truth_gate.py"
    assert gate.exists(), "scripts/repo_truth_gate.py not found"
    text = _read(gate)
    assert '".md"' in text or "'.md'" in text, (
        "repo_truth_gate.py ACTIVE_EXTS does not include .md — "
        "markdown instruction surfaces (.claude/commands/, AGENTS.md, CLAUDE.md) "
        "will not be scanned for Desktop path references"
    )


# ── Test 12: self-audit.md has no Desktop path ───────────────────────────────


def test_self_audit_md_no_desktop_path():
    """self-audit.md must not contain a Desktop repo path."""
    cmd_file = _ROOT / ".claude" / "commands" / "self-audit.md"
    assert cmd_file.exists(), ".claude/commands/self-audit.md not found"
    text = _read(cmd_file)
    assert not _DESKTOP_PATTERN.search(text), (
        "self-audit.md still contains an absolute Desktop path hardcode"
    )
    assert not _DESKTOP_TILDE_PATTERN.search(text), (
        "self-audit.md still contains a tilde Desktop path hardcode"
    )


# ── Test 13: iphone.sh has no Desktop path ───────────────────────────────────


def test_iphone_sh_no_desktop_path():
    """scripts/iphone.sh must not reference the Desktop repo path (absolute or tilde)."""
    script = _ROOT / "scripts" / "iphone.sh"
    assert script.exists(), "scripts/iphone.sh not found"
    text = _read(script)
    assert not _DESKTOP_PATTERN.search(text), (
        "scripts/iphone.sh still contains an absolute Desktop path hardcode"
    )
    assert not _DESKTOP_TILDE_PATTERN.search(text), (
        "scripts/iphone.sh still contains a tilde Desktop path hardcode"
    )


# ── Test 14: install_hooks.sh pre-commit includes truth gate --fast ───────────


def test_install_hooks_pre_commit_includes_truth_gate():
    """install_hooks.sh must wire repo_truth_gate.py --fast into the pre-commit hook."""
    script = _ROOT / "scripts" / "install_hooks.sh"
    assert script.exists(), "scripts/install_hooks.sh not found"
    text = _read(script)
    # Must have both the gate invocation AND --fast flag in the pre-commit block
    assert "repo_truth_gate.py" in text and "--fast" in text, (
        "install_hooks.sh pre-commit does not include repo_truth_gate.py --fast"
    )
    # Verify it appears BEFORE the pre-push section (i.e., in the pre-commit block)
    precommit_idx = text.find("pre-commit")
    prepush_idx = text.find("pre-push")
    gate_idx = text.find("repo_truth_gate.py")
    assert precommit_idx < gate_idx < prepush_idx, (
        "repo_truth_gate.py --fast is not in the pre-commit block of install_hooks.sh"
    )


# ── Test 15: settings.json hook commands use $CLAUDE_PROJECT_DIR ─────────────


def test_settings_json_uses_claude_project_dir():
    """settings.json hook commands must use $CLAUDE_PROJECT_DIR for robust absolute paths."""
    settings = _ROOT / ".claude" / "settings.json"
    assert settings.exists(), ".claude/settings.json not found"
    data = json.loads(_read(settings))

    # Collect all hook commands
    hook_commands = []
    for trigger, matchers in data.get("hooks", {}).items():
        for matcher_block in matchers:
            for h in matcher_block.get("hooks", []):
                cmd = h.get("command", "")
                if cmd:
                    hook_commands.append(cmd)

    assert hook_commands, "settings.json has no hook commands"

    for cmd in hook_commands:
        assert "$CLAUDE_PROJECT_DIR" in cmd, (
            f"settings.json hook command does not use $CLAUDE_PROJECT_DIR: {cmd!r}\n"
            "All hook commands must use $CLAUDE_PROJECT_DIR/... for robust path resolution"
        )


# ── Test 16: .version is ignored as a local generated artifact ───────────────


def test_version_file_is_gitignored():
    """.version must be gitignored so the local post-commit stamp does not dirty the repo."""
    gitignore = _ROOT / ".gitignore"
    assert gitignore.exists(), ".gitignore not found"
    entries = {
        line.strip()
        for line in _read(gitignore).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert ".version" in entries, (
        ".version must be gitignored because it is a local post-commit dashboard stamp"
    )


def test_pre_bash_blocker_allows_go_live_launcher():
    """pre_bash_blocker.sh must allow the controlled go_live.py launcher."""
    rc = _run_bash_blocker("python3 scripts/go_live.py")
    assert rc == 0, f"Expected exit 0 for go_live.py, got {rc}"


def test_pre_bash_blocker_allows_go_paper_launcher():
    """pre_bash_blocker.sh must allow the controlled go_paper.py launcher."""
    rc = _run_bash_blocker("python3 scripts/go_paper.py")
    assert rc == 0, f"Expected exit 0 for go_paper.py, got {rc}"


def test_boot_py_supports_controlled_mode_switch():
    """boot.py must support explicit mode selection and controlled live confirmation."""
    boot = _ROOT / "scripts" / "boot.py"
    assert boot.exists(), "scripts/boot.py not found"
    text = _read(boot)
    for token in ("ALGO_BOOT_MODE", "ALGO_LIVE_CONFIRM", "--confirm-live"):
        assert token in text, f"boot.py missing controlled launch token: {token}"
