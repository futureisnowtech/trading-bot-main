"""
Widget: Scanner Filter Breakdown
Shows why each of BTC/ETH/SOL/XRP passed or failed each of the 7 scanner steps.
Tab: TRADE APPROVAL
"""

import streamlit as st
from data.scan_trace import run_scan_trace, CORE_SYMBOLS

_STEP_LABELS = {
    "1_universe": "Step 1  Volume & price",
    "2_setup": "Step 2  Setup sub-filters",
    "3_liquidity": "Step 3  Order book depth",
    "4_ev": "Step 4  Expected value",
    "5_correlation": "Step 5  Correlation",
    "6_regime": "Step 6  Regime filter",
    "7_rank": "Step 7  Dedup & rank",
}

_PASS = "🟢"
_FAIL = "🔴"
_SKIP = "⚪"


def _badge(ok, skip=False):
    if skip:
        return _SKIP
    return _PASS if ok else _FAIL


def render_scan_breakdown():
    st.markdown("### Scanner Filter Breakdown")
    st.caption(
        "Live re-trace of BTC/ETH/SOL/XRP through all 7 scanner steps. "
        "Results cached 5 min — hit Refresh to force a new fetch."
    )

    col_btn, col_info = st.columns([1, 5])
    with col_btn:
        force = st.button("🔄 Refresh now", key="scan_trace_refresh")

    if force:
        import dashboard.data.scan_trace as _mod

        _mod._CACHE.clear()

    with st.spinner("Fetching live data…"):
        try:
            from data import scan_trace as _mod

            if force:
                _mod._CACHE.clear()
        except Exception:
            pass
        data = run_scan_trace()

    if "error" in data:
        st.error(f"Scanner import failed: {data['error']}")
        return

    # ── Summary row ──────────────────────────────────────────────────────────
    cols = st.columns(4)
    for i, base in enumerate(CORE_SYMBOLS):
        d = data.get(base, {})
        stopped = d.get("stopped_at")
        exch = d.get("exchange", "?")
        if stopped is None:
            status, color = "ALL STEPS PASS", "#4ade80"
        else:
            status, color = f"STOPPED  step {stopped}", "#f87171"
        cols[i].markdown(
            f"""<div style="background:rgba(255,255,255,0.03);border-radius:8px;
                padding:12px 14px;text-align:center;">
              <div style="font-size:1.1em;font-weight:800;color:#e2e8f0;">{base}</div>
              <div style="font-size:0.72em;color:#64748b;margin:3px 0;">{exch}</div>
              <div style="font-size:0.78em;font-weight:700;color:{color};">{status}</div>
            </div>""",
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Per-step detail ───────────────────────────────────────────────────────
    for step_key, step_label in _STEP_LABELS.items():
        step_num = int(step_key[0])
        st.markdown(f"**{step_label}**")
        cols = st.columns(4)

        for i, base in enumerate(CORE_SYMBOLS):
            d = data.get(base, {})
            stopped = d.get("stopped_at")
            steps = d.get("steps", {})
            step_data = steps.get(step_key)

            with cols[i]:
                # Symbol was blocked before this step
                if stopped is not None and step_num > stopped:
                    st.markdown(
                        "<div style='color:#475569;font-size:0.78em;padding:6px 0;'>"
                        "⚪ skipped (blocked earlier)</div>",
                        unsafe_allow_html=True,
                    )
                    continue

                if step_data is None:
                    st.markdown(
                        "<div style='color:#475569;font-size:0.78em;padding:6px 0;'>"
                        "— no data</div>",
                        unsafe_allow_html=True,
                    )
                    continue

                ok = step_data.get("pass", False)
                icon = _PASS if ok else _FAIL
                fail_reason = step_data.get("fail_reason") or step_data.get("note", "")

                # Step-specific metric lines
                lines = []
                if step_key == "1_universe":
                    vol = step_data.get("vol_usd", 0)
                    min_v = step_data.get("min_vol_usd", 0)
                    price = step_data.get("price", 0)
                    lines.append(f"vol  ${vol / 1e6:.1f}M  (need ${min_v / 1e6:.1f}M)")
                    lines.append(f"price  ${price:,.2f}")

                elif step_key == "2_setup":
                    lines.append(
                        f"ADX  {step_data.get('adx', '?')}  (need ≥15 for mom/KST)"
                    )
                    lines.append(
                        f"vol spike  {step_data.get('vol_spike', '?')}×  (need ≥0.4)"
                    )
                    lines.append(f"1h move  {step_data.get('pm_1h_pct', '?')}%")
                    lines.append(f"VWAP disp  {step_data.get('vwap_disp_pct', '?')}%")
                    lines.append(f"funding/8h  {step_data.get('fund_8h_pct', '?')}%")
                    if ok:
                        for fd in step_data.get("fired_directions", []):
                            lines.append(f"✓ {fd}")
                    # Sub-filter breakdown
                    subs = step_data.get("subs", {})
                    for sk, sv in subs.items():
                        sub_icon = "✅" if sv.get("pass") else "❌"
                        lines.append(f"{sub_icon} {sv['label']}")

                elif step_key == "3_liquidity":
                    bid = step_data.get("bid_depth", 0)
                    ask = step_data.get("ask_depth", 0)
                    spread = step_data.get("spread_pct", 0)
                    min_d = step_data.get("min_depth", 5000)
                    max_s = step_data.get("max_spread", 0.25)
                    lines.append(f"bid depth  ${bid:,.0f}  (need ${min_d:,.0f})")
                    lines.append(f"ask depth  ${ask:,.0f}  (need ${min_d:,.0f})")
                    lines.append(f"spread  {spread:.3f}%  (max {max_s}%)")

                elif step_key == "4_ev":
                    ev = step_data.get("ev_usd", 0)
                    min_ev = step_data.get("min_ev", 0.25)
                    stop = step_data.get("stop_pct", 0)
                    tgt = step_data.get("target_pct", 0)
                    pos = step_data.get("effective_pos_usd", 0)
                    lines.append(f"EV  ${ev:.4f}  (need ${min_ev})")
                    lines.append(f"stop  {stop:.3f}%  →  target  {tgt:.3f}%")
                    lines.append(f"effective pos  ${pos:.2f}")

                elif step_key in ("5_correlation", "6_regime", "7_rank"):
                    lines.append(step_data.get("note", "pass"))

                body = "<br>".join(
                    f"<span style='color:#94a3b8'>{l}</span>" for l in lines
                )
                if not ok and fail_reason:
                    body += (
                        f"<br><span style='color:#f87171;font-weight:600'>"
                        f"✗ {fail_reason}</span>"
                    )

                st.markdown(
                    f"""<div style="background:rgba(255,255,255,0.02);border-left:3px solid
                         {"#4ade80" if ok else "#f87171"};border-radius:4px;
                         padding:8px 10px;font-size:0.76em;line-height:1.7;">
                      <div style="font-weight:700;color:{"#4ade80" if ok else "#f87171"};
                           margin-bottom:4px;">{icon} {"PASS" if ok else "FAIL"}</div>
                      {body}
                    </div>""",
                    unsafe_allow_html=True,
                )

        st.markdown("")  # spacer between steps
