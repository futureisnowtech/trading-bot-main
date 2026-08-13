#!/usr/bin/env python3
"""
scripts/audit_real_kalshi_records.py
Canonical Real Kalshi REST API Auditor.

Calculates exact account-level PnL directly from Kalshi V2 REST API:
  - /trade-api/v2/portfolio/balance
  - /trade-api/v2/portfolio/positions
  - /trade-api/v2/portfolio/settlements
"""

import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import POST_PAPER_START_DATE as LIVE_ERA_START
from execution.kalshi_broker import KalshiBroker
from runtime.kalshi_settlement_truth import settlement_pnl_usd

def audit(start_date: str = LIVE_ERA_START, end_date: str | None = None, emit_webapp_ts: str | None = None):
    broker = KalshiBroker()
    if not broker.connect():
        print("❌ Auth Error: Failed to connect to live Kalshi REST API.")
        sys.exit(1)

    window_desc = f"{start_date} to {end_date}" if end_date else f"{start_date} to Present"
    print(f"\n==================================================================")
    print(f"🔒 CANONICAL REAL KALSHI REST API AUDIT ({window_desc})")
    print(f"==================================================================\n")

    # 1. Real Balance & Open Portfolio Value
    bal_data = broker._request("GET", "/trade-api/v2/portfolio/balance")
    balance_dollars = float(bal_data.get("balance_dollars", 0.0))
    portfolio_value = float(bal_data.get("portfolio_value", 0.0)) / 100.0 if "portfolio_value" in bal_data else 0.0

    print(f"💰 Account Cash Balance:   ${balance_dollars:,.2f}")
    print(f"📈 Open Portfolio Value:   ${portfolio_value:,.2f}")
    print(f"🛡️ Total Account Equity:    ${balance_dollars + portfolio_value:,.2f}")

    # 2. Open Market Positions
    pos_data = broker._request("GET", "/trade-api/v2/portfolio/positions")
    mkt_positions = pos_data.get("market_positions", [])
    active_mkt = [p for p in mkt_positions if abs(float(p.get("position_fp", 0.0))) > 0.001]

    print(f"\n📊 Active Kalshi Positions ({len(active_mkt)}):")
    for p in active_mkt:
        ticker = p.get("ticker")
        fp = float(p.get("position_fp", 0.0))
        exposure = float(p.get("market_exposure_dollars", 0.0))
        realized = float(p.get("realized_pnl_dollars", 0.0))
        side = "YES" if fp > 0 else "NO"
        print(f"  • {ticker:<24} | Side: {side:<3} | Qty: {abs(fp):5.1f} | Exposure: ${exposure:6.2f} | Realized PnL: ${realized:+6.2f}")

    # 3. Official Settlements (True Accounting Formula)
    all_settlements = []
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        res = broker._request("GET", "/trade-api/v2/portfolio/settlements", params=params)
        settlements = res.get("settlements", [])
        if not settlements:
            break
        all_settlements.extend(settlements)
        cursor = res.get("cursor")
        if not cursor:
            break

    # Comparing the full ISO timestamp against a bare end_date (e.g. "2026-08-10") with
    # <= would wrongly exclude every row settled ON that date, since
    # "2026-08-10T14:06:35Z" > "2026-08-10" lexicographically. Slice to the date prefix.
    period_settlements = [
        s for s in all_settlements
        if s.get("settled_time", "") >= start_date
        and (not end_date or str(s.get("settled_time", ""))[:10] <= end_date)
    ]

    total_net_pnl = 0.0
    total_fees = 0.0
    wins = 0
    losses = 0
    scratches = 0

    for s in period_settlements:
        total_fees += float(s.get("fee_cost", 0))

        # Single canonical formula, shared with the cockpit and the WebApp ledger.
        net_pnl = settlement_pnl_usd(s)
        total_net_pnl += net_pnl

        if net_pnl > 0.001:
            wins += 1
        elif net_pnl < -0.001:
            losses += 1
        else:
            scratches += 1

    win_rate = (100.0 * wins / len(period_settlements)) if period_settlements else 0.0

    print(f"\n==================================================================")
    print(f"🧾 OFFICIAL KALSHI ACCOUNT PnL AUDIT ({window_desc}):")
    print(f"==================================================================")
    print(f"• Total Settled Contracts:   {len(period_settlements)}")
    print(f"• Winning Settlements:       {wins}")
    print(f"• Losing Settlements:        {losses}")
    print(f"• Settled Win Rate:          {win_rate:.1f}%")
    print(f"• Total Exchange Fees Paid:  ${total_fees:,.2f}")
    print(f"• REALIZED ACCOUNT PnL:      ${total_net_pnl:+,.2f}")
    print(f"==================================================================\n")

    # Independent cross-check: rebuild the same PnL from raw fill cashflow. Method A
    # (above) reads settlement aggregates; this reads every individual fill. They use
    # different endpoints, so agreement means the number is real and not a formula artifact.
    verify_pnl, detail = _verify_via_fills(broker, period_settlements, LIVE_ERA_START)
    if verify_pnl is not None:
        drift = abs(verify_pnl - total_net_pnl)
        status = "✅ agree" if drift < 1.0 else f"❌ DISAGREE by ${drift:,.2f}"
        print(f"🔁 CROSS-CHECK (independent fill cashflow): ${verify_pnl:+,.2f}  [{status}]")
        print(f"   {detail}")
        print(f"   implied account equity on {start_date}: ${balance_dollars + portfolio_value - total_net_pnl:,.2f}")
        if drift >= 1.0:
            raise SystemExit("Refusing to report: the two methods disagree.")
        print()

    if emit_webapp_ts:
        emit_results_data_ts(
            emit_webapp_ts,
            settlements=period_settlements,
            positions=active_mkt,
            start_date=start_date,
            end_date=end_date,
        )


