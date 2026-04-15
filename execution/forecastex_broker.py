"""
execution/forecastex_broker.py — IBKR ForecastEx event-contract execution.

ForecastEx (IBKR) is a prediction-market exchange.  Event contracts are
represented in the TWS API as options with these fixed fields:
  secType  = "OPT"
  exchange = "FORECASTX"
  currency = "USD"
  right    = "C" (YES side)  or  "P" (NO side)
  strike   = the settlement threshold (e.g. CPI >= 3.0%)
  lastTradeDateOrContractMonth = expiry (YYYYMMDD)

CRITICAL constraints:
  - ForecastEx contracts cannot be sold short.
  - To FLATTEN a YES position: buy the matching NO (Right="P") contract.
  - To FLATTEN a NO  position: buy the matching YES (Right="C") contract.
  - All orders are BUY only.
  - Pricing substrate: bid/ask/midpoint — never last/trade prints.
  - No commission (ForecastEx = zero fee).

TWS must be running on the standard ports (7497 paper / 7496 live).
Uses a separate IBKR client ID (default 3) to avoid collisions with the
MES broker (client ID 2).

Architecture mirrors ibkr_broker.py: one persistent asyncio event loop on
a daemon thread.  All ib_insync calls are submitted via
asyncio.run_coroutine_threadsafe() and block the calling thread.
"""

import asyncio
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

# eventkit (ib_insync dep) needs a loop at import time
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PAPER_TRADING
from logging_db.trade_logger import log_event, log_trade

# ── Connection constants ───────────────────────────────────────────────────────
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))  # 7497=paper, 7496=live
FORECASTX_CLIENT_ID = int(
    os.getenv("FORECASTX_CLIENT_ID", "3")
)  # avoids collision with MES (ID 2)

# Zero commission on ForecastEx
FORECASTX_FEE_PER_CONTRACT = 0.0

# Known IND conIds confirmed live on FORECASTX (2026-04-15).
# Used to persist stubs when the IND pass times out or OPT layer is unavailable.
KNOWN_FORECASTX_CONIDS: dict[str, int] = {
    "CPI": 573031126,
    "CPIY": 712856682,
    "CPIC": 727520252,
    "DISSN": 806285268,
    "DISSA": 804725704,
}

# Economic event underlier symbols to scan during discovery.
# These are the ACTUAL IBKR FORECASTX IND symbols confirmed via reqMatchingSymbols.
# FRED/FRED-style codes (CPIAUCSL, UNRATE, PAYEMS) do NOT exist on FORECASTX.
# Confirmed live: CPI=573031126, CPIY=712856682, CPIC=727520252,
#                 DISSN=806285268, DISSA=804725704
ECONOMIC_UNDERLIERS: list[str] = [
    "CPI",  # US CPI All Items (confirmed IND on FORECASTX)
    "CPIY",  # US CPI Year-over-Year (confirmed IND on FORECASTX)
    "CPIC",  # US Core CPI (confirmed IND on FORECASTX)
    "DISSN",  # Number of Dissenting FOMC Members (confirmed IND on FORECASTX)
    "DISSA",  # Any FOMC Members Dissent (confirmed IND on FORECASTX)
    "PCE",  # PCE Price Index (short form)
    "NFP",  # Nonfarm Payrolls
    "GDP",  # Real GDP
    "PPI",  # Producer Price Index
    "UR",  # Unemployment Rate (IBKR short form)
    "RETAIL",  # Retail Sales
    "FOMC",  # FOMC rate decision
]

# Markets to EXCLUDE from v1 (non-economic)
EXCLUDED_CATEGORIES: list[str] = [
    "sports",
    "politics",
    "entertainment",
    "celebrity",
    "weather",
    "award",
    "election",
    "novelty",
]


