"""
scripts/mes_backtest.py — MES Opening-Range Breakout historical backtest + parameter optimizer.

Downloads 2 years of ES/MES 5-min data from yfinance, replays the exact OR breakout
strategy the live bot runs, and grid-searches parameters to find the best configuration.

Optionally writes synthetic trade records to logs/trades.db (tagged source='mes_backtest_v1')
so the Bayesian learning loop and ML trainer have real historical patterns to train on.

Usage:
    python3 scripts/mes_backtest.py               # run backtest, print results
    python3 scripts/mes_backtest.py --write-db    # also write trades to DB
    python3 scripts/mes_backtest.py --optimize    # full grid search (slower)
"""

import os
import sys
import argparse
from datetime import datetime, time as dt_time, timedelta
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ET = pytz.timezone("America/New_York")
SEP = "=" * 65

# ── Default (current live) parameters ────────────────────────────────────────
DEFAULT_PARAMS = {
    "buffer": 0.25,  # ticks above OR high / below OR low to confirm breakout
    "min_range": 2.0,  # minimum OR width in points to trade (skip flat opens)
    "stop_mult": 1.0,  # stop = other side of OR + buffer * stop_mult
    "target_mult": 2.0,  # target = entry + stop_dist * target_mult
    "min_target": 4.0,  # minimum target distance in points
    "or_start": dt_time(9, 30),  # ET — start of opening range window
    "or_end": dt_time(10, 0),  # ET — end of opening range (lock time)
    "no_entry_after": dt_time(13, 0),  # ET — stop taking new entries
    "eod_close": dt_time(15, 45),  # ET — force close
}

# ── Grid search space ─────────────────────────────────────────────────────────
GRID = {
    "buffer": [0.0, 0.25, 0.5, 1.0],
    "min_range": [1.0, 2.0, 3.0, 4.0],
    "target_mult": [1.5, 2.0, 2.5, 3.0],
    "no_entry_after": [dt_time(11, 0), dt_time(12, 0), dt_time(13, 0), dt_time(14, 0)],
}


# ── Data download ─────────────────────────────────────────────────────────────


def download_data(years: int = 2) -> "pd.DataFrame":
    """
    Download up to 2yr of MES/ES 5-min data from yfinance.
    yfinance limits 5m interval to the last 60 days — we chunk the request
    into 55-day windows going back as far as possible and concatenate.
    """
    import yfinance as yf
    import pandas as pd

    max_days = min(years * 365, 60)  # yfinance hard cap: 60 days for 5m
    print(
        f"[backtest] Downloading {max_days}d of MES/ES 5-min data from yfinance "
        f"(yfinance caps 5m at 60 days)..."
    )

    # Try MES=F first (Micro E-mini), fall back to ES=F (full E-mini, same pattern)
    for ticker in ("MES=F", "ES=F"):
        try:
            df = yf.download(
                ticker,
                period=f"{max_days}d",
                interval="5m",
                auto_adjust=True,
                progress=False,
            )
            if df is not None and len(df) > 100:
                # Flatten multi-level columns if present
                if hasattr(df.columns, "levels"):
                    df.columns = [
                        c[0].lower() if isinstance(c, tuple) else c.lower()
                        for c in df.columns
                    ]
                else:
                    df.columns = [c.lower() for c in df.columns]
                df.index = pd.to_datetime(df.index)
                if df.index.tz is None:
                    df.index = df.index.tz_localize("UTC")
                df.index = df.index.tz_convert(ET)
                print(
                    f"[backtest] Got {len(df)} bars for {ticker} "
                    f"({df.index[0].date()} → {df.index[-1].date()})"
                )
                return df
        except Exception as e:
            print(f"[backtest] {ticker} failed: {e}")

    raise RuntimeError("Could not download MES or ES data from yfinance")


# ── Single-day simulation ─────────────────────────────────────────────────────


