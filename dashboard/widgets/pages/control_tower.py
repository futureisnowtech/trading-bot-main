"""
dashboard/widgets/pages/control_tower.py — MISSION CONTROL page (v17.1 premium layout)

ROW 1  — 4 hero cards: Bot Status · Account Snapshot · Trade Quality · Biggest Issue
ROW 2  — 3-zone: LEFT (positions + scanner + activity) · CENTER (performance panel)
         · RIGHT (risk + decision + execution + learning)
ROW 3  — Lower band: Failure Modes · Funnel Detail · Recent Activity · Trade Log
"""

from __future__ import annotations

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

import ui
from data.control_tower import get_control_tower_snapshot
from formatters import _time_ago


# ── plain-English blocker labels ──────────────────────────────────────────────
_BLOCKER_PLAIN: dict[str, str] = {
    "below_threshold": "Signal score too low",
    "econ_veto": "Economics check failed — fees exceed expected profit",
    "dual_exposure_block": "Already holding this symbol",
    "cooldown_block": "In cooldown after recent trade",
    "risk_block": "Risk limits reached",
    "data_unavailable": "Price or signal data unavailable",
    "sizing_zero": "Position size calculated to $0",
    "execution_failed": "Broker order failed",
    "research_only_block": "Symbol not in live trading universe",
    "perp_not_autonomous_eligible": "Symbol not eligible for autonomous trading",
    "perp_deployment_cap_exceeded": "Capital deployment cap reached",
    "perp_position_limit_reached": "Max open positions reached",
    "spot_deployment_cap_exceeded": "Spot deployment cap reached",
    "perp_opposite_side_block": "Opposite-side position already open",
}


def _plain_blocker(reason: str) -> str:
    return _BLOCKER_PLAIN.get(reason, reason.replace("_", " "))


def _age_label(seconds: int) -> str:
    if seconds >= 9999:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s ago"
    return f"{seconds // 60}m ago"


def _lane_role_label(role: str) -> tuple[str, str]:
    role = (role or "").lower()
    mapping = {
        "primary": ("PRIMARY", "good"),
        "tactical": ("TACTICAL", "watch"),
        "dormant_ready": ("DORMANT READY", "info"),
        "blocked_ready": ("BLOCKED READY", "watch"),
        "archived": ("ARCHIVED", "neutral"),
    }
    return mapping.get(role, (role.replace("_", " ").upper() or "UNKNOWN", "neutral"))


def _derive_biggest_issue(
    snap: dict,
    stats: dict,
    dd: dict,
    ex: dict,
) -> tuple[str, str, str, str]:
    """Return (title, why, action, chip_status)."""
    hb = snap.get("heartbeat_age", 9999)
    err = snap.get("error_count", 0)
    exec_fail = int(
        (snap.get("crypto_funnel") or {})
        .get("decision_counts", {})
        .get("execution_failed", 0)
    )
    pf = stats.get("profit_factor", 0)
    closes = stats.get("closes", 0)
    curr_dd = dd.get("current_dd_pct", 0.0)

    if hb >= 300:
        return (
            "Bot appears offline",
            "No heartbeat in 5+ minutes.",
            "Check if main.py is running.",
            "problem",
        )
    if err > 10:
        return (
            f"{err} errors in last hour",
            "A high error rate can cause missed or bad trades.",
            "Check System → Event Log.",
            "problem",
        )
    if exec_fail >= 2:
        return (
            f"{exec_fail} trade orders failed",
            "Signals were good but broker orders weren't placed.",
            "Check broker connection in System tab.",
            "problem",
        )
    if closes >= 5 and pf < 1.0:
        return (
            "Strategy losing money after fees",
            "More is being lost than won across all trades.",
            "Review Performance → Fees & Execution.",
            "problem",
        )
    if curr_dd > 5.0:
        return (
            f"Account {curr_dd:.1f}% below peak",
            "Drawdown from recent high is above 5%.",
            "Risk limits will auto-enforce — monitor closely.",
            "watch",
        )
    if closes >= 5 and pf < 1.2:
        return (
            "Trade quality is marginal",
            f"Profit factor {pf:.2f} is below the 1.35 target.",
            "Review which signals are dragging performance.",
            "watch",
        )
    if err > 0:
        return (
            f"{err} minor error(s) in last hour",
            "Some non-critical errors are present.",
            "Check System → Event Log if persistent.",
            "watch",
        )
    if closes == 0:
        return (
            "No completed trades yet",
            "The bot hasn't finished its first trade — normal early on.",
            "Monitor to ensure the scanner is finding candidates.",
            "info",
        )
    return (
        "No critical issues",
        "System is running normally with no major problems.",
        "Keep monitoring — the bot is working.",
        "good",
    )


