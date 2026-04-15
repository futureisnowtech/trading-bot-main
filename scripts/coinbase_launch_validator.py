#!/usr/bin/env python3
"""
scripts/coinbase_launch_validator.py — Coinbase crypto lane launch readiness checker.

Runs a series of automated checks and emits:
  READY       — check passed
  BLOCKED     — cannot proceed without code/config change
  ACTION NEEDED — human-only step required (no code can do this)

Exit code: 0 if all checks pass or only ACTION NEEDED remain, 1 if BLOCKED.

Usage:
    python3 scripts/coinbase_launch_validator.py
    python3 scripts/coinbase_launch_validator.py --strict   # exit 1 on ACTION NEEDED too
"""

from __future__ import annotations

import os
import sys
import argparse
import importlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_BLOCKED: list[str] = []
_ACTION: list[str] = []
_READY: list[str] = []


def _ready(msg: str) -> None:
    print(f"  READY          {msg}")
    _READY.append(msg)


def _blocked(msg: str) -> None:
    print(f"  BLOCKED        {msg}")
    _BLOCKED.append(msg)


def _action(msg: str) -> None:
    print(f"  ACTION NEEDED  {msg}")
    _ACTION.append(msg)


# ── 1. Python deps ────────────────────────────────────────────────────────────
print("\n[1] Python dependencies")
for pkg, import_name in [
    ("PyJWT", "jwt"),
    ("cryptography", "cryptography"),
    ("requests", "requests"),
]:
    try:
        importlib.import_module(import_name)
        _ready(f"{pkg} importable")
    except ImportError:
        _blocked(f"{pkg} missing — run: pip install {pkg.lower()}")


# ── 2. Coinbase broker module loads ──────────────────────────────────────────
print("\n[2] Coinbase broker module")
try:
    from execution.coinbase_broker import (
        CoinbaseBroker,
        get_coinbase_broker,
        PRODUCT_SPECS,
        COINBASE_TAKER_FEE,
        SUPPORTED_SYMBOLS,
        CoinbaseSymbolError,
    )

    _ready("execution/coinbase_broker.py imports cleanly")
    _ready(f"SUPPORTED_SYMBOLS = {sorted(SUPPORTED_SYMBOLS)}")
    _ready(
        f"COINBASE_TAKER_FEE = {COINBASE_TAKER_FEE} ({COINBASE_TAKER_FEE * 100:.3f}%)"
    )
except Exception as e:
    _blocked(f"coinbase_broker import failed: {e}")


# ── 3. Symbol mapping correctness ────────────────────────────────────────────
print("\n[3] Symbol → Coinbase product mapping")
try:
    expected = {
        "BTC": "BIP-20DEC30-CDE",
        "ETH": "ETP-20DEC30-CDE",
        "SOL": "SLP-20DEC30-CDE",
        "XRP": "XPP-20DEC30-CDE",
    }
    for sym, pid in expected.items():
        spec = PRODUCT_SPECS.get(sym)
        if spec and spec["product_id"] == pid:
            _ready(f"{sym} → {pid}  contract_size={spec['contract_size']}")
        else:
            _blocked(f"{sym} mapping wrong or missing: got {spec}")

    # Verify fail-closed on unsupported symbol
    try:
        broker = CoinbaseBroker(paper=True)
        broker._resolve_symbol("DOGE")
        _blocked(
            "Unsupported symbol DOGE did NOT raise CoinbaseSymbolError — fail-open!"
        )
    except CoinbaseSymbolError:
        _ready(
            "Unsupported symbol DOGE correctly raises CoinbaseSymbolError (fail-closed)"
        )
    except Exception as e:
        _blocked(f"Unexpected error on unsupported symbol test: {e}")
except Exception as e:
    _blocked(f"Symbol mapping check failed: {e}")


# ── 4. CDP credential presence ────────────────────────────────────────────────
print("\n[4] Coinbase CDP credentials")
key_name = os.getenv("COINBASE_CDP_KEY_NAME", "")
private_key = os.getenv("COINBASE_CDP_PRIVATE_KEY", "")

if not key_name:
    _action(
        "COINBASE_CDP_KEY_NAME not set in .env — "
        "create a CDP API key at https://portal.cdp.coinbase.com and add to .env"
    )
else:
    if key_name.startswith("organizations/") and "/apiKeys/" in key_name:
        _ready(f"COINBASE_CDP_KEY_NAME present and has correct format")
    else:
        _blocked(
            f"COINBASE_CDP_KEY_NAME format wrong — expected "
            f"'organizations/{{org_id}}/apiKeys/{{key_id}}', got: {key_name[:40]}..."
        )

