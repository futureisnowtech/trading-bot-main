"""
scripts/test_futures_paper_trades.py — Force 10 MES paper trades for pipeline testing.

Exercises broker, trade logger, notification engine, and dashboard display
without modifying any strategy files or thresholds.

5 round trips (10 trades): alternating LONG / SHORT, 60s hold, 90s between.
Total runtime: ~12 minutes.

Usage:
    python3 scripts/test_futures_paper_trades.py

TWS not required — trades log to DB via offline paper path if disconnected.
Trades tagged with reason=pipeline_test in notes for easy identification.
Results appear on dashboard FUTURES tab immediately.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROUNDS = 5          # 5 round trips = 10 trades
HOLD_SECONDS = 60   # hold each position 60 seconds before exiting
PAUSE_SECONDS = 90  # pause between round trips


def _fmt_pnl(pnl: float) -> str:
    sign = "+" if pnl >= 0 else ""
    return f"{sign}${pnl:.2f}"


def main():
    from execution.ibkr_broker import get_ibkr_broker

    print("=" * 60)
    print("  MES FUTURES PIPELINE TEST — 10 PAPER TRADES")
    print(f"  {ROUNDS} round trips | {HOLD_SECONDS}s hold | {PAUSE_SECONDS}s between")
    print(f"  Estimated runtime: ~{(ROUNDS * (HOLD_SECONDS + PAUSE_SECONDS)) // 60} minutes")
    print("=" * 60)

    broker = get_ibkr_broker()

    # Try TWS — gracefully fall back to offline paper logging
    connected = broker.connect()
    if connected:
        print("\n[test] Connected to TWS paper account ✓")
    else:
        print("\n[test] TWS not available — using offline paper mode (still logs to DB) ✓")

    price = broker.get_price("MES")
    if price <= 0:
        print("[test] ERROR: Could not get MES price. Check yfinance / internet.")
        sys.exit(1)

    print(f"[test] MES price: {price:.2f}\n")

    trades_done = 0
    total_pnl = 0.0

    for i in range(ROUNDS):
        direction = "LONG" if i % 2 == 0 else "SHORT"
        entry_num = i * 2 + 1
        exit_num = i * 2 + 2

        price = broker.get_price("MES")
        print(f"─── Round {i + 1}/{ROUNDS}  ({direction}) ───────────────────────────────")

        if direction == "LONG":
            stop = round(price - 6.0, 2)
            target = round(price + 12.0, 2)
            print(
                f"[{entry_num:02d}/10] BUY  1 MES @ {price:.2f}  "
                f"SL={stop}  TP={target}"
            )
            broker.buy_mes(
                qty=1,
                stop_price=stop,
                target_price=target,
                reason="pipeline_test",
                strategy="futures_scalper",
            )
            trades_done += 1

            print(f"      Holding {HOLD_SECONDS}s...")
            time.sleep(HOLD_SECONDS)

            exit_price = broker.get_price("MES")
            pnl = (exit_price - price) * 1 * 5 - 0.94  # $5/pt, $0.94 RT commission
            total_pnl += pnl
            print(
                f"[{exit_num:02d}/10] SELL 1 MES @ {exit_price:.2f}  "
                f"P&L: {_fmt_pnl(pnl)}"
            )
            broker.sell_mes(
                qty=1,
                reason="pipeline_test_exit",
                entry_price=price,
                strategy="futures_scalper",
            )
            trades_done += 1

        else:
            stop = round(price + 6.0, 2)
            target = round(price - 12.0, 2)
            print(
                f"[{entry_num:02d}/10] SHORT 1 MES @ {price:.2f}  "
                f"SL={stop}  TP={target}"
            )
            broker.short_mes(
                qty=1,
                stop_price=stop,
                target_price=target,
                reason="pipeline_test",
                strategy="futures_scalper",
            )
            trades_done += 1

            print(f"      Holding {HOLD_SECONDS}s...")
            time.sleep(HOLD_SECONDS)

            exit_price = broker.get_price("MES")
            pnl = (price - exit_price) * 1 * 5 - 0.94
            total_pnl += pnl
            print(
                f"[{exit_num:02d}/10] COVER 1 MES @ {exit_price:.2f}  "
                f"P&L: {_fmt_pnl(pnl)}"
            )
            broker.cover_mes(
                qty=1,
                reason="pipeline_test_exit",
                entry_price=price,
                strategy="futures_scalper",
            )
            trades_done += 1

        if i < ROUNDS - 1:
            print(f"      Pausing {PAUSE_SECONDS}s before next trade...\n")
            time.sleep(PAUSE_SECONDS)

    print("\n" + "=" * 60)
    print(f"  DONE. {trades_done}/10 trades logged.")
    print(f"  Session P&L (this script): {_fmt_pnl(total_pnl)}")
    print("  Check dashboard FUTURES tab for results.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[test] Interrupted by user.")
