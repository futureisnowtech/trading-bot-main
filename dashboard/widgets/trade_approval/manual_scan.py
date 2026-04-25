"""
Widget: Trade Approval / Manual Scan
Question: Run a fresh scan and hand-pick which trades to execute.
Tab: TRADE APPROVAL
Refresh: manual (button-driven)
Asset class: CRYPTO SPOT + CRYPTO PERPS
"""

import importlib.util as _ilu
import os
import sys
import streamlit as st
from datetime import datetime

# dashboard/data/ imports — intentionally from dashboard data layer (not repo-root data/)
try:
    from data.positions import (
        get_live_prices,
        get_open_positions,
        get_spot_positions_dashboard,
    )
except ImportError:
    from data.positions import get_live_prices, get_open_positions

    def get_spot_positions_dashboard():
        return []


from data.account import get_account

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# _ROOT = repo root (3 levels up from dashboard/widgets/trade_approval/)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))

# Safe import of shared tradeability engine (v16.14)
try:
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    from runtime.crypto_tradeability import get_crypto_tradeability as _get_tradeability

    _TRADEABILITY_OK = True
except Exception:
    _TRADEABILITY_OK = False


def _get_tier_info(sym: str) -> dict:
    """Return execution tier dict for a candidate symbol."""
    try:
        if _ROOT not in sys.path:
            sys.path.insert(0, _ROOT)
        from runtime.execution_universe import get_execution_policy

        return get_execution_policy(sym)
    except Exception:
        return {
            "symbol": sym,
            "underlying": sym,
            "tier": "suppressed",
            "execute": False,
            "reason": "policy_lookup_failed",
        }


def _runtime_live_flag() -> bool:
    try:
        from db import _runtime_paper_flag as _rflag

        return not bool(_rflag())
    except Exception:
        try:
            from config import PAPER_TRADING

            return not bool(PAPER_TRADING)
        except Exception:
            return False


def _blocked_tradeability(sym: str, reason: str) -> dict:
    underlying = _get_tier_info(sym).get("underlying", sym)
    return {
        "symbol": sym,
        "underlying": underlying,
        "lane": "blocked",
        "recommended_lane": "blocked",
        "status": "blocked",
        "auto_executable": 0,
        "manual_executable": 0,
        "blocked_reason": reason,
        "size_block_reason": "none",
        "source_reason": "not_applicable",
        "display_label": "BLOCKED",
    }


def _manual_tradeability(candidate: dict) -> dict:
    sym = str(candidate.get("symbol", ""))
    dirn = str(candidate.get("direction", "LONG"))
    if not _TRADEABILITY_OK:
        return _blocked_tradeability(sym, "execution_policy_unavailable")
    try:
        return _get_tradeability(
            sym,
            dirn,
            candidate,
            live=_runtime_live_flag(),
            manual=True,
        )
    except Exception:
        return _blocked_tradeability(sym, "execution_policy_unavailable")


def _min_contract_usd(exec_sym: str, price: float) -> float:
    """Return USD value of 1 Coinbase contract for exec_sym at given price. 0 if unknown."""
    try:
        if _ROOT not in sys.path:
            sys.path.insert(0, _ROOT)
        from execution.coinbase_broker import PRODUCT_SPECS

        spec = PRODUCT_SPECS.get(exec_sym, {})
        return price * spec.get("contract_size", 0)
    except Exception:
        return 0.0


_SETUP_DESC = {
    "momentum": "Price closed above VWAP with a volume spike. Trend is accelerating — ride the move.",
    "ranging_mr": "ADX < 20 (no trend). Price stretched from VWAP. Mean-reversion back toward center expected.",
    "kst_cross": "KST momentum oscillator crossed its signal line. Indicates a turning point in medium-term momentum.",
    "supertrend": "SuperTrend indicator flipped direction. Trailing stop-based trend-following entry.",
    "ichimoku": "Price broke through the Ichimoku cloud. Cloud acts as dynamic support/resistance.",
}


def _win_prob(c: dict) -> float:
    prob = 52.0
    dirn = c.get("direction", "LONG")
    vs = c.get("vol_spike", 1.0)
    adx = c.get("adx_15m", 20.0)
    setup = c.get("primary_setup", "")
    vwap_d = abs(c.get("vwap_disp_pct", 0.0))
    kst_v = c.get("kst_value", 0.0)
    kst_s = c.get("kst_signal", 0.0)
    st_dir = c.get("supertrend_dir", 0)
    fund = abs(c.get("funding_rate", 0.0))
    pm1h = c.get("price_move_1h_pct", 0.0)
    if vs >= 3.0:
        prob += 9
    elif vs >= 2.0:
        prob += 6
    elif vs >= 1.5:
        prob += 3
    if "momentum" in setup and adx >= 25:
        prob += 7
    elif "ranging" in setup and adx < 20:
        prob += 7
    elif "kst" in setup and adx < 30:
        prob += 4
    else:
        prob += 2
    if (dirn == "LONG" and kst_v > kst_s) or (dirn == "SHORT" and kst_v < kst_s):
        prob += 5
    if (dirn == "LONG" and st_dir > 0) or (dirn == "SHORT" and st_dir < 0):
        prob += 5
    if "ranging" in setup:
        if vwap_d >= 2.0:
            prob += 5
        elif vwap_d >= 1.0:
            prob += 3
    if fund > 0.002:
        prob += 3
    elif fund > 0.0005:
        prob += 1
    if dirn == "LONG" and pm1h > 0.3:
        prob += 2
    elif dirn == "SHORT" and pm1h < -0.3:
        prob += 2
    return min(round(prob, 1), 84.0)


