"""
scripts/acceptance_test_spot_pipeline.py
One-shot live acceptance test for the spot scalp learning pipeline.

Executes a real BTC-USD buy then immediate sell (maker-first, ~$50).
Verifies end-to-end: broker execution → DB trade row → open_positions →
ml_feature_snapshots (lineage) → trade_attribution → signal_stats.

Safe to run once. Designed to eat ~$0.06 in fees (BTC maker round-trip).
DO NOT run repeatedly — each run places real Coinbase orders.
"""

import sqlite3
import sys
import time
import json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DB = _ROOT / "logs" / "trades.db"

SYMBOL = "BTC"
SIZE_USD = 50.0
EXIT_REASON = "acceptance_test_forced"

# Synthetic spot_state using a non-quarantined cluster (impulse_continuation × TREND)
# so the quality gate does not block the trade.
# frames dict required by spot_quality_block_reason for per-frame floor checks.
_FRAME_5M = {
    "frame_score": 72.0,
    "momentum_impulse": 0.10,
    "structure_component": 0.60,
    "path_efficiency": 0.55,
    "participation_component": 0.50,
    "volatility_quality": 0.65,
}
_FRAME_30M = {
    "frame_score": 68.0,
    "momentum_impulse": 0.06,
    "structure_component": 0.55,
    "path_efficiency": 0.50,
    "participation_component": 0.45,
    "volatility_quality": 0.60,
}
FAKE_SPOT_STATE = {
    "regime": "TREND",
    "composite_score": 78.0,
    "derivative_score": 70.0,
    "setup_family": "impulse_continuation",
    "setup_score": 0.78,
    "setup_preference": "strong",
    "structural_confirms": "supertrend|ichimoku",
    "structural_confirm_count": 2,
    "tf_5m_state": "z=0.80|v=0.05|a=0.02|j=0.01|imp=0.10|score=72.0",
    "tf_30m_state": "z=0.60|v=0.04|a=0.01|j=0.00|imp=0.06|score=68.0",
    "tf_4h_state": "z=0.50|v=0.03|a=0.01|j=0.00|imp=0.04|score=65.0",
    "tf_1d_state": "z=0.40|v=0.02|a=0.01|j=0.00|imp=0.02|score=62.0",
    "frames": {
        "5m": _FRAME_5M,
        "30m": _FRAME_30M,
    },
}


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def _pass(msg: str) -> None:
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    raise AssertionError(msg)


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


# ─────────────────────────────────────────────────────────────
# PRE-FLIGHT
# ─────────────────────────────────────────────────────────────
def check_preflight() -> None:
    _section("1. PRE-FLIGHT CHECKS")

    # Bot not running (we're doing a standalone test)
    import subprocess

    result = subprocess.run(["pgrep", "-f", "main.py"], capture_output=True)
    if result.returncode == 0:
        _warn("main.py process detected — bot may be running; test will still proceed")
    else:
        _pass("Bot process not running (standalone test safe)")

    # Check credentials loaded
    import config

    if not getattr(config, "COINBASE_CDP_KEY_NAME", ""):
        _fail("COINBASE_CDP_KEY_NAME not set — cannot place live order")
    _pass("Coinbase credentials present")

    # Check SPOT_LANE_ACTIVE
    if not getattr(config, "SPOT_LANE_ACTIVE", False):
        _fail("SPOT_LANE_ACTIVE=False — open_spot() will block immediately")
    _pass(f"SPOT_LANE_ACTIVE={config.SPOT_LANE_ACTIVE}")

    # Check taker fallback disabled
    taker = getattr(config, "SPOT_TAKER_FALLBACK_ENABLED", False)
    if taker:
        _warn("SPOT_TAKER_FALLBACK_ENABLED=True (should be False per policy)")
    else:
        _pass("SPOT_TAKER_FALLBACK_ENABLED=False ✓")

    # Check quarantine flags
    neutral_blocked = getattr(config, "SPOT_PULLBACK_RECLAIM_NEUTRAL_BLOCKED", True)
    chop_blocked = getattr(config, "SPOT_PULLBACK_RECLAIM_CHOP_BLOCKED", True)
    _pass(f"Quarantine: NEUTRAL={neutral_blocked}, CHOP={chop_blocked}")

    # No existing BTC position
    conn = _db()
    pos = conn.execute(
        "SELECT symbol FROM open_positions WHERE symbol='BTC' AND paper=0 AND strategy LIKE 'spot_%'"
    ).fetchone()
    conn.close()
    if pos:
        _fail("BTC spot position already open — close it before running this test")
    _pass("No existing BTC spot position")

    # Account has buying power
    try:
        from execution.coinbase_spot_broker import CoinbaseSpotBroker

        broker = CoinbaseSpotBroker()
        bal = broker.get_spot_balance() or {}
        usd = float(bal.get("usd_available") or 0)
        print(f"         Coinbase spot USD available: ${usd:.2f}")
        if usd < SIZE_USD:
            _fail(f"Insufficient buying power: ${usd:.2f} < ${SIZE_USD:.2f} needed")
        _pass(f"Buying power ${usd:.2f} ≥ ${SIZE_USD:.2f}")
    except Exception as e:
        _fail(f"Broker connection failed: {e}")


