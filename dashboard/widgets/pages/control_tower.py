"""
dashboard/widgets/pages/control_tower.py — CONTROL TOWER page (default landing).

Sections:
  A. Global status strip — mode, alive, lanes, scan age, incidents
  B. Summary card row — portfolio, funnel, problem split, action needed
  C. Why trades are dying — stage funnel + top blockers
  D. Live opportunities now — executable scan_candidates
  E. Open positions
  F. Lane health strip
  G. Action center / alert feed
"""

import os
import sys

_PAGES_DIR = os.path.dirname(os.path.abspath(__file__))
_WIDGETS_DIR = os.path.dirname(_PAGES_DIR)
_DASH_DIR = os.path.dirname(_WIDGETS_DIR)
_ROOT = os.path.dirname(_DASH_DIR)

for _p in (_DASH_DIR, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import streamlit as st

from data.control_tower import get_control_tower_snapshot
from data.crypto_dashboard import get_crypto_opportunity_board
from data.health import get_heartbeat_age
from db import _q, _q1, _runtime_paper_flag
from formatters import _time_ago


def _age_label(seconds: int) -> str:
    if seconds >= 9999:
        return "UNKNOWN"
    if seconds < 60:
        return f"{seconds}s ago"
    return f"{seconds // 60}m ago"


def _badge(label: str, color: str) -> str:
    return (
        f'<span style="background:rgba({color},0.15);color:rgb({color});'
        f"padding:2px 8px;border-radius:4px;font-size:0.8em;font-weight:700;"
        f'display:inline-block;">{label}</span>'
    )


def render_control_tower():
    window = st.selectbox("Window", ["1h", "24h", "7d"], index=1, key="ct_window")
    window_hours = {"1h": 1, "24h": 24, "7d": 168}[window]

    # All windowed panels (funnel, lifecycle, blockers, opportunities, action items)
    # use window_hours. Non-windowed panels (positions, lane health, heartbeat) are
    # always current and labeled "as of now" in their headings.
    snap = get_control_tower_snapshot(hours=window_hours)
    crypto_funnel = snap.get("crypto_funnel") or {}

    st.divider()

    # ── A. Global status strip ─────────────────────────────────────────────────
    st.markdown("**System Status**")
    c1, c2, c3, c4, c5, c6 = st.columns(6)

    mode = snap.get("runtime_mode", "UNKNOWN")
    c1.metric("Mode", mode)

    hb = snap.get("heartbeat_age", 9999)
    alive_label = "ALIVE" if hb < 120 else "STALE"
    alive_delta = f"{_age_label(hb)}"
    c2.metric("Bot", alive_label, delta=alive_delta, delta_color="off")

    # Crypto lane from lane_runtime_state
    crypto_lane = _q1(
        "SELECT health, active FROM lane_runtime_state WHERE lane_id='crypto' ORDER BY id DESC LIMIT 1"
    )
    crypto_health = crypto_lane.get("health") or "UNKNOWN"
    c3.metric("Crypto lane", crypto_health)

    # Forecast lane
    fc_lane = _q1(
        "SELECT readiness_state, active FROM lane_runtime_state WHERE lane_id='forecast' ORDER BY id DESC LIMIT 1"
    )
    fc_state = fc_lane.get("readiness_state") or (
        "ACTIVE" if fc_lane.get("active") else "INACTIVE"
    )
    c4.metric("Forecast lane", fc_state)

    # Last scan age
    try:
        from data.scanner_data import get_last_scan_age

        scan_age = get_last_scan_age()
        c5.metric("Last scan", _age_label(scan_age) if scan_age else "UNKNOWN")
    except Exception:
        c5.metric("Last scan", "UNKNOWN")

    # Open incidents
    incidents = snap.get("incident_count", 0)
    c6.metric("Open incidents", incidents)

    st.divider()

    # ── B. Summary card row ────────────────────────────────────────────────────
    b1, b2, b3, b4 = st.columns(4)

    with b1:
        st.markdown("**Portfolio Snapshot**")
        equity = snap.get("account_equity", 0.0)
        daily_pnl = snap.get("daily_pnl", 0.0)
        deployed_pct = snap.get("deployed_pct", 0.0)
        open_count = len(snap.get("open_positions", []))
        st.metric("Equity", f"${equity:,.2f}")
        st.metric("Daily P&L", f"${daily_pnl:+.2f}")
        st.metric(
            "Deployed",
            f"{deployed_pct:.1f}%",
            help="% of equity in open perp positions",
        )
        st.metric("Open positions", open_count)

    with b2:
        st.markdown("**Trade Funnel**")
        funnel = crypto_funnel.get("funnel") or {}
        scanned = int(funnel.get("scanner_candidates_total") or 0)
        entered = int(funnel.get("entered") or 0)
        scored = int(funnel.get("scored_total") or 0)
        conv = f"{crypto_funnel.get('conversion_pct', 0.0):.1f}%"
        st.metric("Scanned", scanned)
        st.metric("Scored", scored)
        st.metric("Entered", entered)
        st.metric("Conversion", conv)

    with b3:
        st.markdown("**Problem Split**")
        issue = crypto_funnel.get("issue_breakdown") or {}
        strategy_n = issue.get("strategy", 0)
        system_n = issue.get("system", 0)
        bug_n = issue.get("bug", 0)
        st.metric("Strategy rejects", strategy_n, help="below_threshold + econ_veto")
        st.metric("System blocks", system_n, help="policy/risk/cooldown blocks")
        st.metric("Bug/data flags", bug_n, help="data_unavailable + execution_failed")

    with b4:
        st.markdown("**Action Needed**")
        actions = snap.get("action_items") or []
        if actions:
            for a in actions[:4]:
                st.warning(a)
        else:
            st.success("No action items")

    st.divider()

    # ── C. Candidate lifecycle funnel (standardized 8-stage) ─────────────────
    # Primary view: where candidates die across routing / sizing / execution.
    # Secondary view: coarse blocker breakdown (strategy / system / bug).
    left, right = st.columns([1.2, 0.8])

    with left:
        st.markdown(f"**Candidate Lifecycle — last {window}**")
        lifecycle = snap.get("lifecycle_stages") or []
        if lifecycle:
            # Compute max for relative bar width
            max_count = max((s.get("count", 0) for s in lifecycle), default=1) or 1
            _lc_colors = {
                "discovered": "#94a3b8",
                "signal_pass": "#facc15",
                "econ_pass": "#fb923c",
                "route_decided": "#60a5fa",
                "size_pass": "#a78bfa",
                "execution_attempted": "#38bdf8",
                "position_open": "#4ade80",
                "exit_complete": "#86efac",
            }
            for s in lifecycle:
                stage = s.get("stage", "")
                count = s.get("count", 0)
                derived = s.get("derived", False)
                color = _lc_colors.get(stage, "#94a3b8")
                bar_pct = int(count / max_count * 100) if max_count else 0
                derived_note = " ·derived" if derived else ""
                st.markdown(
                    f'<div style="margin-bottom:3px;">'
                    f'<div style="display:flex;justify-content:space-between;'
                    f"padding:3px 8px;border-left:3px solid {color};"
                    f'background:rgba(0,0,0,0.10);border-radius:2px;">'
                    f'<span style="color:{color};font-size:0.82em;">'
                    f'{stage}<span style="color:#475569;font-size:0.85em;">{derived_note}</span>'
                    f"</span>"
                    f'<strong style="color:{color}">{count}</strong></div>'
                    f'<div style="height:3px;width:{bar_pct}%;background:{color};'
                    f'opacity:0.35;border-radius:0 0 2px 2px;"></div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )
            st.caption(
                "·derived = computed from existing persisted fields, "
                "not a dedicated persisted column"
            )
        else:
            st.caption("No lifecycle data for this window.")

    with right:
        st.markdown(f"**Where candidates die — last {window}**")
        issue = crypto_funnel.get("issue_breakdown") or {}
        _issue_colors = {
            "strategy": ("#facc15", "Signal / economics"),
            "system": ("#60a5fa", "Policy / routing"),
            "bug": ("#f87171", "Bug / data"),
            "success": ("#4ade80", "Entered"),
        }
        for key, (color, label) in _issue_colors.items():
            n = issue.get(key, 0)
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;'
                f"padding:3px 8px;margin-bottom:2px;border-left:3px solid {color};"
                f'background:rgba(0,0,0,0.08);border-radius:2px;">'
                f'<span style="color:{color};font-size:0.82em;">{label}</span>'
                f'<strong style="color:{color}">{n}</strong></div>',
                unsafe_allow_html=True,
            )

        st.markdown(f"**Top blockers — last {window}**")
        blockers = crypto_funnel.get("top_blockers") or []
        if blockers:
            for b in blockers[:6]:
                reason = b.get("reason") or "unknown"
                n = b.get("n", 0)
                st.caption(f"`{reason}` — **{n}**")
        else:
            st.caption("No blocker data.")

    st.divider()

    # ── D. Live opportunities now ──────────────────────────────────────────────
    st.markdown("**Live Opportunities (Executable)**")
    try:
        board = get_crypto_opportunity_board(hours=window_hours)
        executable = [r for r in board if r.get("status") == "executable"][:10]
        if executable:
            cols = [
                "symbol",
                "underlying",
                "direction",
                "recommended_lane",
                "score",
                "auto_executable",
            ]
            import pandas as pd

            df = pd.DataFrame(executable)[cols]
            df.columns = ["Symbol", "Underlying", "Direction", "Lane", "Score", "Auto"]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.caption("No currently executable candidates in this window.")
    except Exception as e:
        st.caption(f"Opportunity board unavailable: {e}")

    st.divider()

    # ── E. Open positions ──────────────────────────────────────────────────────
    try:
        from widgets.mission_control.open_positions import render_positions_compact

        render_positions_compact()
    except Exception as e:
        st.caption(f"Positions widget unavailable: {e}")

    st.divider()

    # ── F. Lane health strip ───────────────────────────────────────────────────
    st.markdown("**Lane Health**")
    lanes = _q(
        "SELECT lane_id, enabled, active, health, readiness_state, last_heartbeat_at "
        "FROM lane_runtime_state ORDER BY id DESC"
    )
    # Deduplicate — keep most recent row per lane_id
    seen: set = set()
    lane_rows: list = []
    for lane in lanes:
        lid = lane.get("lane_id")
        if lid and lid not in seen:
            seen.add(lid)
            lane_rows.append(lane)

    if lane_rows:
        lane_cols = st.columns(len(lane_rows))
        for i, lane in enumerate(lane_rows):
            with lane_cols[i]:
                lid = lane.get("lane_id", "unknown")
                health = lane.get("health") or "UNKNOWN"
                readiness = lane.get("readiness_state") or ""
                hb_ts = lane.get("last_heartbeat_at")
                hb_age = _time_ago(hb_ts) if hb_ts else "never"
                st.markdown(f"**{lid.upper()}**")
                st.caption(f"Health: {health}")
                if readiness:
                    st.caption(f"Readiness: {readiness}")
                st.caption(f"Heartbeat: {hb_age}")
    else:
        st.caption("Lane runtime state not available.")

    st.divider()

    # ── G. Action center / alert feed ─────────────────────────────────────────
    try:
        from widgets.mission_control.alert_feed import render_alert_feed

        render_alert_feed()
    except Exception as e:
        st.caption(f"Alert feed unavailable: {e}")
