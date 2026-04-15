#!/usr/bin/env python3
"""
scripts/funding_carry_audit.py — Funding regime + carry suitability audit.

Reads scan_candidates for observed funding rates, price_archive.db for
4h/1d structure, and produces:
  - Per-symbol funding posture (hostile / neutral / favorable)
  - Spot vs perp routing recommendation
  - Carry suitability table
  - Tonight's $500 instrument split doctrine

Usage:
    python3 scripts/funding_carry_audit.py
    python3 scripts/funding_carry_audit.py --json
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from strategies.market_type_classifier import MarketType, classify, underlying
from strategies.funding_instrument_router import (
    CARRY_SUITABILITY,
    FundingRegime,
    InstrumentRoute,
    classify_funding,
    route,
)
from strategies.symbol_governance import GovernanceStatus, get_policy

DEFAULT_DB = PROJ / "logs" / "trades.db"
DEFAULT_PRICE_DB = PROJ / "logs" / "price_archive.db"

HEADLINE_SOURCES = ("clean_paper_v10", "live_v10")


def _load_funding_from_candidates(db_path: str) -> dict[str, dict]:
    """
    Load observed funding rates from scan_candidates.
    Returns {symbol: {avg_rate, min_rate, max_rate, n, pct_hostile, pct_favorable}}.
    Note: Projects DB fresh session may have all-zero rates — flagged in output.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    try:
        c.execute(
            """
            SELECT symbol,
                   COUNT(*) as n,
                   AVG(funding_rate) as avg_rate,
                   MIN(funding_rate) as min_rate,
                   MAX(funding_rate) as max_rate,
                   SUM(CASE WHEN funding_rate > 0.0002 THEN 1 ELSE 0 END) as hostile_n,
                   SUM(CASE WHEN funding_rate < -0.0001 THEN 1 ELSE 0 END) as favorable_n
            FROM scan_candidates
            WHERE funding_rate IS NOT NULL
            GROUP BY symbol
            ORDER BY symbol
            """
        )
        rows = c.fetchall()
    except Exception:
        rows = []

    conn.close()

    result = {}
    for r in rows:
        n = r["n"] or 1
        result[r["symbol"]] = {
            "n": n,
            "avg_rate": r["avg_rate"],
            "min_rate": r["min_rate"],
            "max_rate": r["max_rate"],
            "pct_hostile": round(100 * (r["hostile_n"] or 0) / n, 1),
            "pct_favorable": round(100 * (r["favorable_n"] or 0) / n, 1),
            "all_zero": (
                r["avg_rate"] == 0 and r["min_rate"] == 0 and r["max_rate"] == 0
            ),
        }
    return result