def _is_economic_market(symbol: str, name: str, category: str = "") -> bool:
    """Return True if this market is in-scope for v1 economic-only trading."""
    category_lower = category.lower()
    name_lower = name.lower()
    symbol_lower = symbol.lower()

    # Hard exclusions
    for excl in EXCLUDED_CATEGORIES:
        if excl in category_lower or excl in name_lower:
            return False

    # Must match at least one economic keyword
    econ_keywords = [
        "cpi",
        "inflation",
        "fed",
        "fomc",
        "rate",
        "rates",
        "payroll",
        "nonfarm",
        "unemployment",
        "gdp",
        "pce",
        "retail",
        "housing",
        "consumer",
        "ppi",
        "production",
        "jobs",
        "employment",
        "macro",
        "economic",
        "economy",
    ]
    for kw in econ_keywords:
        if kw in name_lower or kw in symbol_lower or kw in category_lower:
            return True

    return False


class ForecastExBroker:
    """
    Interactive Brokers broker for ForecastEx event contracts.

    Manages discovery, quote fetching, order placement, and position tracking
    for YES/NO event contracts on the FORECASTX exchange.

    Position dict keys (stored in _open_positions keyed by local_symbol+right):
      local_symbol, right, strike, last_trade_at, conid,
      qty, entry_price, side ('YES'|'NO'), order_id, entered_at
    """

    def __init__(self) -> None:
        self._ib = None
        self._connected = False
        self._open_positions: dict[str, dict] = {}  # key = f"{local_symbol}_{right}"
        self._lock = threading.Lock()

        # Persistent asyncio event loop (same pattern as IBKRBroker)
        self._loop = asyncio.new_event_loop()

        def _start_loop(loop: asyncio.AbstractEventLoop) -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self._loop_thread = threading.Thread(
            target=_start_loop,
            args=(self._loop,),
            daemon=True,
            name="forecastex-event-loop",
        )
        self._loop_thread.start()

    # ── Event loop bridge ──────────────────────────────────────────────────────

    def _run(self, coro, timeout: float = 20.0):
        """Submit coroutine to persistent loop; block calling thread until done."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Connection ─────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Connect to TWS using FORECASTX_CLIENT_ID. Returns True on success."""
        from ib_insync import IB

        if self._ib is not None:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None

        self._ib = IB()
        try:
            self._run(
                self._ib.connectAsync(
                    IBKR_HOST, IBKR_PORT, clientId=FORECASTX_CLIENT_ID
                ),
                timeout=15,
            )
            self._connected = self._ib.isConnected()
            mode = "PAPER" if PAPER_TRADING else "LIVE"
            if self._connected:
                acct = (
                    self._ib.managedAccounts()[0]
                    if self._ib.managedAccounts()
                    else "unknown"
                )
                print(
                    f"[ForecastExBroker] Connected ({mode}) account={acct} "
                    f"port={IBKR_PORT} clientId={FORECASTX_CLIENT_ID} ✅"
                )
                log_event(
                    "INFO", "ForecastExBroker", f"Connected ({mode}) account={acct}"
                )
                self._sync_positions()
            else:
                print("[ForecastExBroker] ⚠️ Could not connect to TWS")
            return self._connected
        except Exception as e:
            print(f"[ForecastExBroker] Connection error: {e}")
            log_event("ERROR", "ForecastExBroker", f"Connection failed: {e}")
            self._connected = False
            return False

    def is_connected(self) -> bool:
        if self._ib is None:
            return False
        try:
            return self._ib.isConnected()
        except Exception:
            return False

    def _sync_positions(self) -> None:
        """Sync any open ForecastEx positions from TWS into local state."""
        if not self.is_connected():
            return
        try:
            for pos in self._ib.positions():
                try:
                    if pos.contract.exchange == "FORECASTX" and pos.position > 0:
                        key = f"{pos.contract.localSymbol}_{pos.contract.right}"
                        side = "YES" if pos.contract.right == "C" else "NO"
                        with self._lock:
                            self._open_positions[key] = {
                                "local_symbol": pos.contract.localSymbol,
                                "right": pos.contract.right,
                                "strike": pos.contract.strike,
                                "last_trade_at": pos.contract.lastTradeDateOrContractMonth,
                                "conid": pos.contract.conId,
                                "qty": int(pos.position),
                                "entry_price": pos.avgCost,
                                "side": side,
                                "order_id": "SYNCED",
                                "entered_at": datetime.now(timezone.utc).isoformat(),
                            }
                            print(
                                f"[ForecastExBroker] Synced {side} {int(pos.position)} "
                                f"{pos.contract.localSymbol}"
                            )
                except Exception:
                    pass
        except Exception as e:
            log_event("WARN", "ForecastExBroker", f"Position sync error: {e}")

    # ── Market discovery ───────────────────────────────────────────────────────

    async def _discover_async(self, underlier: str) -> list[dict]:
        """
        Discover event contracts for one FORECASTX underlier.

        Two-pass approach (confirmed via live TWS probing 2026-04-15):
          Pass 1 — IND: find the underlying IND contract on FORECASTX to confirm
                   the symbol exists and get its conId + long_name.
                   IBKR FORECASTX uses short symbols (CPI, CPIY, CPIC, DISSA,
                   DISSN) NOT FRED codes (CPIAUCSL, UNRATE, PAYEMS).
          Pass 2 — OPT: request event contracts (YES/NO binary options) on the
                   confirmed IND underlier.  If the account is not enrolled in
                   ForecastEx event-contract trading, this will return empty or
                   hang — handled with a short timeout.
        """
        from ib_insync import Contract

        # Pass 1: confirm IND underlier exists (10s timeout — DISSN/DISSA/GDP can hang)
        ind_contract = Contract(
            secType="IND",
            symbol=underlier,
            exchange="FORECASTX",
            currency="USD",
        )
        ind_conid = KNOWN_FORECASTX_CONIDS.get(underlier)
        long_name = ""
        category = ""
        try:
            ind_details = await asyncio.wait_for(
                self._ib.reqContractDetailsAsync(ind_contract), timeout=10
            )
            if not ind_details:
                # Symbol not on FORECASTX this session.  If we have a known conId,
                # fall through and try OPT anyway; otherwise bail.
                if not ind_conid:
                    return []
            else:
                ind_info = ind_details[0]
                long_name = ind_info.longName or ""
                category = ind_info.category or ""
                ind_conid = ind_info.contract.conId
        except Exception as e:
            log_event(
                "WARN", "ForecastExBroker", f"IND discovery error for {underlier}: {e}"
            )
            # If we have a confirmed conId from the known map, still attempt OPT pass.
            if not ind_conid:
                return []

        # Pass 2: get OPT event contracts (YES=Right C, NO=Right P)
        # 12s timeout — OPT layer hangs when account not enrolled for event trading.
        opt_contract = Contract(
            secType="OPT",
            symbol=underlier,
            exchange="FORECASTX",
            currency="USD",
        )

        def _stub() -> list[dict]:
            """Return an IND-only stub when OPT layer is unavailable."""
            log_event(
                "INFO",
                "ForecastExBroker",
                f"IND {underlier} (conId={ind_conid}) found but OPT layer unavailable "
                f"— account may need ForecastEx enrollment",
            )
            return [
                {
                    "underlier": underlier,
                    "und_conid": ind_conid,
                    "long_name": long_name,
                    "category": category,
                    "stub_only": True,
                    "opt_unavailable": True,
                    "local_symbol": underlier,
                    "conid": None,
                    "right": None,
                    "strike": None,
                    "last_trade_at": None,
                    "exchange": "FORECASTX",
                    "currency": "USD",
                }
            ]

        try:
            details = await asyncio.wait_for(
                self._ib.reqContractDetailsAsync(opt_contract), timeout=12
            )
        except Exception as e:
            log_event(
                "WARN", "ForecastExBroker", f"OPT discovery error for {underlier}: {e}"
            )
            # Account may not have ForecastEx event trading enabled.
            # Return a stub so callers can persist the underlier visibility to DB.
            return _stub()

        results = []
        now_str = datetime.now(timezone.utc).date().isoformat()
        for d in details:
            c = d.contract
            # Skip expired contracts
            expiry = c.lastTradeDateOrContractMonth or ""
            if expiry and expiry < now_str.replace("-", ""):
                continue
            results.append(
                {
                    "underlier": underlier,
                    "local_symbol": c.localSymbol,
                    "conid": c.conId,
                    "right": c.right,  # "C" = YES, "P" = NO
                    "strike": c.strike,
                    "last_trade_at": expiry,
                    "exchange": c.exchange,
                    "currency": c.currency,
                    "long_name": d.longName or long_name,
                    "category": d.category or category,
                    "und_conid": ind_conid,
                }
            )
        return results

    def discover_markets(
        self,
        category_filter: Optional[str] = None,
        underliers: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Discover active ForecastEx event contracts for economic markets.

        Returns list of contract dicts:
          {underlier, local_symbol, conid, right, strike, last_trade_at,
           long_name, category, side ('YES'|'NO')}

        Filters to economic markets only (v1 scope).
        """
        if not self.is_connected():
            log_event(
                "WARN", "ForecastExBroker", "discover_markets called while disconnected"
            )
            return []

        targets = underliers or ECONOMIC_UNDERLIERS
        all_contracts = []
        for underlier in targets:
            try:
                contracts = self._run(self._discover_async(underlier), timeout=30)
                all_contracts.extend(contracts)
            except Exception as e:
                log_event(
                    "WARN",
                    "ForecastExBroker",
                    f"Discovery timeout for {underlier}: {e}",
                )
                # If this underlier has a confirmed conId, emit a stub so the DB
                # records that the IND is visible even though OPT timed out.
                if underlier in KNOWN_FORECASTX_CONIDS:
                    all_contracts.append(
                        {
                            "underlier": underlier,
                            "und_conid": KNOWN_FORECASTX_CONIDS[underlier],
                            "long_name": "",
                            "category": "",
                            "stub_only": True,
                            "opt_unavailable": True,
                            "local_symbol": underlier,
                            "conid": None,
                            "right": None,
                            "strike": None,
                            "last_trade_at": None,
                            "exchange": "FORECASTX",
                            "currency": "USD",
                        }
                    )

        # Label YES/NO and apply economic filter
        results = []
        for c in all_contracts:
            # Known confirmed underliers bypass the economic keyword filter —
            # DISSN/DISSA have short symbols that don't match any keyword when
            # long_name/category are empty (stub case).
            is_known = c.get("underlier") in KNOWN_FORECASTX_CONIDS
            if not is_known and not _is_economic_market(
                c["underlier"], c.get("long_name", ""), c.get("category", "")
            ):
                continue
            if (
                category_filter
                and category_filter.lower() not in c.get("category", "").lower()
            ):
                continue
            # Stub-only entries don't have a right/side — pass through without labelling
            if not c.get("stub_only"):
                c["side"] = "YES" if c["right"] == "C" else "NO"
            results.append(c)

        log_event(
            "INFO",
            "ForecastExBroker",
            f"Discovery found {len(results)} contracts across {len(targets)} underliers",
        )
        return results

    # ── Quote fetching ─────────────────────────────────────────────────────────

    async def _get_quote_async(self, conid: int, local_symbol: str = "") -> dict:
        """Fetch bid/ask/mid from TWS market data for a ForecastEx contract.

        ForecastEx MUST use bid/ask — trade prints / 'last' are unreliable
        on thin prediction markets.
        """
        from ib_insync import Contract

        contract = Contract(conId=conid, exchange="FORECASTX")
        try:
            await self._ib.qualifyContractsAsync(contract)
        except Exception:
            pass  # conId may already be qualified

        ticker = self._ib.reqMktData(
            contract, "", snapshot=False, regulatorySnapshot=False
        )
        await asyncio.sleep(1.5)  # allow data to arrive

        bid = float(ticker.bid) if ticker.bid and ticker.bid > 0 else None
        ask = float(ticker.ask) if ticker.ask and ticker.ask > 0 else None
        bsz = float(ticker.bidSize) if ticker.bidSize else None
        asz = float(ticker.askSize) if ticker.askSize else None

        self._ib.cancelMktData(contract)

        mid = round((bid + ask) / 2.0, 4) if bid and ask else None
        spread = round(ask - bid, 4) if bid and ask else None

        return {
            "conid": conid,
            "local_symbol": local_symbol,
            "bid": bid,
            "ask": ask,
            "bid_size": bsz,
            "ask_size": asz,
            "mid": mid,
            "spread": spread,
            "implied_prob": mid,  # mid IS the implied probability [0,1]
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def get_quote(self, conid: int, local_symbol: str = "") -> dict:
        """
        Return bid/ask/mid quote dict for a ForecastEx contract.
        All None fields mean TWS returned no data.
        """
        if not self.is_connected():
            return {
                "conid": conid,
                "local_symbol": local_symbol,
                "bid": None,
                "ask": None,
                "mid": None,
                "spread": None,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        try:
            return self._run(self._get_quote_async(conid, local_symbol), timeout=10)
        except Exception as e:
            log_event("WARN", "ForecastExBroker", f"get_quote error conid={conid}: {e}")
            return {
                "conid": conid,
                "local_symbol": local_symbol,
                "bid": None,
                "ask": None,
                "mid": None,
                "spread": None,
                "ts": datetime.now(timezone.utc).isoformat(),
            }

    def get_quotes_batch(self, contracts: list[dict]) -> list[dict]:
        """Fetch quotes for multiple contracts. Each dict must have 'conid' and 'local_symbol'."""
        quotes = []
        for c in contracts:
            q = self.get_quote(c["conid"], c.get("local_symbol", ""))
            q["right"] = c.get("right", "")
            q["strike"] = c.get("strike", 0.0)
            quotes.append(q)
        return quotes

    # ── Order placement ────────────────────────────────────────────────────────

    async def _place_limit_buy_async(
        self,
        conid: int,
        local_symbol: str,
        right: str,
        strike: float,
        last_trade_at: str,
        qty: int,
        limit_price: float,
    ) -> str:
        """Place a limit BUY order (ForecastEx only supports buying, not shorting)."""
        from ib_insync import Contract, LimitOrder

        contract = Contract(
            secType="OPT",
            conId=conid,
            exchange="FORECASTX",
            currency="USD",
            right=right,
            strike=strike,
            lastTradeDateOrContractMonth=last_trade_at,
        )
        try:
            await self._ib.qualifyContractsAsync(contract)
        except Exception:
            pass

        order = LimitOrder("BUY", qty, round(limit_price, 2))
        order.outsideRth = True  # ForecastEx is always open
        trade = self._ib.placeOrder(contract, order)
        return str(trade.order.orderId)

    def place_buy_order(
        self,
        contract_dict: dict,
        qty: int,
        limit_price: float,
        reason: str = "signal",
        strategy: str = "forecast_event",
    ) -> dict:
        """
        Buy `qty` contracts of a ForecastEx event contract.

        contract_dict must contain: conid, local_symbol, right, strike, last_trade_at.
        Returns {order_id, price, side, qty} or raises on fatal error.

        Side is "YES" if right="C", "NO" if right="P".
        """
        conid = contract_dict["conid"]
        local_symbol = contract_dict["local_symbol"]
        right = contract_dict["right"]
        strike = contract_dict["strike"]
        last_trade = contract_dict.get("last_trade_at", "")
        side = "YES" if right == "C" else "NO"

        if qty <= 0:
            raise ValueError(f"qty must be positive for ForecastEx buy (got {qty})")

        if self.is_connected():
            try:
                order_id = self._run(
                    self._place_limit_buy_async(
                        conid, local_symbol, right, strike, last_trade, qty, limit_price
                    ),
                    timeout=15,
                )
                print(
                    f"[ForecastExBroker] BUY {qty} {local_symbol} ({side}) "
                    f"@ {limit_price:.4f} | reason={reason}"
                )
            except Exception as e:
                log_event("ERROR", "ForecastExBroker", f"place_buy_order error: {e}")
                order_id = f"FX_ERR_{uuid.uuid4().hex[:8]}"
        else:
            order_id = f"FX_PAPER_{uuid.uuid4().hex[:8]}"
            print(
                f"[ForecastExBroker] ⚠️ Not connected — paper-logging BUY {qty} "
                f"{local_symbol} ({side}) @ {limit_price:.4f}"
            )

        position_key = f"{local_symbol}_{right}"
        with self._lock:
            existing = self._open_positions.get(position_key, {})
            self._open_positions[position_key] = {
                "local_symbol": local_symbol,
                "right": right,
                "strike": strike,
                "last_trade_at": last_trade,
                "conid": conid,
                "qty": existing.get("qty", 0) + qty,
                "entry_price": limit_price,
                "side": side,
                "order_id": order_id,
                "entered_at": datetime.now(timezone.utc).isoformat(),
            }

        log_trade(
            strategy=strategy,
            broker="forecastex" if not PAPER_TRADING else "forecastex_paper",
            symbol=local_symbol,
            action="BUY",
            order_type="Limit",
            qty=qty,
            price=limit_price,
            fee_usd=FORECASTX_FEE_PER_CONTRACT * qty,
            paper=PAPER_TRADING,
            order_id=order_id,
            notes=(
                f"side={side} strike={strike} right={right} "
                f"expiry={last_trade} reason={reason}"
            ),
        )
        return {"order_id": order_id, "price": limit_price, "side": side, "qty": qty}

    def flatten_position(
        self,
        local_symbol: str,
        right: str,
        qty: int,
        strategy: str = "forecast_event",
        reason: str = "exit",
    ) -> dict:
        """
        Flatten a ForecastEx position by buying the OPPOSITE side.

        ForecastEx contracts pay $1 at resolution.  Buying the opposite side
        creates an offsetting payoff (YES + NO = $1 at resolution; buying NO
        against an open YES effectively locks in a known combined payout,
        removing market exposure).

        right="C" (YES) → buy right="P" (NO) to flatten
        right="P" (NO)  → buy right="C" (YES) to flatten
        """
        opposite_right = "P" if right == "C" else "C"
        opposite_side = "NO" if right == "C" else "YES"
        position_key = f"{local_symbol}_{right}"

        pos = self._open_positions.get(position_key)
        if not pos:
            log_event(
                "WARN",
                "ForecastExBroker",
                f"flatten_position: no open position for {local_symbol}_{right}",
            )
            return {"error": "no_open_position"}

        flatten_qty = min(qty, pos.get("qty", 0))
        if flatten_qty <= 0:
            return {"error": "nothing_to_flatten"}

        # Build opposite contract dict; conid may differ — use local_symbol with opposite right
        opposite_contract = {
            "conid": pos.get("conid", 0),  # Will re-qualify in broker
            "local_symbol": local_symbol,
            "right": opposite_right,
            "strike": pos.get("strike", 0.0),
            "last_trade_at": pos.get("last_trade_at", ""),
        }

        # Get current ask of opposite side to flatten at market
        quote = self.get_quote(opposite_contract["conid"], local_symbol)
        limit_price = quote.get("ask") or 0.99  # worst case pay $0.99 to flatten

        print(
            f"[ForecastExBroker] FLATTEN {local_symbol} {right} by buying "
            f"{opposite_side} × {flatten_qty} @ {limit_price:.4f} | reason={reason}"
        )

        result = self.place_buy_order(
            opposite_contract,
            flatten_qty,
            limit_price,
            reason=reason,
            strategy=strategy,
        )

        # Update local position
        with self._lock:
            remaining = pos.get("qty", 0) - flatten_qty
            if remaining <= 0:
                self._open_positions.pop(position_key, None)
            else:
                self._open_positions[position_key]["qty"] = remaining

        return {
            **result,
            "flattened_qty": flatten_qty,
            "remaining_qty": max(0, pos.get("qty", 0) - flatten_qty),
        }

    # ── Position & account ─────────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """Return all open ForecastEx positions (local state)."""
        with self._lock:
            return list(self._open_positions.values())

    def get_position(self, local_symbol: str, right: str) -> Optional[dict]:
        key = f"{local_symbol}_{right}"
        with self._lock:
            return self._open_positions.get(key)

    def get_open_position_count(self) -> int:
        with self._lock:
            return len(self._open_positions)

    def get_account_balance(self) -> float:
        """Return IBKR account NetLiquidation in USD. Returns 0.0 if not connected."""
        if not self.is_connected():
            return 0.0
        try:
            for v in self._ib.accountValues():
                if v.tag == "NetLiquidation" and v.currency == "USD":
                    return float(v.value)
        except Exception:
            pass
        return 0.0

    def disconnect(self) -> None:
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            self._connected = False


# ── Singleton ──────────────────────────────────────────────────────────────────
_forecastex_broker: Optional[ForecastExBroker] = None


def get_forecastex_broker() -> ForecastExBroker:
    global _forecastex_broker
    if _forecastex_broker is None:
        _forecastex_broker = ForecastExBroker()
    return _forecastex_broker