def _render_trade_details(c: dict, prob: float):
    import pandas as pd

    sym = c.get("symbol", "")
    dirn = c.get("direction", "")
    exch = c.get("exchange", "kraken").upper()
    setup = c.get("primary_setup", "")
    price = c.get("price", 0)
    atr = c.get("atr_15m", 0)
    stop_p = c.get("stop_pct", 0)
    tgt_p = c.get("target_pct", 0)
    ev = c.get("expected_profit", 0)
    fund_ann = c.get("funding_rate", 0.0)
    fund_cost = c.get("funding_cost_pct", 0.0)
    pm4h = c.get("price_move_4h_pct", 0.0)
    vwap = c.get("vwap", 0)
    vwap_d = c.get("vwap_disp_pct", 0.0)
    all_setups = c.get("scan_setups", [setup])
    desc = _SETUP_DESC.get(setup, "Composite signal — multiple filters triggered.")
    st.markdown(f"**Setup: `{setup}`** — {desc}")
    if len(all_setups) > 1:
        others = [s for s in all_setups if s != setup]
        st.caption(f"Also triggered: {', '.join(others)}")
    st.divider()
    st.markdown(f"**→ Estimated win probability: {prob:.1f}%**")
    st.divider()
    st.markdown("**EV calculation**")
    try:
        from config import ACCOUNT_SIZE, MAX_RISK_PER_TRADE_PCT

        _acct = float(ACCOUNT_SIZE)
        _risk_pct = float(MAX_RISK_PER_TRADE_PCT)
    except Exception:
        _acct, _risk_pct = 5000.0, 0.01
    risk_usd = _acct * _risk_pct
    pos_usd = risk_usd / (stop_p / 100) if stop_p > 0 else 0
    fee_pct = 0.13  # 0.065% Kraken taker × 2 sides = 0.13% round-trip
    net_win = tgt_p / 100 - fee_pct / 100 - fund_cost / 100
    net_loss = stop_p / 100 + fee_pct / 100
    st.text(
        f"  Position: ${pos_usd:,.0f}  |  Stop: {stop_p:.3f}%  |  Target: {tgt_p:.3f}%"
    )
    st.text(f"  Net win if TP: {net_win * 100:.3f}% → ${net_win * pos_usd:+.2f}")
    st.text(f"  Net loss if SL: {net_loss * 100:.3f}% → ${-net_loss * pos_usd:.2f}")
    st.text(f"  EV = ${ev:+.2f}")
    st.divider()
    st.markdown("**Indicator readings**")
    c1, c2 = st.columns(2)
    c1.text(f"  Price:      {price:.6g}")
    c1.text(f"  VWAP:       {vwap:.6g}  ({vwap_d:+.3f}%)")
    c1.text(f"  1h move:    {c.get('price_move_1h_pct', 0):+.3f}%")
    c1.text(f"  4h move:    {pm4h:+.3f}%")
    c2.text(f"  ADX (15m):  {c.get('adx_15m', 0):.1f}")
    c2.text(f"  Vol spike:  {c.get('vol_spike', 0):.3f}×")
    c2.text(f"  Exchange:   {exch}")
    c2.text(f"  Funding:    {fund_ann * 100:.4f}% ann")