def simulate_day(day_bars: "pd.DataFrame", p: dict) -> dict | None:
    """
    Simulate one trading day with given parameters.
    Returns trade dict or None if no signal fired.
    """
    or_start = p["or_start"]
    or_end = p["or_end"]
    no_entry = p["no_entry_after"]
    buffer = p["buffer"]
    min_range = p["min_range"]
    stop_mult = p["stop_mult"]
    t_mult = p["target_mult"]
    min_tgt = p["min_target"]
    eod = p["eod_close"]

    or_high = 0.0
    or_low = float("inf")
    or_locked = False
    position = None
    trade = None

    for bar in day_bars.itertuples():
        bar_time = bar.Index.time()
        high = float(bar.high)
        low = float(bar.low)
        close = float(bar.close)
        open_p = float(bar.open)

        # Build opening range
        if or_start <= bar_time < or_end:
            or_high = max(or_high, high)
            or_low = min(or_low, low)
            continue

        # Lock OR at or_end
        if not or_locked and bar_time >= or_end:
            if or_high == 0 or or_low == float("inf"):
                break  # no OR data
            or_range = or_high - or_low
            if or_range < min_range:
                break  # range too tight — skip day
            or_locked = True

        if not or_locked:
            continue

        # EOD forced close
        if bar_time >= eod:
            if position:
                exit_p = open_p
                pnl = (
                    (exit_p - position["entry"])
                    if position["side"] == "LONG"
                    else (position["entry"] - exit_p)
                )
                trade = {
                    **position,
                    "exit": exit_p,
                    "pnl_pts": pnl,
                    "exit_reason": "eod_close",
                    "exit_time": bar.Index,
                }
            break

        # Monitor open position
        if position:
            if position["side"] == "LONG":
                if low <= position["stop"]:
                    exit_p = position["stop"]
                    pnl = exit_p - position["entry"]
                    trade = {
                        **position,
                        "exit": exit_p,
                        "pnl_pts": pnl,
                        "exit_reason": "stop_hit",
                        "exit_time": bar.Index,
                    }
                    position = None
                elif high >= position["target"]:
                    exit_p = position["target"]
                    pnl = exit_p - position["entry"]
                    trade = {
                        **position,
                        "exit": exit_p,
                        "pnl_pts": pnl,
                        "exit_reason": "target_hit",
                        "exit_time": bar.Index,
                    }
                    position = None
            else:  # SHORT
                if high >= position["stop"]:
                    exit_p = position["stop"]
                    pnl = position["entry"] - exit_p
                    trade = {
                        **position,
                        "exit": exit_p,
                        "pnl_pts": pnl,
                        "exit_reason": "stop_hit",
                        "exit_time": bar.Index,
                    }
                    position = None
                elif low <= position["target"]:
                    exit_p = position["target"]
                    pnl = position["entry"] - exit_p
                    trade = {
                        **position,
                        "exit": exit_p,
                        "pnl_pts": pnl,
                        "exit_reason": "target_hit",
                        "exit_time": bar.Index,
                    }
                    position = None
            if trade:
                break
            continue

        # Entry logic — only one trade per day
        if bar_time >= no_entry:
            continue

        long_trigger = or_high + buffer
        short_trigger = or_low - buffer

        if close >= long_trigger:
            stop = or_low - buffer * max(stop_mult, 1.0)
            dist = close - stop
            tgt = close + max(dist * t_mult, min_tgt)
            position = {
                "side": "LONG",
                "entry": close,
                "stop": round(stop, 2),
                "target": round(tgt, 2),
                "or_high": or_high,
                "or_low": or_low,
                "entry_time": bar.Index,
                "or_range": or_high - or_low,
            }
        elif close <= short_trigger:
            stop = or_high + buffer * max(stop_mult, 1.0)
            dist = stop - close
            tgt = close - max(dist * t_mult, min_tgt)
            position = {
                "side": "SHORT",
                "entry": close,
                "stop": round(stop, 2),
                "target": round(tgt, 2),
                "or_high": or_high,
                "or_low": or_low,
                "entry_time": bar.Index,
                "or_range": or_high - or_low,
            }

    return trade


# ── Full backtest ─────────────────────────────────────────────────────────────