def render_control_tower():
    # ── Window selector ────────────────────────────────────────────────────────
    _wc1, _wc2 = st.columns([4, 1])
    with _wc2:
        window = st.selectbox(
            "Window",
            ["1h", "24h", "7d"],
            index=1,
            key="ct_window",
            label_visibility="collapsed",
        )
    with _wc1:
        st.markdown(
            f'<div style="font-size:0.72em;color:{ui._TEXT_CAP};padding-top:8px;">'
            f"Showing funnel & blocker data for the last "
            f'<strong style="color:{ui._TEXT_SEC};">{window}</strong></div>',
            unsafe_allow_html=True,
        )

    window_hours = {"1h": 1, "24h": 24, "7d": 168}[window]

    # ── Pull all data ──────────────────────────────────────────────────────────
    snap = get_control_tower_snapshot(hours=window_hours)

    try:
        from data.performance import get_performance_stats

        stats = get_performance_stats()
    except Exception:
        stats = {
            "closes": 0,
            "wins": 0,
            "losses": 0,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "total_fees": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "rr_realized": 0.0,
        }

    try:
        from data.account import get_drawdown

        dd = get_drawdown()
    except Exception:
        dd = {
            "current_dd_pct": 0.0,
            "max_dd_pct": 0.0,
            "current_dd_usd": 0.0,
            "max_dd_usd": 0.0,
        }

    try:
        from data.execution import get_execution_stats

        ex = get_execution_stats()
    except Exception:
        ex = {"total": 0, "entry_score": 0.0, "exit_score": 0.0, "fee_trap_rate": 0.0}

    scan_age = 9999
    try:
        from data.scanner_data import get_last_scan_age

        scan_age = get_last_scan_age() or 9999
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════════════════
    # ROW 1 — 4 top cards
    # ══════════════════════════════════════════════════════════════════════════
    c1, c2, c3, c4 = st.columns([1, 1.2, 1, 1])

    # ── Card 1: Bot Status ─────────────────────────────────────────────────────
    with c1:
        hb = snap.get("heartbeat_age", 9999)
        err = snap.get("error_count", 0)

        if hb < 120 and err == 0:
            chip_s, chip_l = "good", "Good"
            verdict = "Running Normally"
        elif hb < 300:
            chip_s, chip_l = "watch", "Watch"
            verdict = "Running with Issues"
        else:
            chip_s, chip_l = "problem", "Problem"
            verdict = "Bot May Be Offline"

        hb_str = _age_label(hb)
        scan_str = _age_label(scan_age)
        expl = (
            f"Heartbeat {hb_str} · "
            f"Last scan {scan_str} · "
            f"{err} error{'s' if err != 1 else ''} in last hour. "
            "Is the bot alive and scanning normally?"
        )
        st.markdown(
            ui.summary_card("BOT STATUS", verdict, chip_l, chip_s, expl),
            unsafe_allow_html=True,
        )

    # ── Card 2: Account Snapshot (hero) ───────────────────────────────────────
    with c2:
        equity = snap.get("account_equity", 0.0)
        daily_pnl = snap.get("daily_pnl", 0.0)
        dep_pct = snap.get("deployed_pct", 0.0)
        dep_usd = snap.get("deployed_usd", 0.0)
        curr_dd = dd.get("current_dd_pct", 0.0)

        pnl_sign = "+" if daily_pnl >= 0 else ""
        pnl_color = ui.C_GREEN if daily_pnl >= 0 else ui.C_RED
        dd_color = (
            ui.C_RED if curr_dd > 3 else ui.C_AMBER if curr_dd > 1 else ui.C_GREEN
        )
        st.markdown(
            ui.hero_card(
                "ACCOUNT SNAPSHOT",
                f"${equity:,.2f}",
                [
                    ("Today's P&L", f"{pnl_sign}${abs(daily_pnl):.2f}", pnl_color),
                    (
                        "Capital deployed",
                        f"{dep_pct:.1f}%  (${dep_usd:,.0f})",
                        ui.C_CYAN if dep_pct > 0 else None,
                    ),
                    ("Drawdown from peak", f"{curr_dd:.1f}%", dd_color),
                ],
                "Your full account at a glance.",
                gradient=True,
            ),
            unsafe_allow_html=True,
        )

    # ── Card 3: Trade Quality ──────────────────────────────────────────────────
    with c3:
        pf = stats.get("profit_factor", 0.0)
        wr = stats.get("win_rate", 0.0)
        closes = stats.get("closes", 0)
        ev = stats.get("total_pnl", 0.0) / closes if closes else 0.0
        fees = stats.get("total_fees", 0.0)
        pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"

        chip_s = "good" if pf >= 1.35 else ("watch" if pf >= 1.0 else "problem")
        chip_l = (
            "Good  ≥ 1.35"
            if pf >= 1.35
            else "Marginal  ≥ 1.0"
            if pf >= 1.0
            else "Problem  < 1.0"
        )
        pf_color = ui.C_GREEN if pf >= 1.35 else (ui.C_AMBER if pf >= 1.0 else ui.C_RED)

        metrics = (
            ui.metric_row(
                "Win rate",
                f"{wr:.0f}%  ({stats.get('wins', 0)}W / {stats.get('losses', 0)}L)",
            )
            + ui.metric_row(
                "EV / trade (after fees)",
                f"${ev:+.2f}",
                value_color=ui.C_GREEN if ev > 0 else ui.C_RED,
            )
            + ui.metric_row("Total fees paid", f"${fees:.2f}", value_color=ui.C_AMBER)
            + (
                f'<div style="font-size:0.70em;color:{ui._TEXT_CAP};margin-top:5px;">'
                f"{closes} closed trades since launch</div>"
                if closes
                else f'<div style="font-size:0.70em;color:{ui._TEXT_CAP};margin-top:5px;">'
                f"No completed trades yet</div>"
            )
        )

        card_html = (
            f'<div style="background:{ui._BG_CARD};border:1px solid {ui._BORDER};'
            f"border-top:2px solid {pf_color};border-radius:{ui._RADIUS};"
            f'padding:20px 22px;box-shadow:{ui._SHADOW};height:100%;box-sizing:border-box;">'
            f'<div style="font-size:0.68em;color:{ui._TEXT_CAP};text-transform:uppercase;'
            f'letter-spacing:0.11em;margin-bottom:10px;">TRADE QUALITY</div>'
            f'<div style="font-size:2.0em;font-weight:800;color:{pf_color};line-height:1.1;'
            f'margin-bottom:8px;">PF&nbsp;{pf_str}</div>'
            f'<div style="margin-bottom:12px;">{ui.chip(chip_l, chip_s)}</div>'
            f'<div style="border-top:1px solid {ui._BORDER};padding-top:10px;">{metrics}</div>'
            f'<div style="font-size:0.70em;color:{ui._TEXT_CAP};margin-top:8px;line-height:1.5;">'
            f"Is the strategy profitable enough to beat fees? Target: PF ≥ 1.35</div>"
            f"</div>"
        )
        st.markdown(card_html, unsafe_allow_html=True)

    # ── Card 4: Biggest Issue ──────────────────────────────────────────────────
    with c4:
        issue, why, action, issue_status = _derive_biggest_issue(snap, stats, dd, ex)
        st.markdown(
            ui.summary_card(
                "BIGGEST ISSUE RIGHT NOW",
                issue,
                issue_status.capitalize(),
                issue_status,
                f"{why}  {action}",
            ),
            unsafe_allow_html=True,
        )

    st.markdown('<div class="ds-spacer-md"></div>', unsafe_allow_html=True)
    st.markdown(
        ui.section_header(
            "LANE ROLES",
            "Primary crypto workflow and promotion-ready side lanes",
        ),
        unsafe_allow_html=True,
    )
    lane_rows = snap.get("lane_overview") or []
    if lane_rows:
        lane_cols = st.columns(len(lane_rows))
        for col, lane in zip(lane_cols, lane_rows):
            with col:
                role_label, chip_status = _lane_role_label(lane.get("lane_role", ""))
                readiness = (lane.get("readiness_state") or "UNKNOWN").replace("_", " ")
                runner = "Running" if lane.get("active") else "Stopped"
                autonomy = "Enabled" if lane.get("autonomous_enabled") else "Disabled"
                manual = "Allowed" if lane.get("manual_allowed") else "Disabled"
                promotion = lane.get("promotion_condition") or "No promotion condition set"
                body = (
                    ui.metric_row("Readiness", readiness)
                    + ui.metric_row("Runner", runner)
                    + ui.metric_row("Autonomy", autonomy)
                    + ui.metric_row("Manual", manual)
                    + ui.metric_row("Promotion", promotion)
                )
                st.markdown(
                    ui.detail_card(
                        str(lane.get("display_name") or lane.get("lane_id", "")).upper(),
                        f"{role_label} lane",
                        body,
                        footer=lane.get("blocked_reason") or "",
                    ),
                    unsafe_allow_html=True,
                )

    # ══════════════════════════════════════════════════════════════════════════
    # ROW 2 — 3-zone layout
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown('<div class="ds-spacer-md"></div>', unsafe_allow_html=True)
    left, center, right = st.columns([0.26, 0.48, 0.26])

    # ── LEFT: Positions + Scanner Summary + Activity ───────────────────────────
    with left:
        try:
            from widgets.mission_control.open_positions import render_positions_compact

            render_positions_compact()
        except Exception as e:
            st.caption(f"Positions unavailable: {e}")

        st.markdown('<div class="ds-spacer-sm"></div>', unsafe_allow_html=True)

        # Inline scanner summary
        crypto_funnel = snap.get("crypto_funnel") or {}
        funnel = crypto_funnel.get("funnel") or {}
        scanned = int(funnel.get("scanner_candidates_total") or 0)
        scored = int(funnel.get("scored_total") or 0)
        entered = int(funnel.get("entered") or 0)
        conv_pct = crypto_funnel.get("conversion_pct", 0.0)
        blockers = crypto_funnel.get("top_blockers") or []
        top_b = blockers[0].get("reason", "") if blockers else ""
        top_b_n = blockers[0].get("n", 0) if blockers else 0

        scan_body = (
            ui.metric_row("Symbols scanned", str(scanned))
            + ui.metric_row("Passed signal score", str(scored))
            + ui.metric_row("Trades entered", str(entered))
            + ui.metric_row(
                "Conversion rate",
                f"{conv_pct:.1f}%",
                value_color=ui.C_GREEN if conv_pct > 1 else ui.C_AMBER,
            )
        )
        if top_b:
            scan_body += (
                f'<div style="font-size:0.71em;color:{ui.C_AMBER};margin-top:6px;">'
                f"Top filter: {_plain_blocker(top_b)} ({top_b_n}×)</div>"
            )

        st.markdown(
            ui.detail_card(
                "SCANNER SUMMARY",
                f"Where trade ideas are filtered out — last {window}",
                scan_body,
            ),
            unsafe_allow_html=True,
        )

        try:
            from widgets.mission_control.activity_log import render_smart_logs

            render_smart_logs()
        except Exception as e:
            st.caption(f"Activity log unavailable: {e}")

    # ── CENTER: Main Performance Panel ────────────────────────────────────────
    with center:
        st.markdown(
            ui.section_header(
                "PERFORMANCE",
                "Account growth since launch — each point is one completed trade",
            ),
            unsafe_allow_html=True,
        )

        try:
            from widgets.mission_control.equity_curve import render_equity_curve_compact

            render_equity_curve_compact()
        except Exception as e:
            st.caption(f"Equity curve unavailable: {e}")

        # Summary strip
        st.markdown('<div class="ds-spacer-sm"></div>', unsafe_allow_html=True)
        total_pnl = stats.get("total_pnl", 0.0)
        n_pos = len(snap.get("open_positions", []))
        ex_entry = ex.get("entry_score", 0.0) if ex.get("total", 0) > 0 else None
        ex_exit = ex.get("exit_score", 0.0) if ex.get("total", 0) > 0 else None

        sc1, sc2, sc3, sc4 = st.columns(4)
        pnl_s = "+" if total_pnl >= 0 else ""
        sc1.metric(
            "After-fee P&L",
            f"{pnl_s}${abs(total_pnl):.2f}",
            help="Total profit or loss after all fees since launch",
        )
        sc2.metric(
            "Open positions",
            str(n_pos),
            help="Number of active trades right now",
        )
        sc3.metric(
            "Entry quality",
            f"{ex_entry:.1f}/10" if ex_entry is not None else "—",
            help="How well-timed our entries are (10 = entered right before the move)",
        )
        sc4.metric(
            "Exit quality",
            f"{ex_exit:.1f}/10" if ex_exit is not None else "—",
            help="Fraction of each price move we actually captured (10 = perfect)",
        )

    # ── RIGHT: Risk + Decision + Execution + Learning ──────────────────────────
    with right:
        # A. Risk Overview
        max_dd = dd.get("max_dd_pct", 0.0)
        mode = snap.get("runtime_mode", "PAPER")
        mode_c = ui.C_RED if mode == "LIVE" else ui.C_CYAN

        risk_body = (
            ui.metric_row("Trading mode", mode, value_color=mode_c)
            + ui.metric_row("Open positions", str(n_pos))
            + ui.metric_row(
                "Capital deployed",
                f"${dep_usd:,.0f}  ({dep_pct:.1f}%)",
            )
            + ui.metric_row(
                "Current drawdown",
                f"{curr_dd:.1f}%",
                value_color=ui.C_RED if curr_dd > 3 else ui.C_GREEN,
            )
            + ui.metric_row("Max drawdown (ever)", f"{max_dd:.1f}%")
        )
        st.markdown(
            ui.detail_card(
                "RISK OVERVIEW",
                "How exposed is the account right now?",
                risk_body,
            ),
            unsafe_allow_html=True,
        )

        # B. Decision Quality
        try:
            from widgets.mission_control.decision_quality import render_decision_quality

            render_decision_quality()
        except Exception as e:
            st.caption(f"Decision quality unavailable: {e}")

        # C. Execution Quality
        try:
            from widgets.mission_control.execution_quality import (
                render_execution_quality,
            )

            render_execution_quality()
        except Exception as e:
            st.caption(f"Execution quality unavailable: {e}")

        # D. Learning Status
        try:
            from data.health import get_ml_status

            ml = get_ml_status()
        except Exception:
            ml = {"snapshots": 0, "min_needed": 200}

        ml_snaps = ml.get("snapshots", 0)
        ml_min = ml.get("min_needed", 200)
        ml_on = ml_snaps >= ml_min

        ml_body = ui.metric_row(
            "ML model active",
            "Yes — scoring trades" if ml_on else "Not yet active",
            value_color=ui.C_GREEN if ml_on else ui.C_AMBER,
        ) + ui.metric_row(
            "Training snapshots",
            f"{ml_snaps} / {ml_min} needed",
            value_color=ui.C_GREEN if ml_on else ui._TEXT_PRI,
        )
        if not ml_on:
            remaining = ml_min - ml_snaps
            ml_body += ui.info_callout(
                f"Needs {remaining} more completed trades before ML activates. "
                "Using rule-based signals only until then — fully normal.",
                "info",
            )

        st.markdown(
            ui.detail_card(
                "LEARNING STATUS",
                "Is the bot improving from past trades?",
                ml_body,
            ),
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # ROW 3 — Lower band
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown('<div class="ds-spacer-md"></div>', unsafe_allow_html=True)
    st.markdown(
        ui.section_header(
            "DETAILS",
            "Deeper diagnostics without leaving this page",
        ),
        unsafe_allow_html=True,
    )

    r1, r2, r3, r4 = st.columns(4)

    with r1:
        try:
            from widgets.mission_control.failure_modes import render_failures_compact

            render_failures_compact()
        except Exception as e:
            st.caption(f"Failure modes unavailable: {e}")

    with r2:
        lifecycle = snap.get("lifecycle_stages") or []
        _lc_colors = {
            "discovered": "#6e7681",
            "signal_pass": "#d29922",
            "econ_pass": "#bc8cff",
            "route_decided": "#58a6ff",
            "size_pass": "#a78bfa",
            "execution_attempted": "#38bdf8",
            "position_open": "#3fb950",
            "exit_complete": "#86efac",
        }
        if lifecycle:
            max_c = max((s.get("count", 0) for s in lifecycle), default=1) or 1
            bars_html = ""
            for s in lifecycle:
                color = _lc_colors.get(s.get("stage", ""), "#6e7681")
                note = " ·derived" if s.get("derived") else ""
                bars_html += ui.funnel_bar(
                    s.get("stage", ""), s.get("count", 0), max_c, color, note
                )
            st.markdown(
                ui.detail_card(
                    "SCANNER FUNNEL DETAIL",
                    f"Where trade ideas die — last {window}",
                    bars_html,
                    "·derived = computed from persisted fields, not a dedicated column",
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                ui.detail_card(
                    "SCANNER FUNNEL DETAIL",
                    f"Where trade ideas die — last {window}",
                    ui.empty_state(
                        "No funnel data yet",
                        "Populates after the bot runs a scan.",
                    ),
                ),
                unsafe_allow_html=True,
            )

    with r3:
        try:
            from widgets.mission_control.alert_feed import render_alert_feed

            render_alert_feed()
        except Exception as e:
            st.caption(f"Alert feed unavailable: {e}")

    with r4:
        try:
            from data.account import get_trade_log

            trades = get_trade_log(limit=8)
        except Exception:
            trades = []

        if trades:
            rows_html = ""
            for t in trades:
                pnl = float(t.get("pnl_usd") or 0)
                p_c = ui.C_GREEN if pnl >= 0 else ui.C_RED
                p_sign = "+" if pnl >= 0 else ""
                sym = t.get("symbol", "?")
                action = t.get("action", "")
                ts = str(t.get("ts", ""))[:16]
                rows_html += (
                    f'<div style="display:flex;justify-content:space-between;'
                    f"padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);"
                    f'font-size:0.79em;">'
                    f"<div>"
                    f'<span style="color:{ui._TEXT_PRI};font-weight:600;">{sym}</span>'
                    f'&nbsp;<span style="color:{ui._TEXT_CAP};font-size:0.88em;">{action}</span>'
                    f"</div>"
                    f'<span style="color:{p_c};font-weight:700;">'
                    f"{p_sign}${abs(pnl):.2f}</span>"
                    f"</div>"
                )
            rows_html += (
                f'<div style="font-size:0.68em;color:{ui._TEXT_CAP};margin-top:6px;">'
                f"Showing last {len(trades)} trades</div>"
            )
            st.markdown(
                ui.detail_card(
                    "TRADE LOG",
                    "Recent completed trades",
                    rows_html,
                ),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                ui.detail_card(
                    "TRADE LOG",
                    "Recent completed trades",
                    ui.empty_state(
                        "No trades yet",
                        "Will populate after the first completed trade.",
                    ),
                ),
                unsafe_allow_html=True,
            )