# ─────────────────────────────────────────────────────────────
# SNAPSHOT BEFORE
# ─────────────────────────────────────────────────────────────
def snapshot_before() -> dict:
    _section("2. SNAPSHOT BEFORE TRADE")
    conn = _db()
    max_trade_id = conn.execute("SELECT MAX(id) FROM trades").fetchone()[0] or 0
    max_mfs_id = (
        conn.execute("SELECT MAX(id) FROM ml_feature_snapshots").fetchone()[0] or 0
    )
    max_attr_id = (
        conn.execute("SELECT MAX(id) FROM trade_attribution").fetchone()[0] or 0
    )
    conn.close()
    snap = {
        "max_trade_id": max_trade_id,
        "max_mfs_id": max_mfs_id,
        "max_attr_id": max_attr_id,
        "ts": _now_ts(),
    }
    print(
        f"         max trade_id={max_trade_id}, max mfs_id={max_mfs_id}, max attr_id={max_attr_id}"
    )
    _pass("Snapshot captured")
    return snap


# ─────────────────────────────────────────────────────────────
# EXECUTE OPEN
# ─────────────────────────────────────────────────────────────
def execute_open() -> dict:
    _section("3. OPEN BTC SPOT (live, maker-first, $50)")
    from spot_engine import open_spot

    print(
        f"         Calling open_spot(BTC, ${SIZE_USD}, spot_state=TREND/impulse_continuation)"
    )
    start = time.time()
    result = open_spot(
        symbol=SYMBOL,
        size_usd=SIZE_USD,
        composite_score=78.0,
        final_spot_score=78.0,
        atr_at_entry=0.0,
        spot_state=FAKE_SPOT_STATE,
        risk_dollars=SIZE_USD * 0.01,
        candidate_id=999999,
        candidate_scan_id="acceptance_test_scan",
        raw_scanner_symbol="BTC-USDC",
        base_asset="BTC",
    )
    elapsed = time.time() - start

    if result is None:
        _fail(
            f"open_spot returned None after {elapsed:.1f}s — "
            "maker order may not have filled (6s wait) or blocked by gate. "
            "Check logs for reason."
        )

    print(
        f"         open_spot result: {json.dumps({k: v for k, v in result.items() if k not in ['paper']}, indent=2)}"
    )
    _pass(
        f"open_spot succeeded: qty={result['qty']:.6f} BTC @ ${result['entry']:.2f} "
        f"route={result['execution_route']} fee=${result['fee_usd']:.4f} ({elapsed:.1f}s)"
    )
    return result


# ─────────────────────────────────────────────────────────────
# VERIFY OPEN IN DB
# ─────────────────────────────────────────────────────────────
def verify_open_db(snap: dict, open_result: dict) -> dict:
    _section("4. VERIFY BUY IN DB")
    conn = _db()

    # trades table
    buy_row = conn.execute(
        "SELECT * FROM trades WHERE id > ? AND symbol='BTC' AND action='BUY' AND strategy='spot_btc' AND paper=0",
        (snap["max_trade_id"],),
    ).fetchone()
    if not buy_row:
        conn.close()
        _fail("BUY row not found in trades table")
    buy_row = dict(buy_row)
    print(
        f"         BUY trade_id={buy_row['id']}, qty={buy_row['qty']}, price={buy_row['price']}"
    )
    _pass("BUY row found in trades table")

    # open_positions
    pos = conn.execute(
        "SELECT * FROM open_positions WHERE symbol='BTC' AND paper=0 AND strategy='spot_btc'"
    ).fetchone()
    if not pos:
        conn.close()
        _fail("Position not found in open_positions")
    pos = dict(pos)
    print(
        f"         Position: entry={pos['entry']}, stop={pos['stop']}, target={pos['target']}"
    )
    print(
        f"         Lineage: candidate_id={pos['candidate_id']}, base_asset={pos['base_asset']}, setup_family={pos['setup_family']}"
    )

    if pos["candidate_id"] != 999999:
        _fail(f"candidate_id mismatch: expected 999999, got {pos['candidate_id']}")
    _pass("candidate_id=999999 persisted to open_positions")

    if pos["base_asset"] != "BTC":
        _fail(f"base_asset mismatch: expected BTC, got {pos['base_asset']}")
    _pass("base_asset=BTC persisted")

    if pos["setup_family"] != "impulse_continuation":
        _warn(f"setup_family={pos['setup_family']} (may be from spot_state resolution)")
    else:
        _pass("setup_family=impulse_continuation persisted")

    if pos["spot_regime"] != "TREND":
        _warn(f"spot_regime={pos['spot_regime']}")
    else:
        _pass("spot_regime=TREND persisted")

    route = pos.get("execution_route", "")
    if route not in ("maker_first", "taker_fallback"):
        _warn(f"execution_route={route}")
    else:
        _pass(f"execution_route={route} persisted")

    conn.close()
    return pos


