"""
dashboard/widgets/pages/crypto_page.py — CRYPTO page.

Sections:
  A. Crypto header strip
  B. Crypto Opportunity Board (filterable/sortable)
  C. Manual Trade Console
  D. Open Crypto Positions
  E. Crypto Failure Diagnostics (expanders)
  F. Scanner/route detail (expander)
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

from data.crypto_dashboard import (
    get_crypto_header,
    get_crypto_opportunity_board,
    get_crypto_failure_summary,
)


def render_crypto_page():
    # ── A. Crypto header strip ─────────────────────────────────────────────────
    hdr = get_crypto_header()

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Lane health", hdr.get("lane_health", "UNKNOWN"))
    c2.metric("Mode", hdr.get("mode_label", "UNKNOWN"))
    c3.metric("Perp lane", "ACTIVE" if hdr.get("perp_active") else "INACTIVE")
    c4.metric("Spot lane", "ACTIVE" if hdr.get("spot_active") else "INACTIVE")
    c5.metric("Buying power", f"${hdr.get('buying_power', 0.0):,.0f}")
    c6.metric("Open positions", hdr.get("open_count", 0))

    st.divider()

    # ── B. Crypto Opportunity Board ────────────────────────────────────────────
    st.subheader("Crypto Opportunity Board")

    hours_sel = st.selectbox(
        "Window", ["1h", "6h", "24h", "7d"], index=2, key="crypto_board_hours"
    )
    hours_map = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
    board_hours = hours_map[hours_sel]

    filter_sel = st.radio(
        "Filter",
        ["All", "Executable", "Spot", "Perp", "Blocked", "Manual-only"],
        horizontal=True,
        key="crypto_board_filter",
    )
    sort_sel = st.selectbox(
        "Sort",
        ["Newest", "Executable first", "Highest score", "Blocked reason"],
        key="crypto_board_sort",
    )

    rows = get_crypto_opportunity_board(hours=board_hours)

    # Apply filter
    if filter_sel == "Executable":
        rows = [r for r in rows if r.get("status") == "executable"]
    elif filter_sel == "Spot":
        rows = [r for r in rows if r.get("recommended_lane") == "spot"]
    elif filter_sel == "Perp":
        rows = [r for r in rows if r.get("recommended_lane") == "perp"]
    elif filter_sel == "Blocked":
        rows = [r for r in rows if r.get("status") == "blocked"]
    elif filter_sel == "Manual-only":
        rows = [
            r
            for r in rows
            if r.get("manual_executable") and not r.get("auto_executable")
        ]

    # Apply sort
    if sort_sel == "Executable first":
        rows = sorted(
            rows,
            key=lambda r: (
                0 if r.get("status") == "executable" else 1,
                r.get("ts", ""),
            ),
            reverse=False,
        )
        rows = sorted(rows, key=lambda r: r.get("status") != "executable")
    elif sort_sel == "Highest score":
        rows = sorted(rows, key=lambda r: float(r.get("score") or 0), reverse=True)
    elif sort_sel == "Blocked reason":
        rows = sorted(
            rows, key=lambda r: r.get("trade_blocked_reason") or r.get("decision") or ""
        )
    # Default: already ordered newest first from query

    if rows:
        for row in rows[:50]:
            status = row.get("status", "not_evaluated")
            sym = row.get("symbol", "")
            underlying = row.get("underlying", "")
            direction = row.get("direction", "")
            lane = row.get("recommended_lane", "")
            score = float(row.get("score") or 0)
            auto_ex = bool(row.get("auto_executable"))
            manual_ex = bool(row.get("manual_executable"))
            blocked = row.get("trade_blocked_reason") or row.get("decision") or ""
            ts = row.get("ts", "")

            if status == "executable":
                badge_color = "#4ade80"
                badge_label = "EXECUTABLE"
            elif status == "blocked":
                badge_color = "#f87171"
                badge_label = "BLOCKED"
            else:
                badge_color = "#94a3b8"
                badge_label = status.upper().replace("_", " ")

            auto_badge = (
                '<span style="color:#60a5fa;font-size:0.75em;font-weight:700;">AUTO</span>'
                if auto_ex
                else ""
            )
            manual_badge = (
                '<span style="color:#a78bfa;font-size:0.75em;font-weight:700;">MANUAL</span>'
                if manual_ex
                else ""
            )

            detail = f"Lane: **{lane or 'unknown'}** | Score: **{score:.0f}** | {ts[:16] if ts else ''}"
            if blocked and status == "blocked":
                detail += f" | Block: `{blocked}`"

            st.markdown(
                f'<div style="border-left:3px solid {badge_color};padding:4px 10px;'
                f'margin-bottom:4px;background:rgba(0,0,0,0.08);border-radius:2px;">'
                f'<span style="color:{badge_color};font-weight:700;font-size:0.8em;">{badge_label}</span>'
                f"&nbsp;&nbsp;<strong>{underlying or sym}</strong> {direction}"
                f"&nbsp;{auto_badge}&nbsp;{manual_badge}"
                f'<br><span style="color:#94a3b8;font-size:0.8em;">{detail}</span>'
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No candidates in this window / filter.")

    st.divider()

    # ── C. Manual Trade Console ────────────────────────────────────────────────
    st.subheader("Manual Trade Console")
    try:
        from widgets.trade_approval.manual_scan import render_manual_scan

        render_manual_scan()
    except Exception as e:
        st.error(f"Manual scan widget unavailable: {e}")

    st.divider()

    # ── D. Open Crypto Positions ───────────────────────────────────────────────
    try:
        from widgets.mission_control.open_positions import render_positions_compact

        render_positions_compact()
    except Exception as e:
        st.caption(f"Positions widget unavailable: {e}")

    st.divider()

    # ── E. Crypto Failure Diagnostics ─────────────────────────────────────────
    with st.expander("Execution failures", expanded=False):
        try:
            failure_data = get_crypto_failure_summary(hours=board_hours)
            exec_fails = failure_data.get("execution_failures") or []
            if exec_fails:
                for f in exec_fails:
                    st.caption(
                        f"`{f.get('symbol', '')}` {f.get('direction', '')} — "
                        f"{f.get('reason', 'unknown')} @ {f.get('ts', '')[:16]}"
                    )
            else:
                st.success("No execution failures in this window.")
        except Exception as e:
            st.caption(f"Failure data unavailable: {e}")

    with st.expander("Policy / system blocks", expanded=False):
        try:
            failure_data = get_crypto_failure_summary(hours=board_hours)
            policy_blocks = failure_data.get("top_policy_blocks") or []
            if policy_blocks:
                for b in policy_blocks:
                    st.caption(f"`{b.get('reason', 'unknown')}` — **{b.get('n', 0)}**")
            else:
                st.success("No policy blocks in this window.")
        except Exception as e:
            st.caption(f"Policy block data unavailable: {e}")

    # ── F. Scanner/route detail ────────────────────────────────────────────────
    with st.expander("Scanner filter detail", expanded=False):
        try:
            from widgets.trade_approval.scan_breakdown import render_scan_breakdown

            render_scan_breakdown()
        except Exception as e:
            st.caption(f"Scan breakdown unavailable: {e}")
