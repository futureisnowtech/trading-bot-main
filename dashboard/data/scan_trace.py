"""
dashboard/data/scan_trace.py
Re-runs the 7 scanner steps for BTC/ETH/SOL/XRP on demand and returns
per-step pass/fail with the actual metric values so the dashboard can show
exactly WHY a symbol was skipped.

Imports scanner internals directly — scanner.py is not modified.
Results are cached for 5 minutes to avoid hammering the APIs.
"""

import sys
import os
import time
from typing import Dict, List

# Ensure project root is on path so we can import scanner
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_CACHE: Dict = {}
_CACHE_TTL = 300  # 5 minutes

CORE_SYMBOLS = ["BTC", "ETH", "SOL", "XRP"]

# Symbol maps: base_asset → (kraken_sym, binance_sym, hl_coin)
_KRAKEN_SYM = {
    "BTC": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "XRP": "PF_XRPUSD",
}
_BINANCE_SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}


def _import_scanner():
    import scanner as sc

    return sc


def _step1_check(sc, base: str, vol_usd: float, price: float) -> Dict:
    """Volume + price gate."""
    ok = vol_usd >= sc._MIN_VOLUME_24H_USD and price > 0
    return {
        "pass": ok,
        "vol_usd": vol_usd,
        "min_vol_usd": sc._MIN_VOLUME_24H_USD,
        "price": price,
        "fail_reason": None
        if ok
        else f"vol ${vol_usd / 1e6:.1f}M < ${sc._MIN_VOLUME_24H_USD / 1e6:.1f}M required"
        if vol_usd < sc._MIN_VOLUME_24H_USD
        else "price ≤ 0",
    }