def render_manual_scan():
    st.subheader("Trade Approval")
    st.caption(
        "Runs a fresh scan on the actual live-tradable universe only "
        "(BTC / ETH / SOL / XRP / LTC / DOGE / ADA / LINK; bypasses the 5-min cache)."
    )
    st.caption(
        "Unsupported long-tail coins are intentionally excluded here so manual "
        "execution stays aligned with the live Coinbase execution set."
    )

    # ── Toast popup — fires once immediately after execution, then clears ────────
    if st.session_state.pop("manual_toast_pending", False):
        _toast_results = st.session_state.get("manual_results", [])
        _ok_trades = [(s, d, m) for s, d, ok, m in _toast_results if ok]
        _fail_trades = [(s, d, m) for s, d, ok, m in _toast_results if not ok]
        if _ok_trades and not _fail_trades:
            _label = ", ".join(f"{s} {d}" for s, d, _ in _ok_trades)
            st.toast(f"Trade filled — {_label}", icon="✅")
        elif _ok_trades and _fail_trades:
            st.toast(
                f"{len(_ok_trades)} filled, {len(_fail_trades)} failed — see results below",
                icon="⚠️",
            )
        else:
            st.toast("Execution failed — see results below", icon="❌")

    # ── Persistent execution results (survive page rerenders) ─────────────────
    _last_results = st.session_state.get("manual_results")
    if _last_results:
        st.markdown("**Last execution results:**")
        for r_sym, r_dirn, r_ok, r_msg in _last_results:
            if r_ok:
                st.success(f"✅ **{r_sym} {r_dirn}** — {r_msg}")
            else:
                st.error(f"❌ **{r_sym} {r_dirn}** — {r_msg}")
        if st.button("Clear results", key="clear_results_btn", type="secondary"):
            st.session_state.pop("manual_results", None)
            st.rerun()
        st.divider()

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        run_scan = st.button("Run Scan Now", type="primary", key="manual_scan_btn")
    with col_info:
        last_ts = st.session_state.get("manual_scan_time")
        if last_ts:
            st.caption(f"Last scan: {last_ts}")

    if run_scan:
        # Clear stale results when starting a new scan
        st.session_state.pop("manual_results", None)
        with st.spinner("Scanning Kraken + Hyperliquid (~5–10s)…"):
            try:
                import importlib

                sys.path.insert(0, _ROOT)
                import scanner as _scanner_mod

                importlib.reload(_scanner_mod)
                try:
                    from data.account import get_account as _get_account

                    _acct_balance = float(_get_account()[0])
                except Exception:
                    _acct_balance = 5000.0
                candidates = _scanner_mod.scan(
                    account_balance=_acct_balance,
                    force=True,
                    core_only=True,
                )
                st.session_state["manual_candidates"] = candidates
                st.session_state["manual_scan_time"] = datetime.now().strftime(
                    "%H:%M:%S"
                )
                for k in list(st.session_state.keys()):
                    if k.startswith("ms_sel_"):
                        del st.session_state[k]
                # Auto-select executable candidates so Review fires immediately
                for _j, _c in enumerate(candidates):
                    if _manual_tradeability(_c).get("status") == "executable":
                        st.session_state[f"ms_sel_{_j}"] = True
                st.session_state["ms_auto_review"] = True
                st.session_state.pop("ms_previews", None)
            except Exception as e:
                st.error(f"Scan failed: {e}")
                return
        n = len(st.session_state.get("manual_candidates", []))
        st.success(f"Found {n} candidates.")

    candidates = st.session_state.get("manual_candidates", [])
    if not candidates:
        st.info("No scan results yet — click **Run Scan Now** above.")
        render_spot_section()
        return

    hc1, hc2, hc3, hc4 = st.columns([0.4, 3.2, 2.8, 0.6])
    hc1.caption("Trade?")
    hc2.caption("Signal")
    hc3.caption("Win Probability")
    hc4.caption("Why")
    st.divider()

    for i, c in enumerate(candidates):
        prob = _win_prob(c)
        sym = c.get("symbol", "")
        dirn = c.get("direction", "")
        exch = c.get("exchange", "kraken")
        setup = c.get("primary_setup", "")
        badge = "🔵" if exch == "hyperliquid" else "🟠"
        _trade_row = _manual_tradeability(c)
        _can_execute = bool(_trade_row.get("manual_executable")) and (
            _trade_row.get("status") == "executable"
        )
        _display = _trade_row.get("display_label", "BLOCKED")
        _lane = _trade_row.get("lane", "blocked")
        _recommended = _trade_row.get("recommended_lane", _lane)
        _blocked_reason = _trade_row.get("blocked_reason", "")
        _auto_executable = bool(_trade_row.get("auto_executable", 0))

        col1, col2, col3, col4 = st.columns([0.4, 3.2, 2.8, 0.6])
        with col1:
            if _can_execute:
                st.checkbox("", key=f"ms_sel_{i}", label_visibility="collapsed")
            else:
                st.checkbox(
                    "",
                    key=f"ms_sel_{i}",
                    label_visibility="collapsed",
                    disabled=True,
                    value=False,
                )
        with col2:
            if _display == "SPOT EXECUTABLE":
                _badge_str = ":green[SPOT EXECUTABLE]"
            elif _display == "PERP EXECUTABLE":
                _badge_str = ":blue[PERP EXECUTABLE]"
            else:
                _badge_str = ":red[BLOCKED]"
            st.markdown(
                f"**{sym}** `{dirn}` {badge} `{exch[:5].upper()}` · *{setup}* {_badge_str}"
            )
            if not _can_execute:
                st.caption(
                    f"Blocked: {_blocked_reason or 'execution_policy_unavailable'}"
                )
            else:
                st.caption(f"Route: {_lane}")
                if _recommended and _recommended != _lane and _recommended != "blocked":
                    st.caption(
                        f"Preferred lane `{_recommended}` currently unavailable; using `{_lane}` instead"
                    )
                if _runtime_live_flag() and not _auto_executable:
                    st.caption(
                        "Manual only right now — bot will not auto-enter this route."
                    )
        with col3:
            label = f"{prob:.0f}% — {'High edge' if prob >= 68 else ('Moderate edge' if prob >= 60 else 'Lower edge')}"
            st.progress(prob / 100.0, text=label)
        with col4:
            with st.expander("i"):
                _render_trade_details(c, prob)

    st.divider()

    selected_idx = [
        i for i in range(len(candidates)) if st.session_state.get(f"ms_sel_{i}", False)
    ]
    n_sel = len(selected_idx)

    if n_sel == 0:
        st.caption(
            "Check the **Trade?** box on rows you want to execute, then click Review."
        )
        render_spot_section()
        return

    # ── Helper: compute sizing for one candidate (shared by Review + Execute) ──
    def _compute_preview(cand, exec_paper, acct_balance, get_candles_fn, open_pos):
        sym = cand["symbol"]
        dirn = cand["direction"]
        setup = cand.get("primary_setup", "manual")
        prob = _win_prob(cand)

        # ── Tradeability routing (v16.14) ─────────────────────────────────────
        # Use shared engine if available; fall back to tier-only gate on import error.
        _trade_result = _manual_tradeability(cand)
        exec_sym = _trade_result.get("underlying") or sym

        if _trade_result.get("status") == "blocked":
            return {
                "sym": sym,
                "exec_sym": exec_sym,
                "dirn": dirn,
                "setup": setup,
                "blocked": True,
                "block_reason": _trade_result.get(
                    "blocked_reason", "execution_policy_unavailable"
                ),
                "display_label": _trade_result.get("display_label", "BLOCKED"),
                "trade_lane": "blocked",
                "prob": prob,
                "cand": cand,
            }

        # Fetch candles
        df_c = None
        for fsym in [exec_sym, sym] if exec_sym != sym else [sym]:
            try:
                df = get_candles_fn(fsym, "1h", 100)
                if df is not None and len(df) >= 10:
                    df_c = df
                    break
            except Exception:
                pass
        if df_c is None or len(df_c) < 10:
            return {
                "sym": sym,
                "exec_sym": exec_sym,
                "dirn": dirn,
                "setup": setup,
                "blocked": True,
                "block_reason": "insufficient candle data",
                "prob": prob,
                "cand": cand,
            }

        candle_price = float(df_c["close"].iloc[-1])
        live_now = get_live_prices([exec_sym]).get(exec_sym, 0) or get_live_prices(
            [sym]
        ).get(sym, 0)
        if live_now > 0:
            ratio = candle_price / live_now
            price = candle_price if 0.95 <= ratio <= 1.05 else live_now
        else:
            price = candle_price
        if price <= 0:
            return {
                "sym": sym,
                "exec_sym": exec_sym,
                "dirn": dirn,
                "setup": setup,
                "blocked": True,
                "block_reason": "no valid price",
                "prob": prob,
                "cand": cand,
            }

        atr_7 = float(df_c["high"].sub(df_c["low"]).tail(7).mean())
        if atr_7 <= 0 or (live_now > 0 and abs(candle_price / live_now - 1) > 0.10):
            atr_7 = price * 0.015

        stop_dist = max(atr_7 * 1.5, price * 0.008)
        target_dist = stop_dist * 3.0
        composite = cand.get("composite_score", 50.0)

        from position_manager import compute_position_size

        deployed = sum(
            float(p.get("qty", 0)) * float(p.get("entry", 0)) for p in open_pos
        )
        sizing = compute_position_size(
            account_balance=acct_balance,
            current_price=price,
            atr_7=atr_7,
            stop_multiplier=1.5,
            ml_score=composite,
            composite_score=composite,
            deployed_usd=deployed,
            paper=exec_paper,
        )
        pos_usd = sizing["position_usd"]
        leverage = sizing["leverage"]

        # ── Size guards + lane from tradeability (live only) ─────────────────
        size_note = None
        trade_lane = _trade_result.get("lane", "perp")
        if not exec_paper:
            if trade_lane == "spot":
                # Spot has no perp contract minimum — floor/cap by spot policy
                try:
                    from config import (
                        SPOT_MAX_DEPLOYED_PCT as _spct,
                        SPOT_MIN_ORDER_USD as _smin,
                    )

                    spot_floor = float(_smin)
                    spot_cap = round(acct_balance * float(_spct), 2)
                except Exception:
                    spot_floor, spot_cap = 10.0, round(acct_balance * 0.50, 2)
                if pos_usd < spot_floor:
                    pos_usd = spot_floor
                if pos_usd > spot_cap:
                    pos_usd = spot_cap
            else:
                min_usd = _min_contract_usd(exec_sym, price)
                max_single = round(acct_balance * 0.15, 2)
                if min_usd > 0 and min_usd > max_single:
                    return {
                        "sym": sym,
                        "exec_sym": exec_sym,
                        "dirn": dirn,
                        "setup": setup,
                        "blocked": True,
                        "block_reason": f"perp_contract_min_exceeds_policy (${min_usd:.0f} > 15% cap ${max_single:.0f})",
                        "display_label": "BLOCKED",
                        "trade_lane": "blocked",
                        "prob": prob,
                        "cand": cand,
                    }
                if min_usd > 0 and pos_usd < min_usd:
                    size_note = f"bumped from ${pos_usd:.0f} to 1-contract minimum"
                pos_usd = round(min_usd * 1.02, 2)
            if pos_usd > max_single:
                pos_usd = max_single

        stop_p = (
            round(price - stop_dist, 6)
            if dirn == "LONG"
            else round(price + stop_dist, 6)
        )
        target_p = (
            round(price + target_dist, 6)
            if dirn == "LONG"
            else round(price - target_dist, 6)
        )
        stop_pct = stop_dist / price * 100
        target_pct = target_dist / price * 100
        max_loss = round(pos_usd * (stop_pct / 100), 2)
        fee_cost = round(pos_usd * 0.0006, 2)  # round-trip 0.06%

        return {
            "sym": sym,
            "exec_sym": exec_sym,
            "dirn": dirn,
            "setup": setup,
            "blocked": False,
            "block_reason": None,
            "display_label": _trade_result.get("display_label", "PERP EXECUTABLE"),
            "trade_lane": _trade_result.get("lane", "perp"),
            "price": price,
            "stop_p": stop_p,
            "target_p": target_p,
            "stop_pct": stop_pct,
            "target_pct": target_pct,
            "pos_usd": pos_usd,
            "leverage": leverage,
            "max_loss": max_loss,
            "fee_cost": fee_cost,
            "atr_7": atr_7,
            "composite": composite,
            "prob": prob,
            "size_note": size_note,
            "cand": cand,
        }

    # ── PHASE 1: Review button — compute previews, don't execute yet ──────────
    previews = st.session_state.get("ms_previews")

    if previews is None:
        _auto_review = st.session_state.pop("ms_auto_review", False)
        if _auto_review or st.button(
            f"Review {n_sel} Order(s)", type="primary", key="ms_review_btn"
        ):
            # Resolve mode + balance once
            try:
                from db import _runtime_paper_flag as _rflag

                _exec_paper = bool(_rflag())
            except Exception:
                try:
                    from config import PAPER_TRADING

                    _exec_paper = bool(PAPER_TRADING)
                except Exception:
                    _exec_paper = True

            if not _exec_paper:
                try:
                    from data.balance import get_coinbase_balance

                    _cb = get_coinbase_balance()
                    _acct_balance = (
                        float(_cb["balance"])
                        if _cb.get("connected") and _cb.get("balance", 0) > 0
                        else get_account()[0]
                    )
                except Exception:
                    _acct_balance = get_account()[0]
            else:
                _acct_balance = get_account()[0]

            try:
                _hd_spec = _ilu.spec_from_file_location(
                    "_root_data_historical_data",
                    os.path.join(_ROOT, "data", "historical_data.py"),
                )
                _hd_mod = _ilu.module_from_spec(_hd_spec)
                _hd_spec.loader.exec_module(_hd_mod)
                get_candles = _hd_mod.get_candles
            except Exception as e:
                st.error(f"Cannot load candle module: {e}")
                return

            _open_pos_now = get_open_positions()

            with st.spinner("Computing order details…"):
                computed = []
                for idx in selected_idx:
                    pv = _compute_preview(
                        candidates[idx],
                        _exec_paper,
                        _acct_balance,
                        get_candles,
                        _open_pos_now,
                    )
                    pv["exec_paper"] = _exec_paper
                    pv["acct_balance"] = _acct_balance
                    computed.append(pv)

            st.session_state["ms_previews"] = computed
            st.rerun()
        render_spot_section()
        return

    # ── PHASE 2: Confirmation panel ───────────────────────────────────────────
    # Always re-read runtime paper flag — never trust stale session-state preview
    try:
        from db import _runtime_paper_flag as _rflag_p2

        _exec_paper = bool(_rflag_p2())
    except Exception:
        _exec_paper = previews[0].get("exec_paper", True)
    _acct_balance = previews[0].get("acct_balance", 5000.0)
    mode_label = "PAPER" if _exec_paper else "LIVE"
    mode_color = "orange" if _exec_paper else "red"

    st.markdown(f"### Order Review — :{mode_color}[{mode_label} MODE]")
    st.caption(
        "Prices and sizes were computed when you clicked Review. "
        "Execution uses fresh prices — small slippage is normal."
    )

    any_executable = False
    for pv in previews:
        sym = pv["sym"]
        dirn = pv["dirn"]
        setup = pv["setup"]

        if pv["blocked"]:
            st.error(f"**{sym} {dirn}** — blocked: {pv['block_reason']}")
            continue

        any_executable = True
        price = pv["price"]
        stop_p = pv["stop_p"]
        target_p = pv["target_p"]
        pos_usd = pv["pos_usd"]
        leverage = pv["leverage"]
        max_loss = pv["max_loss"]
        fee_cost = pv["fee_cost"]
        stop_pct = pv["stop_pct"]
        target_pct = pv["target_pct"]
        prob = pv["prob"]
        composite = pv["composite"]
        cand = pv["cand"]

        arrow = "▲" if dirn == "LONG" else "▼"
        dir_color = "green" if dirn == "LONG" else "red"

        with st.container(border=True):
            st.markdown(f"#### :{dir_color}[{arrow} {sym} {dirn}]  `{setup}`")

            c1, c2, c3 = st.columns(3)
            c1.metric("Entry (approx)", f"${price:.5g}")
            c2.metric(
                "Stop", f"${stop_p:.5g}", f"-{stop_pct:.2f}%", delta_color="inverse"
            )
            c3.metric("Target", f"${target_p:.5g}", f"+{target_pct:.2f}%")

            c4, c5, c6, c7 = st.columns(4)
            c4.metric("Position size", f"${pos_usd:.2f}")
            c5.metric("Leverage", f"{leverage}×")
            c6.metric("Max loss (at stop)", f"-${max_loss:.2f}")
            c7.metric("Est. fees (round-trip)", f"${fee_cost:.2f}")

            st.divider()

            th1, th2 = st.columns([1, 1])
            with th1:
                st.markdown("**Signal thesis**")
                st.markdown(
                    f"- Setup: `{setup}` — {_SETUP_DESC.get(setup, 'Composite signal.')}"
                )
                st.markdown(f"- Win probability (heuristic): **{prob:.1f}%**")
                st.markdown(f"- Composite score: **{composite:.1f} / 100**")
                st.markdown(
                    f"- R:R ratio: **3:1** (stop {stop_pct:.2f}% → target {target_pct:.2f}%)"
                )
                all_setups = cand.get("scan_setups", [setup])
                if len(all_setups) > 1:
                    others = [s for s in all_setups if s != setup]
                    st.markdown(f"- Also triggered: {', '.join(others)}")
            with th2:
                st.markdown("**Key indicators**")
                adx = cand.get("adx_15m", 0)
                vs = cand.get("vol_spike", 1.0)
                fund = cand.get("funding_rate", 0.0)
                vwap_d = cand.get("vwap_disp_pct", 0.0)
                pm1h = cand.get("price_move_1h_pct", 0.0)
                pm4h = cand.get("price_move_4h_pct", 0.0)
                st.markdown(
                    f"- ADX (15m): `{adx:.1f}` {'(trending)' if adx >= 25 else '(ranging)'}"
                )
                st.markdown(f"- Volume spike: `{vs:.2f}×`")
                st.markdown(f"- VWAP displacement: `{vwap_d:+.3f}%`")
                st.markdown(f"- 1h / 4h move: `{pm1h:+.2f}%` / `{pm4h:+.2f}%`")
                st.markdown(f"- Funding rate: `{fund * 100:.4f}%`")
                st.markdown(f"- Exchange: `{cand.get('exchange', '?').upper()}`")

            if pv.get("size_note"):
                st.info(f"ℹ️ Size note: {pv['size_note']}")

    if not any_executable:
        st.warning("All selected trades are blocked. Nothing to execute.")
        if st.button("Back", key="ms_back_all_blocked"):
            st.session_state.pop("ms_previews", None)
            st.rerun()
        render_spot_section()
        return

    st.divider()
    ca, cb = st.columns([1, 1])
    with ca:
        if st.button("Confirm & Execute", type="primary", key="ms_confirm_btn"):
            # ── PHASE 3: Execute confirmed orders ─────────────────────────────
            # Re-read paper flag fresh at execute time — never trust stale preview cache
            try:
                from db import _runtime_paper_flag as _rflag_exec

                _exec_paper = bool(_rflag_exec())
            except Exception:
                try:
                    from config import PAPER_TRADING as _PT

                    _exec_paper = bool(_PT)
                except Exception:
                    _exec_paper = True
            try:
                _hd_spec = _ilu.spec_from_file_location(
                    "_root_data_historical_data",
                    os.path.join(_ROOT, "data", "historical_data.py"),
                )
                _hd_mod = _ilu.module_from_spec(_hd_spec)
                _hd_spec.loader.exec_module(_hd_mod)
                get_candles = _hd_mod.get_candles
            except Exception as e:
                st.error(f"Cannot load candle module: {e}")
                return

            if _ROOT not in sys.path:
                sys.path.insert(0, _ROOT)
            import perps_engine as perps

            _open_pos_now = get_open_positions()
            _held_syms = {p.get("symbol", "") for p in _open_pos_now}
            _batch_syms: set = set()
            results = []

            for pv in previews:
                if pv["blocked"]:
                    results.append((pv["sym"], pv["dirn"], False, pv["block_reason"]))
                    continue

                sym = pv["sym"]
                dirn = pv["dirn"]
                setup = pv["setup"]
                _trade_now = _manual_tradeability(pv["cand"])
                if _trade_now.get("status") != "executable" or not _trade_now.get(
                    "manual_executable", 0
                ):
                    results.append(
                        (
                            sym,
                            dirn,
                            False,
                            _trade_now.get(
                                "blocked_reason", "execution_policy_unavailable"
                            ),
                        )
                    )
                    continue
                exec_sym = _trade_now.get("underlying") or pv["exec_sym"]

                # Batch dedup — block the same symbol appearing twice in one click
                if exec_sym in _batch_syms:
                    results.append(
                        (sym, dirn, False, f"conflict: {exec_sym} already in batch")
                    )
                    continue

                # Re-fetch fresh price/ATR at execution time
                try:
                    df_c = None
                    for fsym in [exec_sym, sym] if exec_sym != sym else [sym]:
                        try:
                            df = get_candles(fsym, "1h", 100)
                            if df is not None and len(df) >= 10:
                                df_c = df
                                break
                        except Exception:
                            pass

                    candle_price = (
                        float(df_c["close"].iloc[-1])
                        if df_c is not None
                        else pv["price"]
                    )
                    live_now = get_live_prices([exec_sym]).get(
                        exec_sym, 0
                    ) or get_live_prices([sym]).get(sym, 0)
                    if live_now > 0:
                        ratio = candle_price / live_now
                        price = candle_price if 0.95 <= ratio <= 1.05 else live_now
                    else:
                        price = candle_price
                    if price <= 0:
                        price = pv["price"]  # fall back to preview price

                    atr_7 = (
                        float(df_c["high"].sub(df_c["low"]).tail(7).mean())
                        if df_c is not None
                        else pv["atr_7"]
                    )
                    if atr_7 <= 0:
                        atr_7 = price * 0.015

                    stop_dist = max(atr_7 * 1.5, price * 0.008)
                    target_dist = stop_dist * 3.0
                    pos_usd = pv["pos_usd"]
                    leverage = pv["leverage"]

                    # ── Lane routing from tradeability result (v16.14) ────────
                    _exec_lane = _trade_now.get("lane", pv.get("trade_lane", "perp"))

                    if _exec_lane == "spot":
                        # Spot execution — no leverage, no short, no stop/target
                        import spot_engine as _se_exec

                        stop_p = round(price - stop_dist, 6)
                        target_p = round(price + target_dist, 6)
                        try:
                            pos = _se_exec.open_spot(
                                exec_sym, pos_usd, paper=_exec_paper
                            )
                        except Exception as _spot_exc:
                            results.append(
                                (sym, dirn, False, f"spot exception: {_spot_exc}")
                            )
                            continue
                        if pos:
                            results.append(
                                (
                                    sym,
                                    dirn,
                                    True,
                                    f"[{mode_label}][SPOT] entered @ {pos.get('entry', price):.6g}  "
                                    f"qty={pos.get('qty', 0):.6g}  "
                                    f"size=${pos.get('size_usd', pos_usd):.0f}  order={pos.get('order_id', '?')}",
                                )
                            )
                            _batch_syms.add(exec_sym)
                            _held_syms.add(exec_sym)
                        else:
                            _se_exec._load_config()
                            _lane_on = getattr(_se_exec, "_SPOT_LANE_ACTIVE", "?")
                            _syms = getattr(_se_exec, "_SPOT_SYMBOLS", [])
                            _min_sz = getattr(_se_exec, "_SPOT_MIN_ORDER_USD", 10.0)
                            results.append(
                                (
                                    sym,
                                    dirn,
                                    False,
                                    f"spot None (lane={_lane_on}, sym_ok={exec_sym in _syms}, "
                                    f"size=${pos_usd:.0f}>=${_min_sz:.0f}, paper={_exec_paper})",
                                )
                            )
                    else:
                        # Perp execution path
                        if dirn == "LONG":
                            stop_p = round(price - stop_dist, 6)
                            target_p = round(price + target_dist, 6)
                            pos = perps.open_long(
                                symbol=exec_sym,
                                position_usd=pos_usd,
                                entry_price=price,
                                stop_price=stop_p,
                                take_profit_price=target_p,
                                leverage=leverage,
                                composite_score=pv["composite"],
                                atr_at_entry=atr_7,
                                regime="UNKNOWN",
                                entry_setup=f"manual_{setup}",
                                paper=_exec_paper,
                            )
                        else:
                            stop_p = round(price + stop_dist, 6)
                            target_p = round(price - target_dist, 6)
                            pos = perps.open_short(
                                symbol=exec_sym,
                                position_usd=pos_usd,
                                entry_price=price,
                                stop_price=stop_p,
                                take_profit_price=target_p,
                                leverage=leverage,
                                composite_score=pv["composite"],
                                atr_at_entry=atr_7,
                                regime="UNKNOWN",
                                entry_setup=f"manual_{setup}",
                                paper=_exec_paper,
                            )

                        if pos:
                            results.append(
                                (
                                    sym,
                                    dirn,
                                    True,
                                    f"[{mode_label}] entered @ {price:.6g}  "
                                    f"stop={stop_p:.6g}  target={target_p:.6g}  "
                                    f"size=${pos_usd:.0f}  lev={leverage}x",
                                )
                            )
                            _batch_syms.add(exec_sym)
                            _held_syms.add(exec_sym)
                        else:
                            if not _exec_paper:
                                min_usd = _min_contract_usd(exec_sym, price)
                                why = (
                                    f"size ${pos_usd:.0f} < 1 contract (≈${min_usd:.0f})"
                                    if min_usd > 0 and pos_usd < min_usd
                                    else "broker returned None — check bot log"
                                )
                                results.append((sym, dirn, False, why))
                            else:
                                results.append(
                                    (sym, dirn, False, "open_long/short returned None")
                                )

                except Exception as e:
                    results.append((sym, dirn, False, str(e)[:200]))

            st.session_state["manual_results"] = results
            st.session_state["manual_toast_pending"] = True
            st.session_state.pop("ms_previews", None)
            st.session_state.pop("manual_candidates", None)
            st.session_state.pop("manual_scan_time", None)
            st.rerun()

    with cb:
        if st.button("Cancel", type="secondary", key="ms_cancel_btn"):
            st.session_state.pop("ms_previews", None)
            st.rerun()

    render_spot_section()