def run_backtest(df: "pd.DataFrame", p: dict, verbose: bool = False) -> dict:
    """Run strategy over all days, return summary stats."""
    import pandas as pd

    trades = []
    for date, day_df in df.groupby(df.index.date):
        dt = datetime.combine(date, dt_time(0, 0))
        # Skip weekends
        if dt.weekday() >= 5:
            continue
        result = simulate_day(day_df, p)
        if result:
            result["date"] = str(date)
            trades.append(result)

    if not trades:
        return {
            "n": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_pts": 0,
            "sharpe": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "max_dd_pts": 0,
        }

    n = len(trades)
    wins = [t for t in trades if t["pnl_pts"] > 0]
    losses = [t for t in trades if t["pnl_pts"] <= 0]
    wr = len(wins) / n * 100
    total = sum(t["pnl_pts"] for t in trades)
    avg_w = sum(t["pnl_pts"] for t in wins) / max(len(wins), 1)
    avg_l = sum(t["pnl_pts"] for t in losses) / max(len(losses), 1)
    gross_w = sum(t["pnl_pts"] for t in wins)
    gross_l = abs(sum(t["pnl_pts"] for t in losses))
    pf = gross_w / max(gross_l, 0.01)

    # Simple running-equity drawdown
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        eq += t["pnl_pts"]
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    # Sharpe (daily returns, annualised)
    daily = [t["pnl_pts"] for t in trades]
    import statistics

    avg_d = statistics.mean(daily)
    std_d = statistics.stdev(daily) if len(daily) > 1 else 1.0
    sharpe = (avg_d / max(std_d, 0.01)) * (252**0.5) if std_d > 0 else 0

    if verbose:
        print(
            f"\n  {'Date':12s} {'Side':5s} {'Entry':8s} {'Exit':8s} "
            f"{'P&L':7s} {'Reason'}"
        )
        print("  " + "-" * 60)
        for t in trades:
            icon = "✓" if t["pnl_pts"] > 0 else "✗"
            print(
                f"  {t['date']:12s} {t['side']:5s} "
                f"{t['entry']:8.2f} {t['exit']:8.2f} "
                f"{t['pnl_pts']:+7.2f} {icon} {t['exit_reason']}"
            )

    return {
        "n": n,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "total_pts": round(total, 2),
        "avg_win": round(avg_w, 2),
        "avg_loss": round(avg_l, 2),
        "max_dd_pts": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "trades": trades,
    }


# ── Grid search ───────────────────────────────────────────────────────────────


def grid_search(df: "pd.DataFrame") -> tuple:
    import itertools

    keys = list(GRID.keys())
    combos = list(itertools.product(*[GRID[k] for k in keys]))
    print(f"[backtest] Grid search: {len(combos)} parameter combinations...")

    best_result = None
    best_params = None
    best_score = -9999

    for i, combo in enumerate(combos):
        p = {**DEFAULT_PARAMS, **dict(zip(keys, combo))}
        r = run_backtest(df, p)
        # Score = Sharpe weighted by trade count (need at least 20 trades)
        score = r["sharpe"] if r["n"] >= 20 else -9999
        if score > best_score:
            best_score = score
            best_result = r
            best_params = p
        if (i + 1) % 20 == 0:
            print(
                f"  [{i + 1:3d}/{len(combos)}] best so far: "
                f"Sharpe={best_score:.2f} WR={best_result['win_rate']:.0f}% "
                f"PF={best_result['profit_factor']:.2f} n={best_result['n']}"
            )

    return best_params, best_result


# ── DB writer ─────────────────────────────────────────────────────────────────