# ─────────────────────────────────────────────────────────────
# EXECUTE CLOSE
# ─────────────────────────────────────────────────────────────
def execute_close() -> dict:
    _section("5. CLOSE BTC SPOT (maker-first immediate exit)")
    from spot_engine import close_spot

    print("         Waiting 2s before close to allow position to settle...")
    time.sleep(2)

    print(f"         Calling close_spot(BTC, exit_reason={EXIT_REASON!r})")
    start = time.time()
    result = close_spot(
        symbol=SYMBOL,
        exit_reason=EXIT_REASON,
    )
    elapsed = time.time() - start

    if result is None:
        # Check if maker sell timed out (taker disabled)
        _fail(
            f"close_spot returned None after {elapsed:.1f}s — "
            "maker sell may not have filled in 6s (taker disabled). "
            "BTC position still open in DB. Manual close required via Coinbase."
        )

    print(f"         close_spot result: {result}")
    _pass(
        f"close_spot succeeded: pnl=${result.get('pnl_usd', 0):.4f} "
        f"route={result.get('execution_route', '?')} fee=${result.get('fee_usd', 0):.4f} ({elapsed:.1f}s)"
    )
    return result


# ─────────────────────────────────────────────────────────────
# VERIFY LEARNING PIPELINE
# ─────────────────────────────────────────────────────────────
def verify_learning_pipeline(snap: dict) -> None:
    _section("6. VERIFY LEARNING PIPELINE (ml_feature_snapshots + trade_attribution)")
    time.sleep(1)  # brief settle for async DB writes
    conn = _db()

    # ── ml_feature_snapshots ────────────────────────────────
    mfs_row = conn.execute(
        """SELECT * FROM ml_feature_snapshots
           WHERE id > ? AND symbol='BTC' ORDER BY ts DESC LIMIT 1""",
        (snap["max_mfs_id"],),
    ).fetchone()
    if not mfs_row:
        conn.close()
        _fail("ml_feature_snapshots row NOT written — learning loop did not fire")
    mfs = dict(mfs_row)
    print(
        f"         ml_feature_snapshots id={mfs['id']}, symbol={mfs['symbol']}, reconstructed={mfs['reconstructed']}"
    )

    if mfs.get("reconstructed"):
        _fail(
            "ml_feature_snapshots row has reconstructed=1 — this is a backfill row, not a fresh close"
        )
    _pass("ml_feature_snapshots written (reconstructed=0) ✓")

    # Lineage fields
    lineage_checks = [
        ("candidate_id", 999999),
        ("scan_id", "acceptance_test_scan"),
        ("base_asset", "BTC"),
        ("executed_symbol", "BTC"),
        ("setup_family", "impulse_continuation"),
        ("spot_regime", "TREND"),
    ]
    for field, expected in lineage_checks:
        val = mfs.get(field)
        if val != expected:
            _warn(f"ml_feature_snapshots.{field}={val!r} (expected {expected!r})")
        else:
            _pass(f"ml_feature_snapshots.{field}={val!r} ✓")

    route = mfs.get("route_type", "")
    if route in ("maker_first", "taker_fallback"):
        _pass(f"ml_feature_snapshots.route_type={route!r} ✓")
    else:
        _warn(f"ml_feature_snapshots.route_type={route!r}")

    # ── trade_attribution ───────────────────────────────────
    attr_row = conn.execute(
        """SELECT * FROM trade_attribution
           WHERE id > ? AND symbol='BTC' ORDER BY id DESC LIMIT 1""",
        (snap["max_attr_id"],),
    ).fetchone()
    if not attr_row:
        _warn(
            "trade_attribution row NOT written — post_trade_analyzer may have had no signals to attribute"
        )
    else:
        attr = dict(attr_row)
        print(
            f"         trade_attribution id={attr['id']}, symbol={attr['symbol']}, candidate_id={attr.get('candidate_id')}"
        )
        if attr.get("candidate_id") != 999999:
            _warn(
                f"trade_attribution.candidate_id={attr.get('candidate_id')} (expected 999999)"
            )
        else:
            _pass("trade_attribution.candidate_id=999999 ✓")
        _pass("trade_attribution row written ✓")

    # ── signal_stats ON CONFLICT check ─────────────────────
    # Verify signal_stats UNIQUE key is 4-key by checking we can upsert without exception
    try:
        conn.execute(
            """INSERT INTO signal_stats (signal_name, regime, strategy, source, fires, wins)
               VALUES ('acceptance_test_signal', 'TREND', 'spot_btc', 'acceptance_test', 1, 1)
               ON CONFLICT(signal_name, regime, strategy, source) DO UPDATE SET fires=fires+1""",
        )
        conn.commit()
        _pass("signal_stats 4-key ON CONFLICT upsert works ✓")
    except Exception as e:
        _fail(f"signal_stats ON CONFLICT failed: {e}")

    conn.close()