# ── Spot section — auto-called from render_manual_scan (wired here) ──────────


def _get_latest_spot_scan(underlying: str) -> dict:
    """
    Return the most recent scan assessment for a spot-eligible underlying.
    Checks in-session manual scan results first (freshest), then DB scan_candidates.
    Returns dict with: score, decision, age_secs, source, notes.
    """
    import sqlite3 as _sq
    from datetime import datetime, timezone

    # 1. Try the in-session manual scan results (Run Scan Now was clicked this page load)
    session_candidates = st.session_state.get("manual_candidates", [])
    best_session = None
    best_score = -1.0
    for c in session_candidates:
        # Resolve underlying via execution policy
        try:
            from runtime.execution_universe import get_execution_policy

            _u = get_execution_policy(c.get("symbol", "")).get("underlying", "")
        except Exception:
            _u = c.get("symbol", "")
        if _u == underlying:
            sc = float(c.get("composite_score", 0))
            if sc > best_score:
                best_score = sc
                best_session = c
    if best_session is not None:
        return {
            "score": best_score,
            "final_spot_score": best_score,
            "decision": "scanned (not yet submitted to runner)",
            "age_secs": 0,
            "source": "manual_scan",
            "primary_setup": best_session.get("primary_setup", ""),
        }

    # 2. Fall back to DB scan_candidates
    try:
        _db_path = os.path.join(_ROOT, "logs", "trades.db")
        _conn = _sq.connect(_db_path, timeout=5)
        _conn.row_factory = _sq.Row
        row = _conn.execute(
            """
            SELECT
                composite_score,
                final_spot_score,
                regime_floor,
                decision,
                ts,
                primary_setup,
                spot_regime,
                setup_family,
                setup_score,
                setup_preference,
                tf_5m_state,
                tf_30m_state,
                tf_4h_state,
                tf_1d_state,
                structural_confirms,
                execution_route,
                cooldown_until,
                microstructure_veto,
                stop_pct
            FROM scan_candidates
            WHERE base_asset=? OR symbol=?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (underlying, underlying),
        ).fetchone()
        _conn.close()
        if row:
            try:
                ts_str = row["ts"].replace("Z", "+00:00")
                ts_dt = datetime.fromisoformat(ts_str)
                age = (datetime.now(timezone.utc) - ts_dt).total_seconds()
            except Exception:
                age = -1
            return {
                "score": float(row["composite_score"]),
                "final_spot_score": float(
                    row["final_spot_score"] or row["composite_score"] or 0.0
                ),
                "regime_floor": float(row["regime_floor"] or 0.0),
                "decision": row["decision"] or "unknown",
                "age_secs": age,
                "source": "db",
                "primary_setup": row["primary_setup"] or "",
                "spot_regime": row["spot_regime"] or "",
                "setup_family": row["setup_family"] or "",
                "setup_score": float(row["setup_score"] or 0.0),
                "setup_preference": row["setup_preference"] or "",
                "tf_5m_state": row["tf_5m_state"] or "",
                "tf_30m_state": row["tf_30m_state"] or "",
                "tf_4h_state": row["tf_4h_state"] or "",
                "tf_1d_state": row["tf_1d_state"] or "",
                "structural_confirms": row["structural_confirms"] or "",
                "execution_route": row["execution_route"] or "",
                "cooldown_until": row["cooldown_until"] or "",
                "microstructure_veto": row["microstructure_veto"] or "",
                "stop_pct": float(row["stop_pct"] or 0.0),
            }
    except Exception:
        pass

    return {}


def render_spot_section():
    """
    Spot lane panel — surfaces autonomous scanner state, then offers manual override.

    The bot already scans the supported spot universe and enters spot automatically
    when the scalp score, derivative stack, and economics gate all agree. This panel
    shows what the scanner found so the operator understands WHY the bot did or
    didn't enter, and provides a clearly-labeled manual override.
    """
    st.divider()
    st.subheader("Spot Lane")

    try:
        if _ROOT not in sys.path:
            sys.path.insert(0, _ROOT)
        from config import SPOT_LANE_ACTIVE, SPOT_SYMBOLS, SPOT_MIN_ORDER_USD
        from runtime.spot_strategy import (
            edge_policy_for_symbol,
            setup_preference_for_symbol,
        )

        spot_active = bool(SPOT_LANE_ACTIVE)
        spot_symbols = list(SPOT_SYMBOLS)
        spot_min = float(SPOT_MIN_ORDER_USD)
    except Exception:
        spot_active = False
        spot_symbols = ["BTC", "ETH", "SOL", "XRP", "LTC", "DOGE", "ADA", "LINK"]
        spot_min = 10.0
        edge_policy_for_symbol = lambda _sym: {}
        setup_preference_for_symbol = lambda _sym, _setup: "unknown"

    if not spot_active:
        st.info("Spot lane disabled — set SPOT_LANE_ACTIVE=true in .env to enable.")
        return

    # Paper flag
    try:
        from db import _runtime_paper_flag as _rflag

        _exec_paper = bool(_rflag())
    except Exception:
        try:
            from config import PAPER_TRADING

            _exec_paper = bool(PAPER_TRADING)
        except Exception:
            _exec_paper = True

    mode_label = "PAPER" if _exec_paper else "LIVE"

    st.caption(
        "The bot scans the full 8-symbol spot scalp universe continuously and enters "
        "spot **automatically** when setup quality, momentum derivatives, confirmations, "
        "and economics all agree. Each symbol has preferred setups, but exceptional "
        "opportunistic setups can still qualify if the derivative evidence is strong enough. "
        "Click **Run Scan Now** above to see the live truth. Manual override is available below each symbol."
    )

    # ── Persistent feedback from last manual action ───────────────────────────
    _spot_msg = st.session_state.get("spot_manual_message")
    if _spot_msg:
        _level = _spot_msg.get("level", "info")
        _text = _spot_msg.get("text", "")
        if _level == "success":
            st.success(_text)
        elif _level == "error":
            st.error(_text)
        else:
            st.info(_text)
        if st.button("Clear spot result", key="clear_spot_result_btn"):
            st.session_state.pop("spot_manual_message", None)
            st.rerun()

    # ── Load spot positions + balance ─────────────────────────────────────────
    try:
        import spot_engine as _se

        spot_positions = get_spot_positions_dashboard()
    except Exception as e:
        st.error(f"Could not load spot positions: {e}")
        return

    try:
        from data.balance import get_spot_balance_summary

        _spot_bal = get_spot_balance_summary()
        usd_avail = _spot_bal.get("usd_available", 0.0)
        held_map = _spot_bal.get("held_usd_by_symbol") or {}
        _bal_source = _spot_bal.get("source", "unknown")
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("USD available (spot)", f"${usd_avail:,.2f}")
        sc2.metric("Spot equity", f"${float(_spot_bal.get('spot_equity') or 0.0):,.2f}")
        sc3.metric(
            "Active spot positions", str(len([v for v in held_map.values() if v > 0]))
        )
        if held_map:
            pretty = " · ".join(
                f"{s} ${a:,.0f}" for s, a in sorted(held_map.items()) if a > 0
            )
            if pretty:
                st.caption(f"Currently holding: {pretty}")
        if _bal_source != "live_api":
            st.caption(f"Balance source: {_bal_source}")
    except Exception:
        pass

    # ── Per-symbol: signal state + open position + manual override ────────────
    held_syms = {p.get("symbol", ""): p for p in spot_positions}

    for sym in spot_symbols:
        st.markdown(f"---\n**{sym}-USD**")
        scan = _get_latest_spot_scan(sym)
        score = scan.get("final_spot_score", scan.get("score"))
        decision = scan.get("decision", "")
        age_secs = scan.get("age_secs", -1)
        scan_source = scan.get("source", "")
        regime = scan.get("spot_regime", "")
        setup_family = scan.get("setup_family", "")
        setup_pref = scan.get("setup_preference") or (
            setup_preference_for_symbol(sym, setup_family) if setup_family else ""
        )
        setup_score = float(scan.get("setup_score") or 0.0)
        confirms = scan.get("structural_confirms", "")
        stop_pct = float(scan.get("stop_pct") or 0.0)
        edge_policy = edge_policy_for_symbol(sym)
        edge_profile = str(edge_policy.get("profile") or "").title()
        edge_summary = str(edge_policy.get("conditions_summary") or "")
        edge_metrics = edge_policy.get("metrics") or {}
        threshold = float(scan.get("regime_floor") or 0.0)
        if threshold <= 0:
            try:
                from runtime.spot_regime import score_floor_for_regime

                threshold = float(
                    score_floor_for_regime(
                        regime or "NEUTRAL",
                        structural_confirm_count=len(
                            [x for x in confirms.split(",") if x.strip()]
                        ),
                        setup_family=setup_family or "",
                        setup_score=setup_score,
                        symbol=sym,
                    )
                )
            except Exception:
                threshold = 61.0

        # Signal status block
        if score is not None:
            age_str = (
                "this scan"
                if scan_source == "manual_scan"
                else (f"{int(age_secs // 60)}m ago" if age_secs >= 0 else "unknown age")
            )
            if score >= threshold:
                score_display = (
                    f":green[{score:.0f}/100 ✅ above threshold ({threshold})]"
                )
            elif score >= threshold * 0.85:
                score_display = (
                    f":orange[{score:.0f}/100 ⚠️ near threshold ({threshold})]"
                )
            else:
                score_display = f":red[{score:.0f}/100 ✗ below threshold ({threshold})]"

            st.markdown(f"Signal score: {score_display} · scanned {age_str}")
            meta_bits = []
            if regime:
                meta_bits.append(f"regime `{regime}`")
            if setup_family:
                if setup_pref and setup_pref != "unknown":
                    if setup_score > 0:
                        meta_bits.append(
                            f"setup `{setup_family}` ({setup_pref}, evidence {setup_score:.2f})"
                        )
                    else:
                        meta_bits.append(f"setup `{setup_family}` ({setup_pref})")
                else:
                    if setup_score > 0:
                        meta_bits.append(
                            f"setup `{setup_family}` (evidence {setup_score:.2f})"
                        )
                    else:
                        meta_bits.append(f"setup `{setup_family}`")
            if confirms:
                meta_bits.append(f"confirms `{confirms}`")
            if stop_pct > 0:
                meta_bits.append(f"stop `{stop_pct:.2%}`")
            if edge_profile:
                meta_bits.append(f"edge `{edge_profile}`")
            if edge_summary:
                meta_bits.append(edge_summary)
            if edge_metrics:
                meta_bits.append(
                    f"replay PF `{float(edge_metrics.get('pf') or 0.0):.2f}`"
                )
                meta_bits.append(
                    f"replay WR `{float(edge_metrics.get('wr') or 0.0) * 100:.1f}%`"
                )
            if meta_bits:
                st.caption(" · ".join(meta_bits))

            # Translate decision into plain language
            _decision_plain = {
                "entered": "Bot entered this position automatically.",
                "below_threshold": f"Bot skipped — signal score too low (needs {threshold}).",
                "econ_veto": "Bot skipped — expected edge doesn't clear fee hurdle.",
                "data_unavailable": "Bot skipped — live multi-timeframe spot state was unavailable.",
                "sizing_zero": "Bot skipped — position size computed to zero.",
                "research_only_block": "Symbol is research-only, not in live execution universe.",
                "scanned (not yet submitted to runner)": "Freshly scanned — not yet processed by the autonomous runner.",
            }.get(
                decision,
                f"Bot decision: {decision}"
                if decision
                else "No decision recorded yet.",
            )
            st.caption(_decision_plain)
        else:
            st.caption(
                "No scan data yet — click **Run Scan Now** above to score this symbol."
            )

        # Open position for this symbol
        pos = held_syms.get(sym)
        if pos:
            qty = float(pos.get("qty", 0))
            entry = float(pos.get("entry", 0))
            target = float(pos.get("target", 0))
            stop = float(pos.get("stop", 0))
            st.markdown(
                f"**Open position:** {qty:.6f} units @ ${entry:.4f} entry"
                + (f" · target ${target:.4f}" if target else "")
                + (f" · stop ${stop:.4f}" if stop else "")
            )
            if st.button(
                f"Sell all {sym} now", key=f"spot_sell_{sym}", type="secondary"
            ):
                try:
                    result = _se.close_spot(sym, paper=_exec_paper)
                    if result:
                        pnl = result.get("pnl_usd", 0.0)
                        st.session_state["spot_manual_message"] = {
                            "level": "success",
                            "text": (
                                f"[{mode_label}] Sold {sym}: "
                                f"exit @ ${result['exit_price']:.4f}  pnl=${pnl:+.2f}"
                            ),
                        }
                    else:
                        st.session_state["spot_manual_message"] = {
                            "level": "error",
                            "text": f"close_spot {sym} returned None",
                        }
                except Exception as ex:
                    st.session_state["spot_manual_message"] = {
                        "level": "error",
                        "text": f"Sell error: {ex}",
                    }
                st.rerun()

        # Manual override buy — clearly secondary
        with st.expander(f"Manual override — Buy {sym}"):
            # Warn if signal doesn't support entry
            if score is not None and score < threshold:
                st.warning(
                    f"Signal is below the entry threshold ({score:.0f} < {threshold}). "
                    "The bot would not enter here autonomously. Only override if you have "
                    "independent conviction the chart warrants it."
                )
            elif score is None:
                st.warning(
                    "No scan data — run a scan first so you can see the signal before buying."
                )

            size_input = st.number_input(
                f"Size USD",
                min_value=float(spot_min),
                max_value=5000.0,
                value=float(spot_min * 2),
                step=10.0,
                key=f"spot_size_{sym}",
            )
            if st.button(
                f"Buy {sym} — Manual Override (${size_input:.0f})",
                key=f"spot_buy_{sym}",
                type="primary",
            ):
                try:
                    _trade = _manual_tradeability({"symbol": sym, "direction": "LONG"})
                    if (
                        _trade.get("status") != "executable"
                        or _trade.get("lane") != "spot"
                        or not _trade.get("manual_executable", 0)
                    ):
                        st.session_state["spot_manual_message"] = {
                            "level": "error",
                            "text": _trade.get(
                                "blocked_reason", "execution_policy_unavailable"
                            ),
                        }
                    else:
                        try:
                            from runtime.spot_momentum import build_spot_state
                        except Exception:
                            build_spot_state = None

                        _spot_state = None
                        if build_spot_state is not None:
                            try:
                                _spot_state = build_spot_state(sym)
                            except Exception:
                                _spot_state = None

                        pos_result = _se.open_spot(
                            sym,
                            size_input,
                            paper=_exec_paper,
                            composite_score=float(scan.get("score") or 0.0),
                            spot_state=_spot_state,
                            final_spot_score=float(score or 0.0),
                        )
                        if pos_result:
                            st.session_state["spot_manual_message"] = {
                                "level": "success",
                                "text": (
                                    f"[{mode_label}][MANUAL] Bought {sym}: "
                                    f"{pos_result['qty']:.6f} units @ ${pos_result['entry']:.4f}  "
                                    f"route={pos_result.get('execution_route', '')} "
                                    f"target={pos_result.get('target_r', 0):.2f}R"
                                ),
                            }
                        else:
                            st.session_state["spot_manual_message"] = {
                                "level": "error",
                                "text": f"open_spot {sym} returned None — broker ack/persist/reconcile failed",
                            }
                except Exception as ex:
                    st.session_state["spot_manual_message"] = {
                        "level": "error",
                        "text": f"Buy error: {ex}",
                    }
                st.rerun()