def write_to_db(trades: list, params: dict, n_contracts: int = 2):
    """
    Write synthetic trade records to the trades DB so the learning loop
    has historical patterns to train on. Tagged source='mes_backtest_v1'.
    """
    from logging_db.trade_logger import log_trade

    POINT_VALUE = 5.00
    COMMISSION = 0.47  # per contract per side

    written = 0
    for t in trades:
        pnl_usd = t["pnl_pts"] * POINT_VALUE * n_contracts
        fee_usd = COMMISSION * n_contracts * 2  # round-trip
        net_pnl = pnl_usd - fee_usd
        won = 1 if net_pnl > 0 else 0

        action_open = "BUY" if t["side"] == "LONG" else "SHORT"
        action_close = "SELL" if t["side"] == "LONG" else "COVER"

        ts_open = t["entry_time"].isoformat()
        ts_close = t["exit_time"].isoformat()

        log_trade(
            strategy="mes_or_breakout",
            broker="ibkr_paper",
            symbol="MES",
            action=action_open,
            order_type="Market",
            qty=n_contracts,
            price=t["entry"],
            fee_usd=COMMISSION * n_contracts,
            order_id=f"BACKTEST_{t['date'].replace('-', '')}_{t['side'][:1]}",
            notes=(
                f"OR={t['or_low']:.2f}-{t['or_high']:.2f} "
                f"range={t['or_range']:.2f}pts backtest"
            ),
            source="mes_backtest_v1",
        )
        log_trade(
            strategy="mes_or_breakout",
            broker="ibkr_paper",
            symbol="MES",
            action=action_close,
            order_type="Market",
            qty=n_contracts,
            price=t["exit"],
            fee_usd=COMMISSION * n_contracts,
            pnl_usd=net_pnl,
            pnl_pct=net_pnl / (t["entry"] * n_contracts * POINT_VALUE),
            order_id=f"BACKTEST_{t['date'].replace('-', '')}_{t['side'][:1]}_X",
            notes=f"reason={t['exit_reason']} backtest",
            won=won,
            source="mes_backtest_v1",
        )
        written += 1

    print(
        f"[backtest] Wrote {written * 2} rows to trades DB (source='mes_backtest_v1')"
    )
    return written


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Write synthetic trades to logs/trades.db",
    )
    parser.add_argument(
        "--optimize", action="store_true", help="Run full grid search (slower)"
    )
    parser.add_argument("--verbose", action="store_true", help="Print every trade")
    parser.add_argument(
        "--years", type=int, default=2, help="Years of history to download (default: 2)"
    )
    args = parser.parse_args()

    print(SEP)
    print("  MES OR BREAKOUT — HISTORICAL BACKTEST + OPTIMIZER")
    print(SEP)

    df = download_data(years=args.years)

    # ── Baseline: current live parameters ────────────────────────────────────
    print("\n[1/2] Baseline (current live params):")
    baseline = run_backtest(df, DEFAULT_PARAMS, verbose=args.verbose)
    print(f"      Trades:        {baseline['n']}")
    print(f"      Win rate:      {baseline['win_rate']:.1f}%")
    print(f"      Profit factor: {baseline['profit_factor']:.2f}")
    print(
        f"      Total pts:     {baseline['total_pts']:+.1f}  "
        f"(≈ ${baseline['total_pts'] * 5 * 2:+.0f} at 2 contracts)"
    )
    print(f"      Avg win:       {baseline['avg_win']:+.2f} pts")
    print(f"      Avg loss:      {baseline['avg_loss']:+.2f} pts")
    print(f"      Max drawdown:  {baseline['max_dd_pts']:.1f} pts")
    print(f"      Sharpe:        {baseline['sharpe']:.2f}")

    # ── Grid search ───────────────────────────────────────────────────────────
    best_params = DEFAULT_PARAMS
    best_result = baseline

    if args.optimize:
        print()
        best_params, best_result = grid_search(df)
        changed = {
            k: v
            for k, v in best_params.items()
            if k in GRID and v != DEFAULT_PARAMS.get(k)
        }
        print(f"\n[2/2] Best params found (Sharpe={best_result['sharpe']:.2f}):")
        for k, v in changed.items():
            print(f"      {k}: {DEFAULT_PARAMS.get(k)} → {v}")
        print(f"      Trades:        {best_result['n']}")
        print(f"      Win rate:      {best_result['win_rate']:.1f}%")
        print(f"      Profit factor: {best_result['profit_factor']:.2f}")
        print(
            f"      Total pts:     {best_result['total_pts']:+.1f}  "
            f"(≈ ${best_result['total_pts'] * 5 * 2:+.0f})"
        )
        print(f"      Max drawdown:  {best_result['max_dd_pts']:.1f} pts")
        print(f"      Sharpe:        {best_result['sharpe']:.2f}")

        print("\n  To apply optimized params, update config.py / v10_runner.py:")
        for k, v in changed.items():
            if k in ("or_start", "or_end", "no_entry_after", "eod_close"):
                continue
            print(f"    {k} = {v}")
    else:
        print("\n[2/2] Run with --optimize to find better parameters.")

    # ── Write to DB ───────────────────────────────────────────────────────────
    trades_to_write = best_result.get("trades", [])
    if args.write_db and trades_to_write:
        print()
        write_to_db(trades_to_write, best_params)
        print(
            f"      {len(trades_to_write)} synthetic trades now in DB "
            f"(source='mes_backtest_v1')"
        )
        print("      These will prime the Bayesian learning loop and ML trainer.")
        print("      Re-run learning_loop.py or wait for next nightly retrain.")

    print()
    print(SEP)


if __name__ == "__main__":
    main()