def _step2_check(sc, c: Dict) -> Dict:
    """Run _evaluate_one_symbol; capture all sub-filter metrics."""
    try:
        sym = c["symbol"]
        exchange = c["exchange"]
        price = c["price"]
        fund_ann = c.get("funding_rate", 0.0)

        if exchange == "kraken":
            klines = sc._kraken_klines(sym, "15m", 65)
        elif exchange == "hyperliquid":
            klines = sc._hl_klines(sym, "15m", 65)
        else:
            klines = sc._binance_klines(sym, "15m", 65)

        if len(klines) < 22:
            return {"pass": False, "fail_reason": f"only {len(klines)} bars (need 22)"}

        opens = [k[0] for k in klines]
        highs = [k[1] for k in klines]
        lows = [k[2] for k in klines]
        closes = [k[3] for k in klines]
        vols = [k[4] for k in klines]

        if len(vols) >= 2 and vols[-2] > 0 and vols[-1] / vols[-2] < 0.10:
            opens, highs, lows, closes, vols = (
                opens[:-1],
                highs[:-1],
                lows[:-1],
                closes[:-1],
                vols[:-1],
            )

        if len(closes) < 20:
            return {"pass": False, "fail_reason": "< 20 usable bars"}

        adx = sc._calc_adx(highs, lows, closes, 14)
        vs = sc._calc_vol_spike(vols, 20)
        atr = sc._calc_atr(highs, lows, closes, 14)
        bars_1h = min(4, len(closes) - 1)
        pm_1h = abs(closes[-1] - closes[-bars_1h]) / (closes[-bars_1h] + 1e-9) * 100
        kst_val, kst_sig, kst_bull, kst_bear = sc._calc_kst(closes)
        st_dir, st_up, st_down = sc._calc_supertrend(highs, lows, closes, 10, 3.0)
        vwap = sc._calc_vwap(highs, lows, closes, vols)
        vwap_disp = (closes[-1] - vwap) / (vwap + 1e-9) * 100
        fund_8h = fund_ann / (3 * 365)

        activity = (vs >= sc._MIN_VOL_SPIKE) or (pm_1h >= sc._MIN_PRICE_MOVE_1H)

        fired = {"LONG": set(), "SHORT": set()}
        if activity and adx >= sc._MIN_ADX_MOMENTUM:
            d = "LONG" if closes[-1] > closes[-min(4, len(closes) - 1)] else "SHORT"
            fired[d].add("momentum")
        if adx >= sc._MIN_ADX_MOMENTUM:
            if kst_bull:
                fired["LONG"].add("kst_cross")
            if kst_bear:
                fired["SHORT"].add("kst_cross")
        if st_up:
            fired["LONG"].add("supertrend")
        if st_down:
            fired["SHORT"].add("supertrend")
        if adx < sc._MAX_ADX_RANGING and abs(vwap_disp) >= sc._MIN_VWAP_DISP_PCT:
            if vwap_disp < 0:
                fired["LONG"].add("ranging_mr")
            else:
                fired["SHORT"].add("ranging_mr")
        if abs(fund_8h) >= sc._MIN_FUNDING_COLLECT:
            if fund_8h < 0:
                fired["LONG"].add("funding_collect")
            else:
                fired["SHORT"].add("funding_collect")

        any_fired = bool(fired["LONG"] or fired["SHORT"])

        # Build sub-filter details for display
        subs = {
            "A_momentum": {
                "label": "A. Momentum",
                "pass": bool({"momentum"} & (fired["LONG"] | fired["SHORT"])),
                "vol_spike": round(vs, 2),
                "vol_spike_need": sc._MIN_VOL_SPIKE,
                "pm_1h_pct": round(pm_1h, 3),
                "pm_1h_need": sc._MIN_PRICE_MOVE_1H,
                "adx": round(adx, 1),
                "adx_need": sc._MIN_ADX_MOMENTUM,
                "activity": activity,
            },
            "B_kst": {
                "label": "B. KST cross",
                "pass": bool({"kst_cross"} & (fired["LONG"] | fired["SHORT"])),
                "kst_bull": kst_bull,
                "kst_bear": kst_bear,
                "adx": round(adx, 1),
                "adx_need": sc._MIN_ADX_MOMENTUM,
            },
            "C_supertrend": {
                "label": "C. SuperTrend flip",
                "pass": st_up or st_down,
                "st_up": st_up,
                "st_down": st_down,
                "st_dir": st_dir,
            },
            "D_ranging": {
                "label": "D. Ranging MR",
                "pass": bool({"ranging_mr"} & (fired["LONG"] | fired["SHORT"])),
                "adx": round(adx, 1),
                "adx_max": sc._MAX_ADX_RANGING,
                "vwap_disp_pct": round(vwap_disp, 3),
                "vwap_disp_need": sc._MIN_VWAP_DISP_PCT,
            },
            "E_funding": {
                "label": "E. Funding collect",
                "pass": bool({"funding_collect"} & (fired["LONG"] | fired["SHORT"])),
                "fund_8h_pct": round(fund_8h * 100, 5),
                "fund_8h_need_pct": round(sc._MIN_FUNDING_COLLECT * 100, 5),
            },
        }

        directions = []
        for d, s in fired.items():
            if s:
                directions.append(f"{d}: {', '.join(sorted(s))}")

        fail_reasons = []
        if not activity:
            fail_reasons.append(
                f"no activity (vol_spike={vs:.2f}<{sc._MIN_VOL_SPIKE}, pm_1h={pm_1h:.3f}%<{sc._MIN_PRICE_MOVE_1H}%)"
            )
        if adx < sc._MIN_ADX_MOMENTUM and not (st_up or st_down):
            fail_reasons.append(
                f"ADX={adx:.1f}<{sc._MIN_ADX_MOMENTUM} (blocks momentum+KST)"
            )
        if not any_fired:
            fail_reasons.append("no sub-filter fired")

        return {
            "pass": any_fired,
            "fired_directions": directions,
            "subs": subs,
            "adx": round(adx, 1),
            "vol_spike": round(vs, 2),
            "pm_1h_pct": round(pm_1h, 3),
            "vwap_disp_pct": round(vwap_disp, 3),
            "atr": round(atr, 6),
            "fund_8h_pct": round(fund_8h * 100, 5),
            "fail_reason": "; ".join(fail_reasons) if fail_reasons else None,
        }
    except Exception as e:
        return {"pass": False, "fail_reason": f"error: {e}"}


