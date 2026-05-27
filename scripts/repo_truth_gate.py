#!/usr/bin/env python3
"""
scripts/repo_truth_gate.py — Repo truth gate.

Permanently prevents Desktop-path coupling, stale absolute-root hardcodes,
and live-start policy bypasses from re-entering the codebase.

Modes:
  --fast    Lightweight (for pre-commit): Desktop path scan + hook root check
            + live-start policy check
  --strict  Full (for pre-push/CI): everything in fast + CI config check

Exit codes:
  0 = all checks passed
  1 = one or more failures

Usage:
  python3 scripts/repo_truth_gate.py --fast    # pre-commit gate
  python3 scripts/repo_truth_gate.py --strict  # pre-push / CI gate
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# ── Patterns that must NEVER appear in active tracked files ──────────────────
# Split to avoid self-match: the regex won't match its own raw-string source
_D = "Desktop"
BANNED_PATTERNS: list[tuple[str, str]] = [
    (rf"/Users/\w+/{_D}/algo_trading_final", "Desktop path hardcode (absolute)"),
    (rf"~/{_D}/algo_trading_final", "Desktop path hardcode (tilde form)"),
]

# Dirs to skip during file scan
SKIP_DIRS: set[str] = {
    ".git",
    "__pycache__",
    "logs",
    "reference_repos",
    "brain",
    "docs",
    "memory",
}

# Files to skip (reference-only; repo_truth_gate may reference patterns for testing)
SKIP_FILES: set[str] = {
    "repo_truth_gate.py",
    "test_hook_truth.py",
    "settings.local.json",  # permission allow-list: legacy entries, not active code
    "CHANGELOG.md",  # historical commit log — mentions Desktop in past-tense descriptions only
}

# Active file extensions to scan
# .md is included to catch Desktop references in instruction surfaces
# (.gemini/commands/, .gemini/agents/, AGENTS.md, GEMINI.md)
ACTIVE_EXTS: set[str] = {".py", ".sh", ".json", ".yml", ".yaml", ".md"}

# ── CI config requirements ────────────────────────────────────────────────────
CI_FILE = _ROOT / ".github" / "workflows" / "ci.yml"
CI_REQUIRED_TOKENS: list[str] = [
    "repo_truth_gate",  # truth gate step
    "pytest",  # proof suite
]

# ── Live-start policy: implicit live-start bypass patterns ───────────────────
# These patterns catch attempts to pipe stdin to main.py to bypass the
# --mode live block. The blocker and its test harness are exempted.
LIVE_START_PATTERNS: list[str] = [
    r"I\s+UNDERSTAND.*\|.*python3.*main\.py",
    r"echo.*I\s+UNDERSTAND.*main\.py",
    r"printf.*I\s+UNDERSTAND.*main\.py",
]

# Files that legitimately reference the live-start patterns (blocker + tester)
POLICY_SKIP_FILES: set[str] = {
    "pre_bash_blocker.sh",  # the blocker itself must reference the pattern
    "test_hooks.sh",  # the test harness must prove the block works
}

# ── Risk Constant Audit: Prevents hardcoded throttles outside config.py ───────
# These patterns catch attempts to hardcode risk limits (MAX_POSITIONS, MAX_DEPLOYED)
# in strategy or execution files. They must live in config.py only.
RISK_CONSTANT_PATTERNS: list[tuple[str, str]] = [
    (r"MAX_CONCURRENT_POSITIONS\s*[:=]\s*\d+", "Hardcoded MAX_CONCURRENT_POSITIONS"),
    (r"MAX_DEPLOYED_PCT\s*[:=]\s*0\.\d+", "Hardcoded MAX_DEPLOYED_PCT"),
    (r"MAX_RISK_PER_EVENT_PCT\s*[:=]\s*0\.\d+", "Hardcoded MAX_RISK_PER_EVENT_PCT"),
    (r"KELLY_CAP\s*[:=]\s*0\.\d+", "Hardcoded KELLY_CAP"),
]

# Files allowed to define risk constants (only config.py)
RISK_ALLOW_FILES: set[str] = {"config.py"}


# ── File collection ───────────────────────────────────────────────────────────


def _tracked_files() -> list[Path]:
    """Return all git-tracked files in the repo."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        paths = []
        for line in result.stdout.strip().splitlines():
            if line:
                p = _ROOT / line
                if p.exists():
                    paths.append(p)
        return paths
    except Exception:
        # Fallback: walk the tree
        files = []
        for p in _ROOT.rglob("*"):
            if p.is_file():
                files.append(p)
        return files


def _should_scan(path: Path) -> bool:
    try:
        rel_parts = path.relative_to(_ROOT).parts
    except ValueError:
        return False
    # Skip hidden gemini logs subdir specifically
    if ".gemini" in rel_parts and "logs" in rel_parts:
        return False
    if set(rel_parts) & SKIP_DIRS:
        return False
    if path.name in SKIP_FILES:
        return False
    if path.suffix not in ACTIVE_EXTS:
        return False
    return True


# ── Checks ────────────────────────────────────────────────────────────────────


