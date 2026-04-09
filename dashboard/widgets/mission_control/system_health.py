"""
Widget: System Health
Question: Is the bot alive, scanning, and error-free right now?
Tab: MISSION CONTROL
Refresh: 10s
Asset class: BOTH
"""

import streamlit as st
from datetime import datetime

from formatters import _status_dot, _age_color
from data.health import (
    get_health_status,
    get_heartbeat_age,
    get_error_rate_1h,
    get_restart_count_24h,
    get_ml_status,
)
from data.scanner_data import get_last_scan_age


@st.fragment(run_every=10)
def render_system_integrity():
    st.markdown(
        '<div class="panel-title">System Integrity</div>', unsafe_allow_html=True
    )

    health = get_health_status()
    scan_age = get_last_scan_age()
    heartbeat_age = get_heartbeat_age()
    error_rate = get_error_rate_1h()
    restart_count = get_restart_count_24h()
    ml = get_ml_status()

    scan_color = _age_color(scan_age, warn=120, crit=300)
    hb_color = _age_color(heartbeat_age, warn=360, crit=720)
    err_color = "green" if error_rate == 0 else ("yellow" if error_rate <= 5 else "red")
    health_status = health.get("status", "UNKNOWN")
    score = health.get("score", 0)
    total_checks = health.get("total", 6)

    if health_status == "UNHEALTHY" or err_color == "red" or scan_color == "red":
        overall_color = "red"
        overall_label = "CRITICAL"
    elif health_status == "DEGRADED" or err_color == "yellow" or scan_color == "yellow":
        overall_color = "yellow"
        overall_label = "DEGRADED"
    else:
        overall_color = "green"
        overall_label = "HEALTHY"

    st.markdown(
        f'{_status_dot(overall_color)} <span style="font-size:1.2em; font-weight:700; color: {"#4ade80" if overall_color == "green" else "#facc15" if overall_color == "yellow" else "#f87171"}">{overall_label}</span>'
        f' &nbsp; <span style="color:#94a3b8; font-size:0.85em">{score}/{total_checks} checks passing</span>',
        unsafe_allow_html=True,
    )

    rows_html = ""
    checks = [
        (
            "Health checks",
            f"{score}/{total_checks}",
            "green"
            if health_status == "HEALTHY"
            else ("yellow" if health_status == "DEGRADED" else "red"),
        ),
        ("Last scan", f"{scan_age}s ago" if scan_age < 9999 else "no data", scan_color),
        (
            "Heartbeat",
            f"{heartbeat_age}s ago" if heartbeat_age < 9999 else "no data",
            hb_color,
        ),
        ("Errors (1h)", str(error_rate), err_color),
        (
            "ML gate",
            "loaded"
            if ml["snapshots"] >= ml["min_needed"]
            else f"{ml['snapshots']}/{ml['min_needed']} snaps",
            "green" if ml["snapshots"] >= ml["min_needed"] else "yellow",
        ),
        (
            "Restarts (24h)",
            str(restart_count),
            "green" if restart_count <= 1 else "yellow",
        ),
    ]
    for label, val, color in checks:
        dot = _status_dot(color)
        rows_html += f'<div style="display:flex; justify-content:space-between; margin:2px 0; font-size:0.82em"><span style="color:#94a3b8">{dot} {label}</span><span style="color:#e2e8f0; font-weight:600">{val}</span></div>'

    st.markdown(rows_html, unsafe_allow_html=True)
    st.caption(f"Updated {datetime.now().strftime('%H:%M:%S')}")
