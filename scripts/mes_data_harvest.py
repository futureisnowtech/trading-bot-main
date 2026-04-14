"""
scripts/mes_data_harvest.py — Open 20 MES paper positions and let SL/TP close them.

Uses client ID 3 (bot uses 2) so it runs alongside the live bot without conflict.
Each position is monitored in a tight 3-second loop — SL/TP trigger at market.
Mix of LONG/SHORT based on current price direction vs entry.
Trades are logged to the standard trades DB so the learning loop can use them.

Designed to collect real lifecycle data (entry → SL/TP hit → close → P&L) today,
rather than waiting for organic OR breakout signals which only fire a few times/week.
"""

import os
import sys
import time
import uuid
import asyncio
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH, PAPER_TRADING
from logging_db.trade_logger import log_trade, log_event

# ── Broker setup (client ID 3 so it coexists with running bot on ID 2) ───────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from ib_insync import IB, Future, MarketOrder

IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))
CLIENT_ID = 4  # intentionally different from bot (2) and previous harvest run (3)
MES_POINT_VALUE = 5.00
IBKR_COMMISSION = 0.47

SEP = "=" * 60

# SL/TP parameters for data harvest
# Tight enough to resolve within minutes during market hours
SL_POINTS = 3.0  # 3 points = $15/contract stop
TP_POINTS = 6.0  # 6 points = $30/contract target  (2R)
MAX_HOLD_S = 120  # force-close after 2 min if neither hit (avoids hanging)
N_TRADES = 20
QTY = 1  # 1 contract per trade for the harvest


def _get_contract():
    from ib_insync import Future
    import os as _os

    expiry = _os.getenv("MES_EXPIRY", "20260619")
    year_str = expiry[2:4]
    month_code = {
        "01": "F",
        "02": "G",
        "03": "H",
        "04": "J",
        "05": "K",
        "06": "M",
        "07": "N",
        "08": "Q",
        "09": "U",
        "10": "V",
        "11": "X",
        "12": "Z",
    }
    local_sym = f"MES{month_code.get(expiry[4:6], 'M')}{year_str}"
    return Future(localSymbol=local_sym, exchange="CME", currency="USD", multiplier="5")