def _step3_check(sc, c: Dict) -> Dict:
    """Order book depth + spread check."""
    try:
        result = sc._check_ob_one(c)
        if result:
            return {
                "pass": True,
                "bid_depth": result.get("bid_depth_usd", 0),
                "ask_depth": result.get("ask_depth_usd", 0),
                "spread_pct": result.get("spread_pct", 0),
                "min_depth": sc._MIN_OB_DEPTH_USD,
                "max_spread": sc._MAX_SPREAD_PCT,
            }
        # Fetch OB directly to show why it failed
        sym = c["symbol"]
        exchange = c["exchange"]
        if exchange == "kraken":
            ob = sc._kraken_ob(sym)
        elif exchange == "hyperliquid":
            ob = sc._hl_ob(sym)
        else:
            ob = sc._binance_ob(sym)
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])
        if not bids or not asks:
            return {"pass": False, "fail_reason": "empty order book"}
        near_bids = bids[-10:]
        bid_depth = sum(float(b[0]) * float(b[1]) for b in near_bids)
        ask_depth = sum(float(a[0]) * float(a[1]) for a in asks[:10])
        best_bid = float(bids[-1][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2.0
        spread_pct = (best_ask - best_bid) / (mid + 1e-9) * 100
        reasons = []
        if bid_depth < sc._MIN_OB_DEPTH_USD:
            reasons.append(f"bid depth ${bid_depth:.0f} < ${sc._MIN_OB_DEPTH_USD:.0f}")
        if ask_depth < sc._MIN_OB_DEPTH_USD:
            reasons.append(f"ask depth ${ask_depth:.0f} < ${sc._MIN_OB_DEPTH_USD:.0f}")
        if spread_pct > sc._MAX_SPREAD_PCT:
            reasons.append(f"spread {spread_pct:.3f}% > {sc._MAX_SPREAD_PCT}%")
        return {
            "pass": False,
            "bid_depth": round(bid_depth, 0),
            "ask_depth": round(ask_depth, 0),
            "spread_pct": round(spread_pct, 4),
            "min_depth": sc._MIN_OB_DEPTH_USD,
            "max_spread": sc._MAX_SPREAD_PCT,
            "fail_reason": "; ".join(reasons),
        }
    except Exception as e:
        return {"pass": False, "fail_reason": f"error: {e}"}


def _step4_check(sc, c: Dict, account_balance: float = 5000.0) -> Dict:
    """EV check."""
    try:
        passed = sc._step4_expected_value([c], account_balance)
        price = c["price"]
        stop_pct = c.get("stop_pct", 0.5) / 100
        target_pct = c.get("target_pct", 1.0) / 100
        fund_ann = abs(c.get("funding_rate", 0.0))
        fund_per8h = fund_ann / (365 * 3)
        fund_cost = fund_per8h * sc._FUNDING_HOLD_PERIODS
        effective_pos = min(account_balance * 0.015 / max(stop_pct, 1e-9), 100.0)
        net_win = target_pct - sc._ROUND_TRIP_FEE_PCT - max(0.0, fund_cost)
        net_loss = stop_pct + sc._ROUND_TRIP_FEE_PCT
        ev = (0.52 * net_win * effective_pos) - (0.48 * net_loss * effective_pos)
        ok = bool(passed)
        return {
            "pass": ok,
            "ev_usd": round(ev, 4),
            "min_ev": sc._MIN_EXPECTED_PROFIT,
            "stop_pct": round(stop_pct * 100, 3),
            "target_pct": round(target_pct * 100, 3),
            "effective_pos_usd": round(effective_pos, 2),
            "fail_reason": None
            if ok
            else f"EV ${ev:.4f} < ${sc._MIN_EXPECTED_PROFIT} required",
        }
    except Exception as e:
        return {"pass": False, "fail_reason": f"error: {e}"}


def run_scan_trace(account_balance: float = 5000.0) -> Dict:
    """
    Trace all 4 core symbols through the 7 scanner steps.
    Returns dict keyed by base_asset with step-by-step results.
    Caches for 5 minutes.
    """
    global _CACHE
    now = time.time()
    if _CACHE.get("ts", 0) > now - _CACHE_TTL:
        return _CACHE["data"]

    try:
        sc = _import_scanner()
    except Exception as e:
        return {"error": str(e)}

    # Fetch live tickers once
    try:
        kraken_tickers = sc._kraken_tickers()
    except Exception:
        kraken_tickers = []
    try:
        binance_tickers = sc._binance_tickers()
    except Exception:
        binance_tickers = []
    try:
        binance_funding = sc._binance_funding_all()
    except Exception:
        binance_funding = {}
    try:
        hl_metas = sc._hl_meta_and_ctxs()
    except Exception:
        hl_metas = []

    # Build lookup by base_asset for each exchange
    kraken_by_base = {}
    for t in kraken_tickers:
        sym = t.get("symbol", "")
        if not sym.startswith("PF_") or t.get("tag") != "perpetual":
            continue
        base = sc._kraken_base(sym)
        if base in CORE_SYMBOLS:
            try:
                kraken_by_base[base] = {
                    "symbol": sym,
                    "exchange": "kraken",
                    "base_asset": base,
                    "price": float(t.get("last", 0) or 0),
                    "volume_24h_usd": float(t.get("volumeQuote", 0) or 0),
                    "vol_usd": float(t.get("volumeQuote", 0) or 0),
                    "funding_rate": float(t.get("fundingRate", 0) or 0),
                    "bid": float(t.get("bid", 0) or 0),
                    "ask": float(t.get("ask", 0) or 0),
                }
            except Exception:
                pass

    binance_by_base = {}
    for t in binance_tickers:
        sym = t.get("symbol", "")
        base = sc._binance_base(sym)
        if base in CORE_SYMBOLS:
            try:
                fund_8h = binance_funding.get(sym, 0.0)
                binance_by_base[base] = {
                    "symbol": sym,
                    "exchange": "binance",
                    "base_asset": base,
                    "price": float(t.get("lastPrice", 0) or 0),
                    "volume_24h_usd": float(t.get("quoteVolume", 0) or 0),
                    "vol_usd": float(t.get("quoteVolume", 0) or 0),
                    "funding_rate": fund_8h * 3 * 365,
                    "bid": float(t.get("bidPrice", 0) or 0),
                    "ask": float(t.get("askPrice", 0) or 0),
                }
            except Exception:
                pass

    hl_by_base = {}
    for m in hl_metas:
        base = m.get("base_asset", "")
        if base in CORE_SYMBOLS:
            hl_by_base[base] = m

    results = {}
    for base in CORE_SYMBOLS:
        # Pick best exchange: prefer Binance (deepest), fall back to Kraken, then HL
        c = (
            binance_by_base.get(base)
            or kraken_by_base.get(base)
            or hl_by_base.get(base)
        )
        if c is None:
            results[base] = {"error": "ticker not found on any exchange"}
            continue

        entry = {"symbol": base, "exchange": c["exchange"], "steps": {}}

        # Step 1
        s1 = _step1_check(sc, base, c["volume_24h_usd"], c["price"])
        entry["steps"]["1_universe"] = s1
        if not s1["pass"]:
            entry["stopped_at"] = 1
            results[base] = entry
            continue

        # Step 2 — use Kraken for klines if available (more stable), else Binance/HL
        kline_src = kraken_by_base.get(base) or c
        s2 = _step2_check(sc, kline_src)
        entry["steps"]["2_setup"] = s2
        if not s2["pass"]:
            entry["stopped_at"] = 2
            results[base] = entry
            continue

        # Need a candidate dict with stop/target for steps 3+
        # Use first fired direction from step 2 to build the candidate
        price = c["price"]
        atr = s2.get("atr", price * 0.005)
        stop_pct = (atr * 1.5) / (price + 1e-9) * 100
        target_pct = (atr * 3.0) / (price + 1e-9) * 100
        candidate = {**c, "stop_pct": stop_pct, "target_pct": target_pct}

        # Step 3
        s3 = _step3_check(sc, candidate)
        entry["steps"]["3_liquidity"] = s3
        if not s3["pass"]:
            entry["stopped_at"] = 3
            results[base] = entry
            continue

        # Step 4
        s4 = _step4_check(sc, candidate, account_balance)
        entry["steps"]["4_ev"] = s4
        if not s4["pass"]:
            entry["stopped_at"] = 4
            results[base] = entry
            continue

        # Steps 5-6 are soft (correlation pass-through, regime penalty only)
        entry["steps"]["5_correlation"] = {"pass": True, "note": "pass-through"}
        entry["steps"]["6_regime"] = {"pass": True, "note": "penalty only, not a block"}
        entry["steps"]["7_rank"] = {"pass": True, "note": "dedup + top 50 by EV"}
        entry["stopped_at"] = None  # made it all the way
        results[base] = entry

    _CACHE = {"ts": now, "data": results}
    return results