def check_desktop_paths(files: list[Path]) -> list[str]:
    """Fail if any tracked file contains a Desktop repo path hardcode."""
    failures: list[str] = []
    for path in files:
        if not _should_scan(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pattern, label in BANNED_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                rel = path.relative_to(_ROOT)
                failures.append(f"  {rel}: {label} — '{matches[0]}'")
    return failures


def check_hook_dynamic_root(files: list[Path]) -> list[str]:
    """Fail if any hook script uses a hardcoded absolute REPO_ROOT/REPO var."""
    failures: list[str] = []
    hooks_dir = _ROOT / ".gemini" / "hooks"
    for path in files:
        try:
            in_hooks = hooks_dir in path.parents or path.parent == hooks_dir
        except Exception:
            continue
        if not in_hooks:
            continue
        if path.suffix != ".sh":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Detect hardcoded absolute paths assigned to REPO_ROOT or REPO
        if re.search(r'(?:REPO_ROOT|REPO)\s*=\s*"/Users/', text):
            rel = path.relative_to(_ROOT)
            failures.append(
                f"  {rel}: hardcoded absolute REPO_ROOT/REPO — use dynamic resolution"
            )
    return failures


def check_live_start_policy(files: list[Path]) -> list[str]:
    """Fail if hooks/scripts encode an implicit live-start bypass."""
    failures: list[str] = []
    check_dirs = [_ROOT / ".claude" / "hooks", _ROOT / "scripts"]
    for path in files:
        try:
            in_scope = any(cd in path.parents or path.parent == cd for cd in check_dirs)
        except Exception:
            continue
        if not in_scope:
            continue
        if path.suffix not in (".sh", ".py"):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.name in POLICY_SKIP_FILES:
            continue  # blocker + test harness legitimately reference the pattern
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pattern in LIVE_START_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                rel = path.relative_to(_ROOT)
                failures.append(f"  {rel}: live-start policy bypass pattern detected")
                break
    return failures


def check_risk_constants(files: list[Path]) -> list[str]:
    """Fail if any file other than config.py defines risk-limit constants."""
    failures: list[str] = []
    for path in files:
        if not _should_scan(path):
            continue
        if path.name in RISK_ALLOW_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pattern, label in RISK_CONSTANT_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                rel = path.relative_to(_ROOT)
                failures.append(f"  {rel}: {label} — must be moved to config.py")
    return failures


def check_ci_config() -> list[str]:
    """Fail if CI config is missing required steps."""
    failures: list[str] = []
    if not CI_FILE.exists():
        rel = CI_FILE.relative_to(_ROOT) if CI_FILE.is_relative_to(_ROOT) else CI_FILE
        return [f"  {rel}: CI config not found"]
    text = CI_FILE.read_text(encoding="utf-8", errors="replace")
    for token in CI_REQUIRED_TOKENS:
        if token not in text:
            failures.append(
                f"  .github/workflows/ci.yml: missing required token '{token}'"
            )
    return failures


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Repo truth gate")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Fast mode: Desktop path + hook root + policy checks (for pre-commit)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode: fast + CI config check (for pre-push/CI)",
    )
    args = parser.parse_args()

    # Default to fast if neither flag given
    if not args.fast and not args.strict:
        args.fast = True

    print()
    print("━" * 56)
    print("  REPO TRUTH GATE")
    mode = "strict" if args.strict else "fast"
    print(f"  mode={mode} | root={_ROOT}")
    print("━" * 56)

    files = _tracked_files()
    all_failures: list[str] = []

    # ── Desktop path scan ──────────────────────────────────────────────────
    print("\n── Desktop path scan ─────────────────────────────────")
    desktop_fails = check_desktop_paths(files)
    if desktop_fails:
        print(f"  FAIL: {len(desktop_fails)} Desktop path reference(s):")
        for f in desktop_fails:
            print(f)
        all_failures.extend(desktop_fails)
    else:
        print("  PASS: No Desktop path references in tracked files")

    # ── Hook dynamic root ──────────────────────────────────────────────────
    print("\n── Hook dynamic root ─────────────────────────────────")
    hook_fails = check_hook_dynamic_root(files)
    if hook_fails:
        print(f"  FAIL: {len(hook_fails)} hook(s) with hardcoded root:")
        for f in hook_fails:
            print(f)
        all_failures.extend(hook_fails)
    else:
        print("  PASS: All hooks use dynamic root resolution")

    # ── Live-start policy ──────────────────────────────────────────────────
    print("\n── Live-start policy ─────────────────────────────────")
    policy_fails = check_live_start_policy(files)
    if policy_fails:
        print(f"  FAIL: {len(policy_fails)} live-start policy bypass(es):")
        for f in policy_fails:
            print(f)
        all_failures.extend(policy_fails)
    else:
        print("  PASS: No live-start policy bypasses in hooks/scripts")

    # ── Risk constant audit ────────────────────────────────────────────────
    print("\n── Risk constant audit ───────────────────────────────")
    risk_fails = check_risk_constants(files)
    if risk_fails:
        print(f"  FAIL: {len(risk_fails)} risk-limit hardcode(s) found:")
        for f in risk_fails:
            print(f)
        all_failures.extend(risk_fails)
    else:
        print("  PASS: All risk limits correctly mapped to config.py")

    # ── Strict-only: CI config ─────────────────────────────────────────────
    if args.strict:
        print("\n── CI config ─────────────────────────────────────────")
        ci_fails = check_ci_config()
        if ci_fails:
            print(f"  FAIL: {len(ci_fails)} CI config issue(s):")
            for f in ci_fails:
                print(f)
            all_failures.extend(ci_fails)
        else:
            print("  PASS: CI config has all required steps")

    # ── Verdict ────────────────────────────────────────────────────────────
    print()
    print("━" * 56)
    if all_failures:
        print(f"  FAIL: {len(all_failures)} issue(s) — fix before proceeding")
        print("━" * 56)
        print()
        return 1
    else:
        print(f"  PASS: All checks passed ({mode} mode)")
        print("━" * 56)
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