class HarvestBroker:
    """Lightweight IBKR wrapper using its own event loop (client ID 3)."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()

        def _start(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        threading.Thread(
            target=_start, args=(self._loop,), daemon=True, name="harvest-loop"
        ).start()
        self._ib = IB()

    def _run(self, coro, timeout=15.0):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def connect(self) -> bool:
        try:
            self._run(self._ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=CLIENT_ID))
            ok = self._ib.isConnected()
            if ok:
                acct = (
                    self._ib.managedAccounts()[0] if self._ib.managedAccounts() else "?"
                )
                print(f"[harvest] Connected — account={acct} clientId={CLIENT_ID}")
            return ok
        except Exception as e:
            print(f"[harvest] Connect failed: {e}")
            return False

    def price(self) -> float:
        async def _fetch():
            c = _get_contract()
            await self._ib.qualifyContractsAsync(c)
            t = self._ib.reqMktData(c, "", False, False)
            await asyncio.sleep(2.5)  # give TWS time to push a snapshot
            p = None
            for attr in ("last", "close", "bid", "ask"):
                v = getattr(t, attr, None)
                if v and v > 0:
                    p = float(v)
                    break
            self._ib.cancelMktData(c)
            return p

        try:
            p = self._run(_fetch(), timeout=12)
            if p and p > 0:
                return p
        except Exception:
            pass

        # yfinance fallback — works during market hours
        try:
            import yfinance as yf

            hist = yf.Ticker("MES=F").history(period="1d", interval="1m")
            if hist is not None and not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            pass
        try:
            import yfinance as yf

            hist = yf.download(
                "ES=F", period="1d", interval="5m", auto_adjust=True, progress=False
            )
            if hist is not None and not hist.empty:
                cols = [
                    c[0].lower() if isinstance(c, tuple) else c.lower()
                    for c in hist.columns
                ]
                hist.columns = cols
                return float(hist["close"].iloc[-1])
        except Exception:
            pass
        return 0.0

    def _place_market(self, action: str) -> int:
        async def _place():
            c = _get_contract()
            await self._ib.qualifyContractsAsync(c)
            # Cancel any open orders for MES first
            for t in self._ib.openTrades():
                try:
                    if t.contract.localSymbol and "MES" in t.contract.localSymbol:
                        self._ib.cancelOrder(t.order)
                except Exception:
                    pass
            await asyncio.sleep(0.2)
            trade = self._ib.placeOrder(c, MarketOrder(action, QTY))
            return trade.order.orderId

        return self._run(_place(), timeout=10)

    def open_long(self, entry: float) -> dict:
        oid = self._place_market("BUY")
        return {
            "side": "LONG",
            "entry": entry,
            "order_id": oid,
            "stop": round(entry - SL_POINTS, 2),
            "target": round(entry + TP_POINTS, 2),
        }

    def open_short(self, entry: float) -> dict:
        oid = self._place_market("SELL")
        return {
            "side": "SHORT",
            "entry": entry,
            "order_id": oid,
            "stop": round(entry + SL_POINTS, 2),
            "target": round(entry - TP_POINTS, 2),
        }

    def close_long(self) -> int:
        return self._place_market("SELL")

    def close_short(self) -> int:
        return self._place_market("BUY")

    def disconnect(self):
        try:
            self._ib.disconnect()
        except Exception:
            pass


def _log(pos: dict, exit_price: float, reason: str) -> float:
    """Log open + close to trades DB, return P&L."""
    side = pos["side"]
    entry = pos["entry"]
    if side == "LONG":
        pnl = (exit_price - entry) * QTY * MES_POINT_VALUE
    else:
        pnl = (entry - exit_price) * QTY * MES_POINT_VALUE
    pnl -= IBKR_COMMISSION * QTY * 2  # round-trip fees

    action_open = "BUY" if side == "LONG" else "SHORT"
    action_close = "SELL" if side == "LONG" else "COVER"

    log_trade(
        strategy="mes_data_harvest",
        broker="ibkr_paper",
        symbol="MES",
        action=action_open,
        order_type="Market",
        qty=QTY,
        price=entry,
        fee_usd=IBKR_COMMISSION,
        paper=True,
        order_id=str(pos["order_id"]),
        notes=f"harvest SL={pos['stop']} TP={pos['target']}",
    )

    log_trade(
        strategy="mes_data_harvest",
        broker="ibkr_paper",
        symbol="MES",
        action=action_close,
        order_type="Market",
        qty=QTY,
        price=exit_price,
        fee_usd=IBKR_COMMISSION,
        pnl_usd=pnl,
        paper=True,
        order_id=f"IBKR_{uuid.uuid4().hex[:8]}",
        notes=f"reason={reason}",
    )
    return pnl


def run_trade(broker: HarvestBroker, trade_num: int, side: str) -> dict:
    """Open one position, monitor it, close on SL/TP/timeout."""
    entry = broker.price()
    if not entry or entry <= 0:
        return {"trade": trade_num, "side": side, "error": "price fetch failed"}

    print(
        f"\n  [{trade_num:02d}/{N_TRADES}] {side}  entry≈{entry:.2f}  "
        f"SL={entry - SL_POINTS:.2f}  TP={entry + TP_POINTS:.2f}"
        if side == "LONG"
        else f"\n  [{trade_num:02d}/{N_TRADES}] {side}  entry≈{entry:.2f}  "
        f"SL={entry + SL_POINTS:.2f}  TP={entry - TP_POINTS:.2f}"
    )

    pos = broker.open_long(entry) if side == "LONG" else broker.open_short(entry)
    time.sleep(0.5)  # let TWS ack the order

    start = time.time()
    exit_price = entry
    reason = "timeout"

    while time.time() - start < MAX_HOLD_S:
        time.sleep(3)
        cur = broker.price()
        if not cur or cur <= 0:
            continue

        if side == "LONG":
            if cur <= pos["stop"]:
                exit_price, reason = cur, "stop_hit"
                break
            elif cur >= pos["target"]:
                exit_price, reason = cur, "target_hit"
                break
        else:
            if cur >= pos["stop"]:
                exit_price, reason = cur, "stop_hit"
                break
            elif cur <= pos["target"]:
                exit_price, reason = cur, "target_hit"
                break

    # Close position
    if side == "LONG":
        broker.close_long()
    else:
        broker.close_short()

    if reason == "timeout":
        cur = broker.price()
        if cur and cur > 0:
            exit_price = cur

    pnl = _log(pos, exit_price, reason)
    result = {
        "trade": trade_num,
        "side": side,
        "entry": entry,
        "exit": exit_price,
        "pnl": round(pnl, 2),
        "reason": reason,
        "order_id": pos["order_id"],
    }
    icon = "✓" if pnl > 0 else "✗" if pnl < 0 else "="
    print(f"     {icon}  exit={exit_price:.2f}  pnl=${pnl:+.2f}  [{reason}]")
    return result


def main():
    print(SEP)
    print(f"  MES DATA HARVEST — {N_TRADES} PAPER POSITIONS")
    print(f"  SL={SL_POINTS}pts  TP={TP_POINTS}pts  MAX_HOLD={MAX_HOLD_S}s  QTY={QTY}")
    print(SEP)

    broker = HarvestBroker()
    if not broker.connect():
        print("[FAIL] Could not connect to TWS. Is it running?")
        sys.exit(1)

    # Seed price for direction check
    seed = broker.price()
    if not seed:
        print("[FAIL] Could not get MES price.")
        sys.exit(1)
    print(f"  MES price: {seed:.2f}\n")

    results = []
    total_pnl = 0.0
    wins = losses = timeouts = 0

    # Alternate LONG/SHORT — gives balanced data regardless of market direction
    for i in range(1, N_TRADES + 1):
        side = "LONG" if i % 2 == 1 else "SHORT"
        r = run_trade(broker, i, side)
        results.append(r)
        if "error" in r:
            print(f"     ERROR: {r['error']}")
            continue
        total_pnl += r["pnl"]
        if r["reason"] == "timeout":
            timeouts += 1
        elif r["pnl"] > 0:
            wins += 1
        else:
            losses += 1
        time.sleep(0.5)  # brief pause between trades

    broker.disconnect()

    print()
    print(SEP)
    print("  HARVEST COMPLETE")
    print(SEP)
    print(f"  Trades:   {N_TRADES}  ({N_TRADES // 2}L + {N_TRADES // 2}S)")
    print(f"  Wins:     {wins}")
    print(f"  Losses:   {losses}")
    print(f"  Timeouts: {timeouts}  (closed at market after {MAX_HOLD_S}s)")
    print(f"  Total P&L: ${total_pnl:+.2f}  (after commissions)")
    print(f"  DB:       {DB_PATH}")
    print()
    print("  Trade log:")
    for r in results:
        if "error" in r:
            print(f"    [{r['trade']:02d}] {r['side']}  ERROR: {r['error']}")
        else:
            icon = "W" if r["pnl"] > 0 else "L" if r["pnl"] < 0 else "-"
            print(
                f"    [{r['trade']:02d}] {r['side']:5s}  "
                f"entry={r['entry']:.2f}  exit={r['exit']:.2f}  "
                f"pnl=${r['pnl']:+.2f}  [{r['reason']}]  {icon}"
            )
    print(SEP)


if __name__ == "__main__":
    main()
