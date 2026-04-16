"""
Widget: Trade Approval / Manual Scan
Question: Run a fresh scan and hand-pick which trades to execute.
Tab: TRADE APPROVAL
Refresh: manual (button-driven)
Asset class: CRYPTO PERPS
"""

import importlib.util as _ilu
import os
import sys
import streamlit as st
from datetime import datetime

# dashboard/data/ imports — intentionally from dashboard data layer (not repo-root data/)
from data.positions import get_open_positions, get_live_prices
from data.account import get_account

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# _ROOT = repo root (3 levels up from dashboard/widgets/trade_approval/)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))


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
        "(BTC / ETH / SOL / XRP only; bypasses the 5-min cache)."
    )
    st.caption(
        "Unsupported long-tail coins are intentionally excluded here so manual "
        "execution stays aligned with the live Coinbase execution set."
    )

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
                candidates = _scanner_mod.scan(
                    account_balance=5000.0,
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
            except Exception as e:
                st.error(f"Scan failed: {e}")
                return
        n = len(st.session_state.get("manual_candidates", []))
        st.success(f"Found {n} candidates.")

    candidates = st.session_state.get("manual_candidates", [])
    if not candidates:
        st.info("No scan results yet — click **Run Scan Now** above.")
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
        _tier = _get_tier_info(sym)
        _can_execute = _tier["execute"]

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
            _tier_label = _tier.get("tier", "core")
            if _tier_label == "core":
                _badge_str = ":green[CORE]"
            elif _tier_label == "research_only":
                _badge_str = ":orange[RESEARCH ONLY]"
            else:
                _badge_str = ":red[SUPPRESSED]"
            st.markdown(
                f"**{sym}** `{dirn}` {badge} `{exch[:5].upper()}` · *{setup}* {_badge_str}"
            )
            if not _can_execute:
                if _tier.get("reason") == "non_core_execution_universe":
                    st.caption(
                        "Visible for discovery only — not in core execution universe"
                    )
                elif _tier.get("reason") == "suppressed_symbol":
                    st.caption("Suppressed symbol — execution blocked")
                else:
                    st.caption(
                        f"Execution blocked — {_tier.get('reason', 'policy lookup failed')}"
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
        return

    # ── Helper: compute sizing for one candidate (shared by Review + Execute) ──
    def _compute_preview(cand, exec_paper, acct_balance, get_candles_fn, open_pos):
        sym = cand["symbol"]
        dirn = cand["direction"]
        setup = cand.get("primary_setup", "manual")
        policy = _get_tier_info(sym)
        exec_sym = policy.get("underlying", sym)
        prob = _win_prob(cand)

        if not policy["execute"]:
            return {
                "sym": sym,
                "exec_sym": exec_sym,
                "dirn": dirn,
                "setup": setup,
                "blocked": True,
                "block_reason": f"tier={policy['tier']}",
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

        # Size guards (live only)
        size_note = None
        if not exec_paper:
            min_usd = _min_contract_usd(exec_sym, price)
            max_single = round(acct_balance * 0.03, 2)
            if min_usd > 0 and min_usd > max_single:
                return {
                    "sym": sym,
                    "exec_sym": exec_sym,
                    "dirn": dirn,
                    "setup": setup,
                    "blocked": True,
                    "block_reason": f"min contract ${min_usd:.0f} > 3% cap ${max_single:.0f}",
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
        if st.button(f"Review {n_sel} Order(s)", type="primary", key="ms_review_btn"):
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
        return

    # ── PHASE 2: Confirmation panel ───────────────────────────────────────────
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
        return

    st.divider()
    ca, cb = st.columns([1, 1])
    with ca:
        if st.button("Confirm & Execute", type="primary", key="ms_confirm_btn"):
            # ── PHASE 3: Execute confirmed orders ─────────────────────────────
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
                exec_sym = pv["exec_sym"]

                # Re-run guards with fresh held list
                if not _exec_paper and exec_sym in _held_syms:
                    results.append((sym, dirn, False, f"already holding {exec_sym}"))
                    continue
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
            st.session_state.pop("ms_previews", None)
            st.session_state.pop("manual_candidates", None)
            st.session_state.pop("manual_scan_time", None)
            st.rerun()

    with cb:
        if st.button("Cancel", type="secondary", key="ms_cancel_btn"):
            st.session_state.pop("ms_previews", None)
            st.rerun()
