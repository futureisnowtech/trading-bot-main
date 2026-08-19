#!/usr/bin/env python3
"""Boundary-contract audit.

Every production bug this repo has produced recently was the same species: a
string-keyed contract that crosses a boundary with nothing verifying the other
side. Green unit tests cannot see them, because both sides of the boundary are
mocked. Examples, all real:

  * "good_till_cancelled" vs Kalshi's "good_till_canceled"  (one L)
  * MAKER_ENTRY_TIMEOUT_SECONDS vs MAKER_ENTRY_TIMEOUT_S
  * DELETE /portfolio/orders/{id} (v1, gone) vs /portfolio/events/orders/{id}
  * positions[].position vs positions[].position_fp
  * reason="take_profit" matched against a broker that only ever sees
    "salvage_exit"

The boundaries are: code<->config, code<->workflow env, code<->database,
module<->module vocabularies, and code<->exchange API.

Run with --strict to exit non-zero on any finding.
"""
from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import re
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", "research_package"}


def py_files() -> list[pathlib.Path]:
    return [
        p for p in ROOT.rglob("*.py")
        if not any(part in SKIP_DIRS for part in p.parts)
    ]


def rel(p: pathlib.Path) -> str:
    return str(p.relative_to(ROOT))


class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def add(self, check: str, msg: str) -> None:
        self.items.append((check, msg))

    def section(self, title: str, msgs: list[str], explain: str) -> None:
        print(f"\n\033[1m{title}\033[0m")
        if not msgs:
            print("  PASS")
            return
        for m in msgs:
            print(f"  FAIL  {m}")
        print(f"  -> {explain}")
        for m in msgs:
            self.add(title, m)


# ---------------------------------------------------------------- config


# Names deliberately absent from config.py. A test asserts FUTURES_LANE_ACTIVE
# stays undefined, because its presence would mean a non-weather lane is back.
INTENTIONALLY_ABSENT = {"FUTURES_LANE_ACTIVE"}


def check_config_refs(f: Findings) -> None:
    """config.NAME / getattr(config, "NAME") that config.py never defines.

    These do not raise. getattr(..., default) silently returns the default
    forever, so the setting looks configurable and is not.
    """
    cfg_src = (ROOT / "config.py").read_text()
    tree = ast.parse(cfg_src)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.Assign):
            defined.update(t.id for t in node.targets if isinstance(t, ast.Name))

    msgs = []
    for p in py_files():
        if p.name in {"config.py", "contract_audit.py"}:
            continue
        txt = p.read_text(errors="ignore")
        names = set(re.findall(r'getattr\(\s*config\s*,\s*["\']([A-Za-z0-9_]+)["\']', txt))
        names |= set(re.findall(r'\bconfig\.([A-Z][A-Z0-9_]{2,})\b', txt))
        for name in sorted(names - defined - INTENTIONALLY_ABSENT):
            msgs.append(f"config.{name} referenced in {rel(p)} but never defined in config.py")
    f.section(
        "CHECK 1  config attributes referenced but never defined",
        msgs,
        "getattr(config, X, default) silently uses the default forever; the knob looks live and is dead.",
    )


def _all_getenv_keys() -> set[str]:
    keys: set[str] = set()
    for p in py_files():
        keys |= set(re.findall(r'os\.getenv\(\s*["\']([A-Z0-9_]+)["\']', p.read_text(errors="ignore")))
    return keys


def _workflow_env_blocks() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    wf_dir = ROOT / ".github" / "workflows"
    if not wf_dir.exists():
        return out
    for wf in sorted(wf_dir.glob("*.yml")):
        block = re.search(r"cat > \.env << 'EOF'(.*?)\n\s*EOF", wf.read_text(), re.S)
        if not block:
            continue
        env: dict[str, str] = {}
        for line in block.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
        out[wf.name] = env
    return out


def check_orphan_env_pins(f: Findings) -> None:
    """Env keys pinned in CI/deploy that no os.getenv anywhere reads."""
    known = _all_getenv_keys()
    msgs = []
    for wf, env in _workflow_env_blocks().items():
        for key in sorted(env):
            if key not in known:
                msgs.append(f"{wf} pins {key}, but no os.getenv in the repo reads it")
    f.section(
        "CHECK 2  workflow env pins nothing reads",
        msgs,
        "A pinned key nobody reads proves nothing and hides a rename (MAKER_ENTRY_TIMEOUT_SECONDS).",
    )