if not private_key:
    _action(
        "COINBASE_CDP_PRIVATE_KEY not set in .env — "
        "download EC PEM from CDP portal and add to .env (\\n-escaped)"
    )
else:
    pem_ok = "BEGIN EC PRIVATE KEY" in private_key or "BEGIN PRIVATE KEY" in private_key
    if pem_ok:
        _ready("COINBASE_CDP_PRIVATE_KEY present and looks like EC PEM")
    else:
        _blocked(
            "COINBASE_CDP_PRIVATE_KEY does not look like an EC PEM — "
            "check for correct format and \\n escaping in .env"
        )


# ── 5. Paper mode broker init (no live API calls) ────────────────────────────
print("\n[5] Paper mode broker initialization")
try:
    b = CoinbaseBroker(paper=True)
    _ready("CoinbaseBroker(paper=True) initializes without error")
    # Exercise a paper long
    result = b.open_long(symbol="BTC", size_usd=100.0, leverage=3)
    if result and result.get("paper"):
        _ready(
            f"Paper open_long BTC succeeded: orderId={result.get('orderId', '?')[:20]}"
        )
    else:
        _blocked(f"Paper open_long returned unexpected result: {result}")
    # Exercise close
    close = b.close_position(
        "BTC",
        pos_fallback={
            "direction": "LONG",
            "qty": 0.001,
            "entry_price": 90000.0,
            "symbol": "BTC",
        },
    )
    if close and "pnl_usd" in close:
        _ready(f"Paper close_position BTC succeeded")
    else:
        _blocked(f"Paper close_position returned unexpected result: {close}")
except Exception as e:
    _blocked(f"Paper mode broker init/exercise failed: {e}")


# ── 6. Live JWT generation (only if creds present) ───────────────────────────
print("\n[6] CDP JWT generation (live creds check)")
if key_name and private_key:
    try:
        b_live = CoinbaseBroker(paper=False)
        tok = b_live._make_jwt("GET", "/api/v3/brokerage/accounts")
        if tok and len(tok) > 20:
            _ready("CDP JWT generated successfully (ES256)")
        else:
            _blocked(f"CDP JWT generation returned short/empty token")
    except Exception as e:
        _blocked(f"CDP JWT generation failed: {e}")
else:
    _action("Skipping live JWT test — credentials not present (see check 4)")


# ── 7. perps_engine wired to Coinbase ────────────────────────────────────────
print("\n[7] perps_engine → coinbase_broker wiring")
try:
    import perps_engine

    src = open(os.path.join(ROOT, "perps_engine.py")).read()
    if "coinbase_broker" in src and "get_coinbase_broker" in src:
        _ready("perps_engine.py imports from coinbase_broker")
    else:
        _blocked("perps_engine.py still references binance_broker — rewire needed")

    if "0.0003" in src and "0.00065" not in src:
        _ready("perps_engine.py uses Coinbase 0.03% taker fee (Binance fee removed)")
    elif "0.00065" in src:
        _blocked("perps_engine.py still contains Binance/Kraken 0.00065 fee constant")
    else:
        _ready("perps_engine.py fee constants look correct")

    if "coinbase_paper" in src and "kraken_paper" not in src:
        _ready(
            "perps_engine.py uses 'coinbase_paper' broker string (Kraken string removed)"
        )
    elif "kraken_paper" in src:
        _blocked("perps_engine.py still logs broker='kraken_paper'")
except Exception as e:
    _blocked(f"perps_engine wiring check failed: {e}")


# ── 8. Economics gate fee model ───────────────────────────────────────────────
print("\n[8] Economics gate fee model")
try:
    from risk.economics_gate import TAKER_FEE_PCT, ROUND_TRIP_COST

    if abs(TAKER_FEE_PCT - 0.0003) < 1e-8:
        _ready(f"economics_gate TAKER_FEE_PCT = {TAKER_FEE_PCT * 100:.3f}% (Coinbase)")
    else:
        _blocked(
            f"economics_gate TAKER_FEE_PCT = {TAKER_FEE_PCT * 100:.4f}% — "
            f"expected 0.030% for Coinbase"
        )
    expected_rt = 0.0003 * 2
    if abs(ROUND_TRIP_COST - expected_rt) < 1e-8:
        _ready(f"ROUND_TRIP_COST = {ROUND_TRIP_COST * 100:.4f}% (0.060% round-trip)")
    else:
        _blocked(f"ROUND_TRIP_COST = {ROUND_TRIP_COST * 100:.4f}% — expected 0.060%")
