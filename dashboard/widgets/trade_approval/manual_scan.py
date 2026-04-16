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
        "Runs a fresh scan (bypasses the 5-min cache). You pick which trades execute."
    )

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        run_scan = st.button("Run Scan Now", type="primary", key="manual_scan_btn")
    with col_info:
        last_ts = st.session_state.get("manual_scan_time")
        if last_ts:
            st.caption(f"Last scan: {last_ts}")

    if run_scan:
        with st.spinner("Scanning Kraken + Hyperliquid (~5–10s)…"):
            try:
                import importlib

                sys.path.insert(0, _ROOT)
                import scanner as _scanner_mod

                importlib.reload(_scanner_mod)
                candidates = _scanner_mod.scan(account_balance=5000.0, force=True)
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

        col1, col2, col3, col4 = st.columns([0.4, 3.2, 2.8, 0.6])
        with col1:
            st.checkbox("", key=f"ms_sel_{i}", label_visibility="collapsed")
        with col2:
            st.markdown(f"**{sym}** `{dirn}` {badge} `{exch[:5].upper()}` · *{setup}*")
        with col3:
            label = f"{prob:.0f}% — {'High edge' if prob >= 68 else ('Moderate edge' if prob >= 60 else 'Lower edge')}"
            st.progress(prob / 100.0, text=label)
        with col4:
            with st.expander("ℹ️"):
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
        # Load get_candles from repo-root data/historical_data.py via explicit path.
        # We CANNOT use `from data.historical_data import get_candles` here because the
        # dashboard process caches `data` as `dashboard/data` (sys.path puts dashboard/
        # first; __init__.py makes it a package). dashboard/data has no historical_data.py.
        # importlib.util bypasses the sys.modules cache entirely.
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

        import perps_engine as perps

        results = []
        for idx in selected_idx:
            cand = candidates[idx]
            sym = cand["symbol"]
            dirn = cand["direction"]
            setup = cand.get("primary_setup", "manual")
            try:
                df_c = get_candles(sym, "1h", 100)
                if df_c is None or len(df_c) < 10:
                    results.append((sym, dirn, False, "insufficient candle data"))
                    continue
                candle_price = float(df_c["close"].iloc[-1])
                live_now = get_live_prices([sym]).get(sym, 0)
                if live_now > 0:
                    ratio = candle_price / live_now
                    if 0.95 <= ratio <= 1.05:
                        price = candle_price
                    else:
                        price = live_now
                        st.warning(
                            f"⚠️ {sym}: candle price ${candle_price:.5g} off by {abs(ratio - 1) * 100:.0f}% — using live ${live_now:.5g}"
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

                balance, _, _b = get_account()
                _open_pos = get_open_positions()
                _deployed = sum(
                    float(p.get("qty", 0)) * float(p.get("entry", 0)) for p in _open_pos
                )
                sizing = compute_position_size(
                    account_balance=balance,
                    current_price=price,
                    atr_7=atr_7,
                    stop_multiplier=1.5,
                    ml_score=composite,
                    composite_score=composite,
                    deployed_usd=_deployed,
                    paper=True,
                )
                pos_usd = sizing["position_usd"]
                leverage = sizing["leverage"]
                if dirn == "LONG":
                    stop_p = round(price - stop_dist, 6)
                    target_p = round(price + target_dist, 6)
                    pos = perps.open_long(
                        symbol=sym,
                        position_usd=pos_usd,
                        entry_price=price,
                        stop_price=stop_p,
                        take_profit_price=target_p,
                        leverage=leverage,
                        composite_score=composite,
                        atr_at_entry=atr_7,
                        regime="UNKNOWN",
                        entry_setup=f"manual_{setup}",
                        paper=True,
                    )
                else:
                    stop_p = round(price + stop_dist, 6)
                    target_p = round(price - target_dist, 6)
                    pos = perps.open_short(
                        symbol=sym,
                        position_usd=pos_usd,
                        entry_price=price,
                        stop_price=stop_p,
                        take_profit_price=target_p,
                        leverage=leverage,
                        composite_score=composite,
                        atr_at_entry=atr_7,
                        regime="UNKNOWN",
                        entry_setup=f"manual_{setup}",
                        paper=True,
                    )
                if pos:
                    results.append(
                        (
                            sym,
                            dirn,
                            True,
                            f"entered @ {price:.6g}  stop={stop_p:.6g}  target={target_p:.6g}  size=${pos_usd:.0f}  lev={leverage}x",
                        )
                    )
                else:
                    results.append((sym, dirn, False, "open_long/short returned None"))
            except Exception as e:
                results.append((sym, dirn, False, str(e)[:120]))

        for sym, dirn, ok, msg in results:
            st.write(f"{'✅' if ok else '❌'} **{sym} {dirn}** — {msg}")

        st.session_state.pop("manual_candidates", None)
        st.session_state.pop("manual_scan_time", None)