def check_workflow_env_parity(f: Findings) -> None:
    """ci.yml and deploy-nyc.yml must pin an identical risk posture."""
    blocks = _workflow_env_blocks()
    ci, dep = blocks.get("ci.yml"), blocks.get("deploy-nyc.yml")
    msgs = []
    if ci and dep:
        for key in sorted(set(ci) | set(dep)):
            a, b = ci.get(key), dep.get(key)
            if a != b:
                msgs.append(f"{key}: ci.yml={a!r} deploy-nyc.yml={b!r}")
    f.section(
        "CHECK 3  ci.yml vs deploy-nyc.yml env parity",
        msgs,
        "The protected deploy must prove the same posture the PR check proved.",
    )


# ---------------------------------------------------------------- database


def _live_schema() -> dict[str, set[str]]:
    """Build the real schema by initialising a throwaway DB."""
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory() as td:
        db = str(pathlib.Path(td) / "schema.db")
        from forecast.db import init_forecast_db

        init_forecast_db(db)
        con = sqlite3.connect(db)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: {d[1] for d in con.execute(f"pragma table_info({t})")} for t in tables}


def check_sql_columns(f: Findings, schema: dict[str, set[str]]) -> None:
    """Columns named in INSERT statements that the table does not have."""
    all_cols = set().union(*schema.values()) if schema else set()
    msgs = []
    for p in py_files():
        txt = p.read_text(errors="ignore")
        for m in re.finditer(
            r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s*\(([^)]*)\)", txt, re.I | re.S
        ):
            table, cols_raw = m.group(1), m.group(2)
            if table not in schema:
                continue
            cols = [c.strip() for c in cols_raw.replace("\n", " ").split(",") if c.strip()]
            for c in cols:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c):
                    continue
                if c not in schema[table]:
                    msgs.append(f"{rel(p)}: INSERT INTO {table} names column '{c}' which does not exist")
    f.section(
        "CHECK 4  SQL columns that do not exist in the schema",
        msgs,
        "A renamed column fails at runtime inside a try/except and looks like 'no data'.",
    )
    _ = all_cols