def _verify_via_fills(broker, settlements: list, fills_lookback_start: str):
    """Rebuild realized PnL from individual fills as a check on the settlement math.

    Sign convention that matters: a fill with side="no" and action="sell" ACQUIRES no
    contracts (it is a sale of the yes side), so it is cash out, not cash in. Reading
    those as inflows inflates the result by hundreds of dollars.

    fills_lookback_start must be the live era's start, NOT a windowed report's own
    --start: a position can be entered days before it settles, so bounding the fills
    pull to the report window clips entry fills for anything opened earlier, understating
    `paid` and inflating the apparent PnL. The ticker set from `settlements` still scopes
    the result to exactly the window being reported -- only the fills lookback needs the
    wider net. A windowed --start/--end run once disagreed with settlement PnL by $52 for
    exactly this reason before the lookback was decoupled from the window.
    """
    from runtime.kalshi_settlement_truth import _parse_session_start

    def _f(v):
        return float(v or 0)

    try:
        min_ts = int(_parse_session_start(fills_lookback_start).timestamp())
        fills, cursor = [], ""
        while True:
            params = {"limit": 200, "min_ts": min_ts}
            if cursor:
                params["cursor"] = cursor
            res = broker._request("GET", "/trade-api/v2/portfolio/fills", params=params)
            batch = res.get("fills") or []
            if not batch:
                break
            fills.extend(batch)
            cursor = str(res.get("cursor") or "")
            if not cursor:
                break

        tickers = {r.get("ticker") for r in settlements}
        paid = recovered = fees = 0.0
        for x in fills:
            if x.get("ticker") not in tickers:
                continue
            qty = _f(x.get("count_fp"))
            side = str(x.get("side") or "").lower()
            price = _f(x.get("yes_price_dollars")) if side == "yes" else _f(x.get("no_price_dollars"))
            fees += _f(x.get("fee_cost"))
            acquiring = (x.get("action") == "buy") if side == "yes" else (x.get("action") == "sell")
            if acquiring:
                paid += qty * price
            else:
                recovered += qty * price

        settled = sum(
            _f(r.get("yes_count_fp")) if r.get("market_result") == "yes" else _f(r.get("no_count_fp"))
            for r in settlements
        )
        pnl = recovered + settled - paid - fees
        detail = (f"{len(fills)} fills | paid ${paid:,.2f} | closed early ${recovered:,.2f} "
                  f"| settled ${settled:,.2f} | fees ${fees:,.2f}")
        return pnl, detail
    except Exception as exc:
        return None, f"fill cross-check unavailable: {exc}"


def _fmt_usd(amount: float) -> str:
    return f"{'+$' if amount >= 0 else '-$'}{abs(amount):.2f}"


