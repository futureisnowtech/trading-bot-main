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
            "Check the **Trade?** box on rows you want to execute, then click Execute."
        )
        return

    if st.button(f"Execute {n_sel} Trade(s)", type="primary", key="manual_execute_btn"):
        # ── Determine paper/live mode from runtime state ───────────────────────
        try:
            from db import _runtime_paper_flag as _rflag

            _exec_paper = bool(_rflag())
        except Exception:
            try:
                sys.path.insert(0, _ROOT)
                from config import PAPER_TRADING

                _exec_paper = bool(PAPER_TRADING)
            except Exception:
                _exec_paper = True

        # ── Get real account balance ───────────────────────────────────────────
        # In live mode use the Coinbase API balance (not config ACCOUNT_SIZE).
        # In paper mode use the DB-computed paper equity.
        if not _exec_paper:
            try:
                from data.balance import get_coinbase_balance

                _cb = get_coinbase_balance()
                if _cb.get("connected") and _cb.get("balance", 0) > 0:
                    _acct_balance = float(_cb["balance"])
                else:
                    _acct_balance, _, _ = get_account()
            except Exception:
                _acct_balance, _, _ = get_account()
        else:
            _acct_balance, _, _ = get_account()

        # ── Load get_candles via explicit path (avoids dashboard/data namespace) ─
        try:
            _hd_spec = _ilu.spec_from_file_location(
                "_root_data_historical_data",
                os.path.join(_ROOT, "data", "historical_data.py"),
            )
            _hd_mod = _ilu.module_from_spec(_hd_spec)  # type: ignore[arg-type]
            _hd_spec.loader.exec_module(_hd_mod)  # type: ignore[union-attr]
            get_candles = _hd_mod.get_candles
        except Exception as _imp_err:
            st.error(f"Cannot load candle data module: {_imp_err}")
            return

        if _ROOT not in sys.path:
            sys.path.insert(0, _ROOT)
        import perps_engine as perps

        # Symbols already held in live DB — block duplicate entries
        _held_syms = {p.get("symbol", "") for p in _open_pos}
        # Track symbols entered in this batch to catch same-underlying conflicts
        # (e.g. PF_SOLUSD + SOL both map to SOL, or LONG + SHORT same coin selected)
        _batch_syms: set = set()

        results = []
        for idx in selected_idx:
            cand = candidates[idx]
            sym = cand["symbol"]
            dirn = cand["direction"]
            setup = cand.get("primary_setup", "manual")
            _policy = _get_tier_info(sym)
            if not _policy["execute"]:
                results.append((sym, dirn, False, f"blocked: {_policy['tier']}"))
                continue
            try:
                # Normalise to underlying symbol for execution and price/candle fetching.
                # PF_SOLUSD → SOL, PF_ETHUSD → ETH, etc.
                exec_sym = _policy.get("underlying", sym)

                # Guard: already holding this underlying in live DB
                if not _exec_paper and exec_sym in _held_syms:
                    results.append(
                        (
                            sym,
                            dirn,
                            False,
                            f"already holding {exec_sym} — close existing position first",
                        )
                    )
                    continue

                # Guard: same underlying already in this execution batch
                # (catches LONG + SHORT same coin, or PF_SOLUSD + SOL both checked)
                if exec_sym in _batch_syms:
                    results.append(
                        (
                            sym,
                            dirn,
                            False,
                            f"conflict: {exec_sym} already queued in this batch",
                        )
                    )
                    continue

                # ── Fetch candles: try exec_sym first, fall back to raw sym ──
                df_c = None
                for _fsym in [exec_sym, sym] if exec_sym != sym else [sym]:
                    try:
                        _df = get_candles(_fsym, "1h", 100)
                        if _df is not None and len(_df) >= 10:
                            df_c = _df
                            break
                    except Exception:
                        pass
                if df_c is None or len(df_c) < 10:
                    results.append((sym, dirn, False, "insufficient candle data"))
                    continue

                candle_price = float(df_c["close"].iloc[-1])

                # Live price: try exec_sym first, then raw sym
                live_now = get_live_prices([exec_sym]).get(
                    exec_sym, 0
                ) or get_live_prices([sym]).get(sym, 0)
                if live_now > 0:
                    ratio = candle_price / live_now
                    if 0.95 <= ratio <= 1.05:
                        price = candle_price
                    else:
                        price = live_now
                        st.warning(
                            f"⚠️ {exec_sym}: candle ${candle_price:.5g} off by "
                            f"{abs(ratio - 1) * 100:.0f}% from live ${live_now:.5g} — using live price"
                        )
                else:
                    price = candle_price

                if price <= 0:
                    results.append(
                        (sym, dirn, False, "could not determine valid entry price")
                    )
                    continue

                atr_7 = float(df_c["high"].sub(df_c["low"]).tail(7).mean())
                if atr_7 <= 0 or (
                    live_now > 0 and abs(candle_price / live_now - 1) > 0.10
                ):
                    atr_7 = price * 0.015

                stop_dist = max(atr_7 * 1.5, price * 0.008)
                target_dist = stop_dist * 3.0
                composite = cand.get("composite_score", 50.0)

                from position_manager import compute_position_size

                _open_pos = get_open_positions()
                _deployed = sum(
                    float(p.get("qty", 0)) * float(p.get("entry", 0)) for p in _open_pos
                )
                sizing = compute_position_size(
                    account_balance=_acct_balance,
                    current_price=price,
                    atr_7=atr_7,
                    stop_multiplier=1.5,
                    ml_score=composite,
                    composite_score=composite,
                    deployed_usd=_deployed,
                    paper=_exec_paper,
                )
                pos_usd = sizing["position_usd"]
                leverage = sizing["leverage"]

                # ── Live-mode size guards ──────────────────────────────────────
                if not _exec_paper:
                    _min_usd = _min_contract_usd(exec_sym, price)
                    # Hard cap: no single trade > 15% of account. If even the
                    # minimum 1-contract cost exceeds that, the account is too
                    # small for this instrument — skip rather than force a huge bet.
                    _max_single = round(_acct_balance * 0.15, 2)
                    if _min_usd > 0 and _min_usd > _max_single:
                        results.append(
                            (
                                sym,
                                dirn,
                                False,
                                f"min contract ${_min_usd:.0f} exceeds 15% account "
                                f"cap ${_max_single:.0f} — account too small for {exec_sym}",
                            )
                        )
                        continue
                    # Bump up to 1-contract minimum if signal sizing falls short.
                    if _min_usd > 0 and pos_usd < _min_usd:
                        _original_pos = pos_usd
                        pos_usd = round(_min_usd * 1.02, 2)
                        st.info(
                            f"ℹ️ {exec_sym}: signal sizing ${_original_pos:.0f} < "
                            f"1 contract (≈${_min_usd:.0f}) — using ${pos_usd:.0f}"
                        )
                    # Apply per-trade cap regardless (catches cases where signal
                    # sizing itself is oversized).
                    if pos_usd > _max_single:
                        pos_usd = _max_single

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
                        composite_score=composite,
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
                        composite_score=composite,
                        atr_at_entry=atr_7,
                        regime="UNKNOWN",
                        entry_setup=f"manual_{setup}",
                        paper=_exec_paper,
                    )

                if pos:
                    mode_tag = "PAPER" if _exec_paper else "LIVE"
                    results.append(
                        (
                            sym,
                            dirn,
                            True,
                            f"[{mode_tag}] entered @ {price:.6g}  "
                            f"stop={stop_p:.6g}  target={target_p:.6g}  "
                            f"size=${pos_usd:.0f}  lev={leverage}x",
                        )
                    )
                    # Register underlying so subsequent batch entries on same
                    # coin are blocked (prevents LONG+SHORT same asset in one click).
                    _batch_syms.add(exec_sym)
                    _held_syms.add(exec_sym)
                else:
                    # Explain why the broker returned None
                    if not _exec_paper:
                        _min_usd = _min_contract_usd(exec_sym, price)
                        _why = (
                            f"size ${pos_usd:.0f} < 1 contract (≈${_min_usd:.0f})"
                            if _min_usd > 0 and pos_usd < _min_usd
                            else "broker returned None — check logs/service/manual_live_bot.log"
                        )
                        results.append((sym, dirn, False, _why))
                    else:
                        results.append(
                            (sym, dirn, False, "open_long/short returned None")
                        )

            except Exception as e:
                results.append((sym, dirn, False, str(e)[:200]))

        # ── Store results persistently and rerender ────────────────────────────
        st.session_state["manual_results"] = results
        st.session_state.pop("manual_candidates", None)
        st.session_state.pop("manual_scan_time", None)
        st.rerun()