def check_tables_without_writers(f: Findings, schema: dict[str, set[str]]) -> None:
    """Tables with a schema that no code ever writes to."""
    blob = "\n".join(p.read_text(errors="ignore") for p in py_files())
    written = set(re.findall(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)", blob, re.I))
    written |= set(re.findall(r"UPDATE\s+(\w+)\s+SET", blob, re.I))
    written |= set(re.findall(r"REPLACE\s+INTO\s+(\w+)", blob, re.I))
    ignore = {"sqlite_sequence"}
    msgs = [
        f"table '{t}' has a schema but no INSERT/UPDATE anywhere in the repo"
        for t in sorted(set(schema) - written - ignore)
    ]
    f.section(
        "CHECK 5  tables with a schema and no writer",
        msgs,
        "A dead table is a feedback loop that silently never closed (q_hat, candidate_outcomes).",
    )


# ---------------------------------------------------------------- vocabularies


VOCAB_KWARGS = ("reason", "exit_type", "strategy")


def _resolve_literal_sets() -> dict[str, set[str]]:
    """Module constants that hold a vocabulary, e.g. frozenset({"take_profit"})."""
    out: dict[str, set[str]] = {}
    for p in py_files():
        for m in re.finditer(
            r"^([A-Z][A-Z0-9_]*)\s*(?::[^=\n]*)?=\s*(?:frozenset|set)\(\{([^}]*)\}\)",
            p.read_text(errors="ignore"), re.M,
        ):
            out[m.group(1)] = set(re.findall(r'["\']([a-z0-9_]{3,})["\']', m.group(2)))
    return out


def check_vocabulary_reachability(f: Findings) -> None:
    """A function gates on reason literals its own callers never pass.

    This is exactly the take_profit bug and the reason the naive version misses
    it: "take_profit" IS emitted elsewhere (exit_type=...), just never on the
    boundary that gates on it. So the question is not "does this literal exist"
    but "does any CALLER OF THIS FUNCTION pass it".
    """
    sets = _resolve_literal_sets()
    gates: dict[str, tuple[set[str], str]] = {}

    for p in py_files():
        txt = p.read_text(errors="ignore")
        for fn in re.finditer(r"\n    def (\w+)\(", txt):
            name = fn.group(1)
            body = txt[fn.end(): fn.end() + 4000]
            if 'kwargs.get("reason"' not in body and "exit_reason" not in body:
                continue
            lits: set[str] = set()
            for cm in re.finditer(r"in\s+config\.([A-Z][A-Z0-9_]*)", body):
                lits |= sets.get(cm.group(1), set())
            for cm in re.finditer(r'exit_reason\s*==\s*["\']([a-z0-9_]+)["\']', body):
                lits.add(cm.group(1))
            if lits:
                gates[name] = (lits, rel(p))

    msgs = []
    for fname, (lits, where) in sorted(gates.items()):
        passed: set[str] = set()
        callers: set[str] = set()
        for p in py_files():
            if rel(p).startswith("tests"):
                continue
            txt = p.read_text(errors="ignore")
            for cm in re.finditer(re.escape(fname) + r"\((.{0,600}?)\)", txt, re.S):
                seg = cm.group(1)
                found = re.findall(r'reason\s*=\s*["\']([a-z0-9_]+)["\']', seg)
                if found:
                    passed.update(found)
                    callers.add(rel(p))
        if callers and not (lits & passed):
            msgs.append(
                f"{where}:{fname}() gates on {sorted(lits)} but its callers only ever "
                f"pass {sorted(passed)} -- the branch is unreachable"
            )
    f.section(
        "CHECK 6  gate literals no caller of that function ever passes",
        msgs,
        "An unreachable branch that looks implemented (the take_profit/salvage_exit bug).",
    )


# ---------------------------------------------------------------- exchange API


DEPRECATED = [
    (r'_request\(\s*["\'](?:DELETE|POST|PUT|PATCH)["\']\s*,\s*f?["\'][^"\']*?/portfolio/orders/',
     "mutation on the v1 /portfolio/orders route (410 deprecated_v1_order_endpoint); "
     "use /portfolio/events/orders/"),
]
KALSHI_ENUMS = {
    "time_in_force": {"fill_or_kill", "good_till_canceled", "immediate_or_cancel"},
    "self_trade_prevention_type": {"taker_at_cross", "maker"},
}


def check_exchange_contract(f: Findings) -> None:
    msgs = []
    for p in py_files():
        txt = p.read_text(errors="ignore")
        for pattern, why in DEPRECATED:
            for _ in re.finditer(pattern, txt):
                msgs.append(f"{rel(p)}: {why}")
        for field, allowed in KALSHI_ENUMS.items():
            for m in re.finditer(field + r'["\']?\s*:\s*(?:[^,\n]*?)["\']([a-z_]+)["\']', txt):
                val = m.group(1)
                if val not in allowed and val not in {"true", "false"}:
                    msgs.append(
                        f"{rel(p)}: {field}={val!r} is not a documented Kalshi value {sorted(allowed)}"
                    )
    f.section(
        "CHECK 7  Kalshi API contract (routes and enums)",
        msgs,
        "The exchange rejects these at runtime; mocked tests cannot see it.",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 on any finding")
    args = ap.parse_args()

    print("=" * 72)
    print("  BOUNDARY CONTRACT AUDIT")
    print("  string-keyed contracts crossing a boundary with no verifier")
    print("=" * 72)

    f = Findings()
    check_config_refs(f)
    check_orphan_env_pins(f)
    check_workflow_env_parity(f)
    try:
        schema = _live_schema()
        check_sql_columns(f, schema)
        check_tables_without_writers(f, schema)
    except Exception as exc:  # pragma: no cover
        print(f"\n  (schema checks skipped: {exc})")
    check_vocabulary_reachability(f)
    check_exchange_contract(f)

    print("\n" + "=" * 72)
    if f.items:
        print(f"  {len(f.items)} FINDING(S)")
    else:
        print("  CLEAN")
    print("=" * 72)
    return 1 if (args.strict and f.items) else 0


if __name__ == "__main__":
    raise SystemExit(main())