except Exception as e:
    _blocked(f"economics_gate check failed: {e}")


# ── 9. Config CDP keys exported ───────────────────────────────────────────────
print("\n[9] config.py CDP key exports")
try:
    import config as _cfg

    if hasattr(_cfg, "COINBASE_CDP_KEY_NAME") and hasattr(
        _cfg, "COINBASE_CDP_PRIVATE_KEY"
    ):
        _ready("config.py exports COINBASE_CDP_KEY_NAME and COINBASE_CDP_PRIVATE_KEY")
    else:
        _blocked("config.py missing COINBASE_CDP_KEY_NAME or COINBASE_CDP_PRIVATE_KEY")
    if (
        hasattr(_cfg, "COINBASE_TAKER_FEE_PCT")
        and abs(_cfg.COINBASE_TAKER_FEE_PCT - 0.0003) < 1e-8
    ):
        _ready(
            f"config.py COINBASE_TAKER_FEE_PCT = {_cfg.COINBASE_TAKER_FEE_PCT * 100:.3f}%"
        )
    else:
        _blocked(f"config.py COINBASE_TAKER_FEE_PCT wrong or missing")
except Exception as e:
    _blocked(f"config.py check failed: {e}")


# ── 10. No stale Binance-only launch assumption in readiness critical paths ───
print("\n[10] Stale Binance references in critical files")
critical_files = {
    "perps_engine.py": ["binance_broker", "kraken_paper", "0.00065"],
    "risk/economics_gate.py": ["Kraken Futures standard"],
}
for fname, bad_strings in critical_files.items():
    fpath = os.path.join(ROOT, fname)
    try:
        content = open(fpath).read()
        found = [s for s in bad_strings if s in content]
        if found:
            _blocked(f"{fname} still contains stale strings: {found}")
        else:
            _ready(f"{fname} — no stale Binance/Kraken execution strings")
    except FileNotFoundError:
        _blocked(f"{fname} not found")


# ── 11. Proof test suite ──────────────────────────────────────────────────────
print("\n[11] Proof test suite")
import subprocess

result = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "tests/proof/test_coinbase_broker.py",
        "-q",
        "--tb=short",
    ],
    capture_output=True,
    text=True,
    cwd=ROOT,
)
if result.returncode == 0:
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    summary = lines[-1] if lines else "passed"
    _ready(f"Coinbase broker proof tests: {summary}")
else:
    out = (result.stdout + result.stderr).strip()[-400:]
    _blocked(f"Coinbase broker proof tests FAILED:\n{out}")


# ── 12. Exchange approval / account state (human-only) ───────────────────────
print("\n[12] Exchange approval and account state (human-only checks)")
_action(
    "Verify Coinbase derivatives trading is approved on your account at "
    "https://www.coinbase.com/advanced-trade"
)
_action(
    "Fund your Coinbase derivatives buying power (USD) sufficient for at least "
    "1 BIP contract (~$900 notional at BTC=$90K)"
)
_action(
    "Create a fresh Coinbase CDP API key at https://portal.cdp.coinbase.com "
    "with 'trade' scope and place COINBASE_CDP_KEY_NAME + COINBASE_CDP_PRIVATE_KEY in .env"
)


# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print(f"LAUNCH READINESS SUMMARY")
print(f"  READY:         {len(_READY)}")
print(f"  BLOCKED:       {len(_BLOCKED)}")
print(f"  ACTION NEEDED: {len(_ACTION)}")
print("=" * 70)

if _BLOCKED:
    print("\nBLOCKED (must fix before launch):")
    for b in _BLOCKED:
        print(f"  • {b}")

if _ACTION:
    print("\nACTION NEEDED (human-only steps):")
    for a in _ACTION:
        print(f"  • {a}")

if not _BLOCKED and not _ACTION:
    print("\nSTATUS: READY TO LAUNCH (paper mode)")
    print("  All automated checks passed.")
    print("  To launch live: ensure ACTION NEEDED steps above are complete,")
    print("  then run: python3 scripts/go_live.py")
elif not _BLOCKED:
    print(
        f"\nSTATUS: CODE READY — {len(_ACTION)} human action(s) remaining before live launch"
    )
else:
    print(
        f"\nSTATUS: NOT READY — {len(_BLOCKED)} code/config issue(s) must be resolved"
    )

parser = argparse.ArgumentParser()
parser.add_argument("--strict", action="store_true", help="Exit 1 if any ACTION NEEDED")
args, _ = parser.parse_known_args()

sys.exit(1 if _BLOCKED or (args.strict and _ACTION) else 0)