def _load_price_structure(db_path: str, symbols: list[str]) -> dict[str, dict]:
    """Load 4h EMA posture and 30d return for key symbols."""
    if not Path(db_path).exists():
        return {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    result = {}

    def ema(data: list[float], span: int) -> float:
        alpha = 2 / (span + 1)
        e = data[0]
        for d in data[1:]:
            e = alpha * d + (1 - alpha) * e
        return e

    for sym in symbols:
        # Try exact, then underlying
        for lookup in (sym, underlying(sym)):
            # 4h structure
            c.execute(
                "SELECT close FROM ohlcv WHERE symbol=? AND timeframe='4h' ORDER BY open_time",
                (lookup,),
            )
            rows_4h = [r["close"] for r in c.fetchall()]

            # 1d 30d return + rvol
            c.execute(
                "SELECT close FROM ohlcv WHERE symbol=? AND timeframe='1d' ORDER BY open_time",
                (lookup,),
            )
            rows_1d = [r["close"] for r in c.fetchall()]

            if rows_4h or rows_1d:
                entry: dict = {}
                if len(rows_4h) >= 20:
                    e8 = ema(rows_4h, 8)
                    e21 = ema(rows_4h, 21)
                    entry["4h_ema_bias"] = "BULL" if e8 > e21 else "BEAR"
                    entry["4h_20bar_pct"] = round(
                        (rows_4h[-1] / rows_4h[-20] - 1) * 100, 2
                    )
                if len(rows_1d) >= 30:
                    entry["1d_30d_pct"] = round(
                        (rows_1d[-1] / rows_1d[-30] - 1) * 100, 2
                    )
                    log_r = [
                        math.log(rows_1d[i] / rows_1d[i - 1])
                        for i in range(max(1, len(rows_1d) - 30), len(rows_1d))
                    ]
                    entry["1d_rvol30"] = (
                        round(
                            math.sqrt(sum(x * x for x in log_r) / len(log_r))
                            * math.sqrt(365)
                            * 100,
                            1,
                        )
                        if log_r
                        else None
                    )
                if len(rows_1d) >= 90:
                    high90 = max(rows_1d[-90:])
                    entry["1d_dd90_pct"] = round((rows_1d[-1] / high90 - 1) * 100, 2)
                if entry:
                    result[sym] = entry
                    break

    conn.close()
    return result


def _tonight_bankroll_split(rows: list[dict]) -> dict:
    """
    Produce tonight's $500 bankroll allocation doctrine.
    Constrained live: longs only, max 4 positions, 70% max deployed.
    """
    allowed = [r for r in rows if r["route"] not in ("blocked",) and not r["is_pf"]]
    perp_preferred = [r for r in allowed if r["route"] == "perp_preferred"]
    spot_pref = [
        r for r in allowed if r["route"] in ("spot_preferred", "perp_tolerated")
    ]

    return {
        "bankroll": 500,
        "mode": "constrained_live",
        "max_positions": 4,
        "max_deployed_pct": 70,
        "max_deployed_usd": 350,
        "per_trade_risk_usd": 5.0,
        "per_trade_risk_pct": 1.0,
        "leverage": 3,
        "instrument": "perp (Binance USDM / Hyperliquid)",
        "note": (
            "Spot infrastructure not live tonight — all non-PF crypto trades "
            "go through perp venue. Spot-preferred doctrine is aspirational until "
            "spot order routing is implemented."
        ),
        "shorts_allowed": False,
        "shorts_note": "Suppressed per go-live audit: LONG net=+13.56 vs SHORT net=-13.82",
        "longs_allowed_symbols": [r["symbol"] for r in allowed],
        "perp_preferred_symbols": [r["symbol"] for r in perp_preferred],
        "spot_when_available": [r["symbol"] for r in spot_pref],
        "pf_symbols_paper_only": True,
    }


def build_carry_audit(db_path: str, price_db: str) -> dict:
    price_db_exists = Path(price_db).exists()
    pdb = price_db if price_db_exists else None

    funding_data = _load_funding_from_candidates(db_path)
    all_zero_warning = (
        all(v.get("all_zero", False) for v in funding_data.values())
        if funding_data
        else True
    )

    # Symbols to audit: all in CARRY_SUITABILITY + any with funding data
    target_symbols = sorted(set(CARRY_SUITABILITY.keys()) | set(funding_data.keys()))

    price_struct = _load_price_structure(price_db, target_symbols) if pdb else {}

    rows = []
    for sym in target_symbols:
        policy = get_policy(sym, price_db=pdb)
        mt = policy.market_type

        # Funding regime: use observed avg or None
        fd = funding_data.get(sym, {})
        avg_rate = fd.get("avg_rate") if not fd.get("all_zero") else None
        regime = classify_funding(avg_rate)

        # Route decision
        rd = route(sym, "LONG", mt, avg_rate)

        # Price structure
        ps = price_struct.get(sym, {})

        carry_suit, carry_note = CARRY_SUITABILITY.get(sym, ("unknown", "no data"))

        rows.append(
            {
                "symbol": sym,
                "market_type": mt.value,
                "governance": policy.governance.value,
                "carry_suitability": carry_suit,
                "funding_regime": regime.value,
                "avg_funding_rate": avg_rate,
                "pct_hostile": fd.get("pct_hostile"),
                "pct_favorable": fd.get("pct_favorable"),
                "obs_n": fd.get("n", 0),
                "route": rd.route.value,
                "route_reason": rd.reason,
                "is_pf": rd.is_pf_symbol,
                "4h_bias": ps.get("4h_ema_bias"),
                "4h_20bar_pct": ps.get("4h_20bar_pct"),
                "1d_30d_pct": ps.get("1d_30d_pct"),
                "1d_rvol30": ps.get("1d_rvol30"),
                "1d_dd90_pct": ps.get("1d_dd90_pct"),
                "carry_note": carry_note,
            }
        )

    tonight = _tonight_bankroll_split(rows)

    return {
        "funding_data_warning": (
            "All funding rates are zero in current DB session. "
            "Projects DB is fresh — funding rates not captured yet. "
            "Routing uses default assumptions (neutral). "
            "Fetch live rates from exchange APIs for real routing."
        )
        if all_zero_warning
        else None,
        "carry_suitability_table": rows,
        "tonight_500_profile": tonight,
        "funding_thresholds": {
            "hostile_per_8h": ">+0.02%",
            "neutral_per_8h": "|rate| <= 0.02%",
            "favorable_per_8h": "<-0.01%",
            "carry_positive_per_8h": "<-0.03%",
            "note": "Positive rate = longs pay shorts. Negative = shorts pay longs (carry for longs).",
        },
    }


def _print_text(audit: dict) -> None:
    print("=" * 70)
    print("FUNDING / CARRY AUDIT")
    print("=" * 70)

    if audit.get("funding_data_warning"):
        print(f"\n[WARNING] {audit['funding_data_warning']}")

    t = audit["funding_thresholds"]
    print(
        f"\nFunding thresholds: hostile={t['hostile_per_8h']}  neutral={t['neutral_per_8h']}  "
        f"favorable={t['favorable_per_8h']}  carry={t['carry_positive_per_8h']}"
    )
    print(f"Convention: {t['note']}")

    print(
        f"\n{'Symbol':12s}  {'Bucket':25s}  {'Carry':10s}  {'Regime':12s}  "
        f"{'Route':16s}  {'4hBias':6s}  {'30d%':7s}  {'RVol':6s}"
    )
    print("-" * 110)
    for r in audit["carry_suitability_table"]:
        bias = r.get("4h_bias") or "  n/a"
        ret30 = (
            f"{r['1d_30d_pct']:+7.1f}" if r.get("1d_30d_pct") is not None else "    n/a"
        )
        rvol = f"{r['1d_rvol30']:6.1f}" if r.get("1d_rvol30") is not None else "   n/a"
        regime_str = r["funding_regime"]
        if r.get("avg_funding_rate") is None:
            regime_str += "(assumed)"
        print(
            f"  {r['symbol']:12s}  {r['market_type']:25s}  {r['carry_suitability']:10s}  "
            f"{regime_str:12s}  {r['route']:16s}  {bias:6s}  {ret30}  {rvol}"
        )

    tn = audit["tonight_500_profile"]
    print(f"\n── Tonight ${tn['bankroll']} Operating Profile ──")
    print(f"  Mode: {tn['mode']}")
    print(f"  Max positions: {tn['max_positions']}")
    print(f"  Max deployed: ${tn['max_deployed_usd']} ({tn['max_deployed_pct']}%)")
    print(
        f"  Per-trade risk: ${tn['per_trade_risk_usd']} ({tn['per_trade_risk_pct']}%)"
    )
    print(f"  Leverage: {tn['leverage']}x")
    print(f"  Instrument: {tn['instrument']}")
    print(
        f"  Shorts: {'ALLOWED' if tn['shorts_allowed'] else 'BLOCKED — ' + tn['shorts_note']}"
    )
    print(f"  PF_* symbols: {'PAPER ONLY' if tn['pf_symbols_paper_only'] else 'live'}")
    print(f"\n  Longs allowed ({len(tn['longs_allowed_symbols'])} symbols):")
    for s in sorted(tn["longs_allowed_symbols"]):
        print(f"    {s}")
    print(f"\n  Note: {tn['note']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Funding/carry suitability audit")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--price-db", default=str(DEFAULT_PRICE_DB))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    audit = build_carry_audit(args.db, args.price_db)
    if args.json:
        print(json.dumps(audit, indent=2, default=str))
    else:
        _print_text(audit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