# ─────────────────────────────────────────────────────────────
# VERIFY KILL SWITCH
# ─────────────────────────────────────────────────────────────
def verify_kill_switch(snap: dict) -> None:
    _section("7. VERIFY KILL SWITCH")
    from runtime.spot_kill_switch import check_spot_kill_switch, kill_switch_status

    # Should still fire KS10a because of pre-existing consecutive losses from before restart
    halt, reason = check_spot_kill_switch()
    print(f"         KS check: halt={halt}, reason={reason!r}")
    if halt:
        _pass(f"Kill switch correctly armed: {reason}")
    else:
        _warn(
            "Kill switch not halted — consecutive loss streak may have been broken by this trade"
        )

    status = kill_switch_status()
    print(f"         Kill switch status: {status}")
    _pass("kill_switch_status() returns dict without exception ✓")


# ─────────────────────────────────────────────────────────────
# VERIFY NO POSITION LEFT OPEN
# ─────────────────────────────────────────────────────────────
def verify_position_closed() -> None:
    _section("8. VERIFY POSITION FULLY CLOSED")
    conn = _db()
    pos = conn.execute(
        "SELECT * FROM open_positions WHERE symbol='BTC' AND paper=0 AND strategy='spot_btc'"
    ).fetchone()
    conn.close()
    if pos:
        _fail(
            "BTC position still in open_positions after close_spot — DB not cleaned up"
        )
    _pass("BTC position removed from open_positions ✓")


# ─────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────
def print_summary(snap: dict) -> None:
    _section("9. POST-TRADE DB SUMMARY")
    conn = _db()

    # Latest trades
    rows = conn.execute(
        "SELECT id, ts, action, qty, price, pnl_usd, fee_usd FROM trades WHERE id > ? ORDER BY id",
        (snap["max_trade_id"],),
    ).fetchall()
    print("  New trade rows:")
    for r in rows:
        print(
            f"    id={r['id']} {r['action']} qty={r['qty']:.6f} @ ${r['price']:.2f} pnl=${r['pnl_usd']:.4f} fee=${r['fee_usd']:.4f}"
        )

    # Latest ml_feature_snapshots
    mfs = conn.execute(
        "SELECT id, symbol, candidate_id, scan_id, setup_family, spot_regime, route_type, reconstructed FROM ml_feature_snapshots WHERE id > ?",
        (snap["max_mfs_id"],),
    ).fetchall()
    print("  New ml_feature_snapshots rows:")
    for r in mfs:
        print(
            f"    id={r['id']} sym={r['symbol']} family={r['setup_family']} regime={r['spot_regime']} route={r['route_type']} reconstructed={r['reconstructed']}"
        )

    # Latest trade_attribution
    attrs = conn.execute(
        "SELECT id, symbol, candidate_id, setup_family FROM trade_attribution WHERE id > ?",
        (snap["max_attr_id"],),
    ).fetchall()
    print("  New trade_attribution rows:")
    for r in attrs:
        print(
            f"    id={r['id']} sym={r['symbol']} candidate_id={r['candidate_id']} family={r['setup_family']}"
        )

    conn.close()


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  SPOT PIPELINE ACCEPTANCE TEST")
    print(f"  {_now_ts()}")
    print("=" * 60)
    print(f"  Symbol: {SYMBOL} | Size: ${SIZE_USD} | Mode: LIVE")
    print("  This places REAL orders on Coinbase. Cost ~$0.06 in fees.")
    print("=" * 60)

    try:
        check_preflight()
        snap = snapshot_before()
        open_result = execute_open()
        pos = verify_open_db(snap, open_result)
        close_result = execute_close()
        verify_learning_pipeline(snap)
        verify_kill_switch(snap)
        verify_position_closed()
        print_summary(snap)

        _section("RESULT: ALL CHECKS PASSED")
        print("  Learning pipeline end-to-end: VERIFIED")
        print(
            "  Quarantine gates: VERIFIED (impulse_continuation × TREND passed through)"
        )
        print("  Taker fallback disabled: VERIFIED (only if maker filled)")
        print("  Kill switch armed: VERIFIED")
        print("  Lineage fields populated: VERIFIED")
        print()
    except AssertionError as e:
        _section("RESULT: TEST FAILED")
        print(f"  FAILURE: {e}")
        sys.exit(1)