"""
dashboard/widgets/pages/engineering_console.py — ENGINEERING CONSOLE page.

Sections:
  A. Engineering truth summary (version, proof count, integrity)
  B. Control-plane internals (master_control)
  C. Raw config/thresholds (dev_config)
  D. Archived Futures (MES) — in expander, visually deprioritized
  E. Raw engineering panels (system_integrity, failure_modes, decision_quality)
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

from data.engineering_console import get_engineering_truth_summary


def render_engineering_console():
    st.caption(
        "All tuning knobs, signal scoring rules, raw system constants, and archived subsystems."
    )

    # ── A. Engineering truth summary ───────────────────────────────────────────
    summary = get_engineering_truth_summary()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Version", summary.get("version", "unknown"))

    proof = summary.get("proof_count")
    c2.metric("Last proof run", proof if proof else "none recorded")

    integrity = summary.get("integrity_summary") or {}
    verified = integrity.get("verified", 0)
    total = integrity.get("total_closes", 0)
    c3.metric("Verified closes", f"{verified}/{total}" if total else "0/0")

    runtime_ts = summary.get("runtime_truth_age")
    c4.metric("Runtime state since", runtime_ts[:16] if runtime_ts else "unknown")

    st.divider()

    # ── B. Control-plane internals ─────────────────────────────────────────────
    try:
        from widgets.system_settings.master_control import render_master_control

        render_master_control()
    except Exception as e:
        st.caption(f"Master control unavailable: {e}")

    st.divider()

    # ── C. Raw config / thresholds ─────────────────────────────────────────────
    try:
        from widgets.system_settings.dev_config import render_dev_config

        render_dev_config()
    except Exception as e:
        st.caption(f"Dev config unavailable: {e}")

    st.divider()

    # ── D. Archived Futures (MES) ──────────────────────────────────────────────
    with st.expander("Archived MES Futures", expanded=False):
        st.markdown(
            """
<div style="background:rgba(100,116,139,0.08); border-left:3px solid #64748b;
            padding:10px 14px; border-radius:4px; margin-bottom:12px; font-size:0.85em;">
<strong style="color:#64748b">ARCHIVED · S&amp;P 500 FUTURES · MES</strong><br>
This lane is <strong>dormant</strong> — not the active live lane.
All code, history, and configuration is preserved for reactivation.<br>
Traded <strong>Micro E-mini S&amp;P 500 (MES)</strong> via IBKR (paper port 7497).<br>
Strategies: <strong>Opening Range Breakout</strong> + <strong>VWAP Mean Reversion</strong>.<br>
To reactivate: set <code>FUTURES_LANE_ACTIVE=true</code> and restart the bot.
</div>
""",
            unsafe_allow_html=True,
        )
        try:
            from widgets.futures.mes_dashboard import render_futures

            render_futures()
        except Exception as e:
            st.caption(f"MES dashboard unavailable: {e}")

    # ── E. Raw engineering panels ──────────────────────────────────────────────
    with st.expander("System integrity checks", expanded=False):
        try:
            from widgets.mission_control.system_health import render_system_integrity

            render_system_integrity()
        except Exception as e:
            st.caption(f"System integrity unavailable: {e}")

    with st.expander("Failure modes (7d)", expanded=False):
        try:
            from widgets.mission_control.failure_modes import render_failures_compact

            render_failures_compact()
        except Exception as e:
            st.caption(f"Failure modes unavailable: {e}")

    with st.expander("Decision quality", expanded=False):
        try:
            from widgets.mission_control.decision_quality import render_decision_quality

            render_decision_quality()
        except Exception as e:
            st.caption(f"Decision quality unavailable: {e}")