def emit_results_data_ts(
    path: str, *, settlements: list, positions: list, start_date: str, end_date: str | None = None
) -> None:
    """Write the WebApp trade ledger (src/lib/resultsData.ts) from settlement truth.

    The headline in botStats and the rows underneath it are derived from the same
    list in the same pass, then asserted to agree before anything is written. A
    published ledger whose header contradicts its own rows is worse than no ledger.
    """
    import json

    rows = []
    row_pnls = []
    for s in sorted(settlements, key=lambda r: str(r.get("settled_time") or ""), reverse=True):
        # Round once, here, so the published row and the headline use the same number.
        pnl = round(settlement_pnl_usd(s), 2)
        row_pnls.append(pnl)
        result = str(s.get("market_result") or "").lower()
        # Report the side actually carried into settlement.
        count = float(s.get("no_count_fp") or s.get("no_count") or 0.0) if result == "no" \
            else float(s.get("yes_count_fp") or s.get("yes_count") or 0.0)
        cost = float(s.get("no_total_cost_dollars") or 0.0) if result == "no" \
            else float(s.get("yes_total_cost_dollars") or 0.0)
        rows.append({
            "settled": str(s.get("settled_time") or "")[:10],
            "bot": "Weather",
            "ticker": str(s.get("ticker") or ""),
            "side": "no" if result == "no" else "yes",
            "contracts": int(round(count)),
            "entry": f"${(cost / count if count else 0.0):.2f}",
            # Plain words only — ResultsDashboard renders its own ✓/✗ glyph.
            "result": "Win" if pnl > 0.001 else ("Loss" if pnl < -0.001 else "Scratch"),
            "pnl": _fmt_usd(pnl),
        })

    wins = sum(1 for r in rows if r["result"] == "Win")
    # Headline is the sum of the rounded rows the page actually renders, so a reader
    # adding up the ledger lands on exactly the number in the header. Summing the
    # unrounded values instead drifts by a few cents across 163 rows.
    total_pnl = round(sum(row_pnls), 2)
    win_rate = (100.0 * wins / len(rows)) if rows else 0.0

    # Guardrail: headline must equal the sum of the rows it sits above.
    rows_pnl = round(sum(
        float(r["pnl"][2:]) * (1 if r["pnl"].startswith("+") else -1) for r in rows
    ), 2)
    if abs(rows_pnl - total_pnl) > 0.005:
        raise SystemExit(f"❌ Consistency check failed: rows sum {rows_pnl:+.2f} != headline {total_pnl:+.2f}")
    if abs(wins - sum(1 for p in row_pnls if p > 0.001)) > 0:
        raise SystemExit("❌ Consistency check failed: win count disagrees with row PnL signs")

    open_rows = []
    for p in positions:
        fp = float(p.get("position_fp", 0.0))
        qty = abs(fp)
        exposure = abs(float(p.get("market_exposure_dollars", 0.0)))
        open_rows.append({
            "bot": "Weather",
            "ticker": str(p.get("ticker") or ""),
            "side": "yes" if fp > 0 else "no",
            "contracts": int(round(qty)),
            "entry": f"${(exposure / qty if qty else 0.0):.2f}",
            "submitted": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        })

    def ts_rows(items: list) -> str:
        return "\n".join(f"  {json.dumps(r, ensure_ascii=False)}," for r in items)

    window_label = f"{start_date} to {end_date}" if end_date else f"{start_date}+"
    content = f"""// AUTO-GENERATED by scripts/audit_real_kalshi_records.py --emit-webapp-ts
// Source: Kalshi REST /trade-api/v2/portfolio/settlements (canonical settlement formula)
// Window: {window_label} (live trading only) | {len(rows)} settlements | generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
// Do not hand-edit: regenerate instead, so the headline always equals the sum of the rows.

export interface Trade {{
  settled: string;
  bot: "Weather";
  ticker: string;
  side: "yes" | "no";
  contracts: number;
  entry: string;
  result: string;
  pnl: string;
}}

export interface OpenPosition {{
  bot: "Weather";
  ticker: string;
  side: "yes" | "no";
  contracts: number;
  entry: string;
  submitted: string;
}}

export interface BotStats {{
  name: string;
  version: string;
  winRate: string;
  totalTrades: number;
  pnl: string;
  isPositive: boolean;
  retired?: boolean;
}}

export const botStats: BotStats[] = [
  {{
    "name": "Weather Bot",
    "version": "v19.17",
    "winRate": "{win_rate:.1f}%",
    "totalTrades": {len(rows)},
    "pnl": "{_fmt_usd(total_pnl)}",
    "isPositive": {str(total_pnl >= 0).lower()}
  }},
];

export const settledTrades: Trade[] = [
{ts_rows(rows)}
];

export const openPositions: OpenPosition[] = [
{ts_rows(open_rows)}
];

export const releaseLogs: {{ date: string; tag: string; details: string }}[] = [];
"""

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)

    losses = sum(1 for p in row_pnls if p < -0.001)
    scratches = len(rows) - wins - losses
    print(f"✅ Wrote {path}")
    print(f"   {len(rows)} settlements | {wins}W/{losses}L/{scratches}S | {win_rate:.1f}% | {_fmt_usd(total_pnl)}")
    print(f"   {len(open_rows)} open positions | headline == sum(rows) verified")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=LIVE_ERA_START, help="Start date ISO format (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="Inclusive end date ISO format (YYYY-MM-DD). Omit for open-ended.")
    parser.add_argument("--emit-webapp-ts", default=None, help="Path to write the WebApp resultsData.ts")
    args = parser.parse_args()
    audit(start_date=args.start, end_date=args.end, emit_webapp_ts=args.emit_webapp_ts)
