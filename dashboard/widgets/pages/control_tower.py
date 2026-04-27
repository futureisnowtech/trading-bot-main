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


def _collect_all_issues(
    snap: dict,
    stats: dict,
    dd: dict,
    ex: dict,
) -> list[dict]:
    """Return list of issue dicts: {label, detail, severity ('problem'|'watch'|'info'|'good')}."""
    issues: list[dict] = []

    hb = snap.get("heartbeat_age", 9999)
    err = snap.get("error_count", 0)
    decision_counts = (snap.get("crypto_funnel") or {}).get("decision_counts", {})
    exec_fail_total = int(decision_counts.get("execution_failed", 0))
    pf = stats.get("profit_factor", 0.0)
    closes = int(stats.get("closes", 0) or 0)
    curr_dd = dd.get("current_dd_pct", 0.0)

    # ── Bot heartbeat ──────────────────────────────────────────────────────────
    if hb >= 300:
        issues.append(
            {
                "label": "Bot appears offline",
                "detail": f"No heartbeat for {hb // 60}m — main.py may have crashed.",
                "severity": "problem",
            }
        )
    elif hb >= 120:
        issues.append(
            {
                "label": f"Heartbeat slow ({hb}s ago)",
                "detail": "Bot is alive but responding slower than usual.",
                "severity": "watch",
            }
        )

    # ── Error rate ─────────────────────────────────────────────────────────────
    if err > 10:
        issues.append(
            {
                "label": f"{err} errors in last hour",
                "detail": "High error rate — some scans or orders may be silently failing.",
                "severity": "problem",
            }
        )
    elif err > 0:
        issues.append(
            {
                "label": f"{err} minor error(s) in last hour",
                "detail": "Non-critical — check Engineering Console if it keeps growing.",
                "severity": "watch",
            }
        )

    # ── Execution failures (broken into real vs sizing) ────────────────────────
    if exec_fail_total >= 2:
        # Try to break down spot_min_order_not_met vs actual broker failures
        try:
            import sqlite3, os as _os

            _db = _os.path.join(_os.path.dirname(__file__), "../../../logs/trades.db")
            _conn = sqlite3.connect(_db)
            _conn.row_factory = sqlite3.Row
            _rows = _conn.execute(
                """SELECT trade_blocked_reason, COUNT(*) as n FROM scan_candidates
                   WHERE status='blocked' AND decision='execution_failed'
                   AND datetime(replace(substr(ts,1,19),'T',' ')) >= datetime('now','-24 hours')
                   GROUP BY trade_blocked_reason ORDER BY n DESC LIMIT 8"""
            ).fetchall()
            _conn.close()
            _sizing = sum(
                r["n"]
                for r in _rows
                if (r["trade_blocked_reason"] or "") == "spot_min_order_not_met"
            )
            _broker = exec_fail_total - _sizing
        except Exception:
            _sizing = 0
            _broker = exec_fail_total

        if _sizing > 0:
            issues.append(
                {
                    "label": f"{_sizing} spot orders too small to place",
                    "detail": "Spot signals fired but sized below the $10 minimum — score was good, sizing was too tiny.",
                    "severity": "watch",
                }
            )
        if _broker > 0:
            issues.append(
                {
                    "label": f"{_broker} broker order(s) failed",
                    "detail": "Signal was valid but the Coinbase/IBKR order came back empty — check API keys or contract specs.",
                    "severity": "problem",
                }
            )

    # ── Drawdown ───────────────────────────────────────────────────────────────
    if curr_dd > 5.0:
        issues.append(
            {
                "label": f"Account {curr_dd:.1f}% below its peak",
                "detail": "Drawdown is elevated — risk limits will tighten automatically.",
                "severity": "problem",
            }
        )
    elif curr_dd > 2.0:
        issues.append(
            {
                "label": f"Drawdown at {curr_dd:.1f}%",
                "detail": "Minor pullback from peak — within normal range but worth watching.",
                "severity": "watch",
            }
        )

    # ── Profit factor ──────────────────────────────────────────────────────────
    if closes >= 5 and pf < 1.0:
        issues.append(
            {
                "label": "Losing more than winning after fees",
                "detail": f"Profit factor {pf:.2f} — the strategy needs the winners to outpace the losers.",
                "severity": "problem",
            }
        )
    elif closes >= 5 and pf < 1.2:
        issues.append(
            {
                "label": f"Trade quality marginal (PF {pf:.2f})",
                "detail": "Profit factor below 1.2 target — fees are eating into thin edges.",
                "severity": "watch",
            }
        )

    # ── No trades yet ──────────────────────────────────────────────────────────
    if closes == 0 and hb < 300:
        issues.append(
            {
                "label": "No completed trades yet",
                "detail": "Normal early on — bot is scanning but hasn't closed a position.",
                "severity": "info",
            }
        )

    # ── All clear ─────────────────────────────────────────────────────────────
    if not issues:
        issues.append(
            {
                "label": "No issues found",
                "detail": "System is running normally.",
                "severity": "good",
            }
        )

    return issues


def _render_issues_card(issues: list[dict]) -> str:
    """Render all issues as HTML bullet rows inside a card body."""
    _SEV_COLOR = {
        "problem": "#f85149",
        "watch": "#d29922",
        "info": "#58a6ff",
        "good": "#3fb950",
    }
    _SEV_ICON = {
        "problem": "●",
        "watch": "◆",
        "info": "○",
        "good": "✓",
    }
    rows = ""
    for issue in issues:
        sev = issue.get("severity", "info")
        color = _SEV_COLOR.get(sev, "#8b949e")
        icon = _SEV_ICON.get(sev, "·")
        rows += (
            f'<div style="display:flex;gap:8px;padding:5px 0;'
            f'border-bottom:1px solid rgba(255,255,255,0.04);">'
            f'<span style="color:{color};font-size:0.78em;flex-shrink:0;padding-top:1px;">{icon}</span>'
            f'<div style="flex:1;">'
            f'<div style="font-size:0.80em;color:#e6edf3;font-weight:600;line-height:1.3;">'
            f"{issue['label']}</div>"
            f'<div style="font-size:0.72em;color:#8b949e;line-height:1.4;">'
            f"{issue['detail']}</div>"
            f"</div></div>"
        )
    return rows


def render_control_tower():
    window = "24h"
    window_hours = 24
    snap = get_control_tower_snapshot(hours=window_hours)

    try:
        from data.account import get_drawdown, get_trade_log

        dd = get_drawdown(current_only=True)
        trades = get_trade_log(limit=8, current_only=True)
    except Exception:
        dd = {
            "current_dd_pct": 0.0,
            "max_dd_pct": 0.0,
            "current_dd_usd": 0.0,
            "max_dd_usd": 0.0,
        }
        trades = []

    stats = snap.get("current_trade_stats") or {
        "closes": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "total_pnl": 0.0,
        "total_fees": 0.0,
    }
    try:
        from data.execution import get_execution_stats

        ex = get_execution_stats()
    except Exception:
        ex = {"total": 0, "fee_trap_rate": 0.0}

    scan_age = 9999
    try:
        from data.scanner_data import get_last_scan_age

        scan_age = get_last_scan_age() or 9999
    except Exception:
        pass

    st.markdown(
        f'<div style="font-size:0.72em;color:{ui._TEXT_CAP};padding-top:8px;">'
        f'Current operational metrics since <strong style="color:{ui._TEXT_SEC};">{snap.get("metrics_since", "")}</strong>'
        f' · blocker funnel still shown for the last <strong style="color:{ui._TEXT_SEC};">{window}</strong></div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns([1, 1.15, 1, 1])
    equity = snap.get("account_equity", 0.0)
    daily_pnl = snap.get("daily_pnl", 0.0)
    dep_pct = snap.get("deployed_pct", 0.0)
    dep_usd = snap.get("deployed_usd", 0.0)
    curr_dd = dd.get("current_dd_pct", 0.0)
    n_pos = len(snap.get("open_positions", []))

    with c1:
        hb = snap.get("heartbeat_age", 9999)
        err = snap.get("error_count", 0)
        if hb < 120 and err == 0:
            chip_s, chip_l, verdict = "good", "Good", "Running Normally"
        elif hb < 300:
            chip_s, chip_l, verdict = "watch", "Watch", "Running with Issues"
        else:
            chip_s, chip_l, verdict = "problem", "Problem", "Bot May Be Offline"
        expl = (
            f"Heartbeat {_age_label(hb)} · Last scan {_age_label(scan_age)} · "
            f"{err} error{'s' if err != 1 else ''} in last hour."
        )
        st.markdown(
            ui.summary_card("BOT STATUS", verdict, chip_l, chip_s, expl),
            unsafe_allow_html=True,
        )

    with c2:
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
                    ("Open positions", str(n_pos), ui.C_CYAN if n_pos else None),
                    (
                        "Capital deployed",
                        f"{dep_pct:.1f}% (${dep_usd:,.0f})",
                        ui.C_CYAN if dep_pct > 0 else None,
                    ),
                    ("Drawdown", f"{curr_dd:.1f}%", dd_color),
                ],
                "Live account state only.",
                gradient=True,
            ),
            unsafe_allow_html=True,
        )

    with c3:
        pf = stats.get("profit_factor", 0.0)
        closes = int(stats.get("closes", 0) or 0)
        wr = float(stats.get("win_rate", 0.0) or 0.0)
        ev = (stats.get("total_pnl", 0.0) or 0.0) / closes if closes else 0.0
        fees = float(stats.get("total_fees", 0.0) or 0.0)
        pf_str = f"{pf:.2f}" if pf != float("inf") else "∞"
        chip_s = "good" if pf >= 1.2 else ("watch" if pf >= 1.0 else "problem")
        chip_l = "Healthy" if pf >= 1.2 else ("Marginal" if pf >= 1.0 else "Needs work")
        body = (
            ui.metric_row(
                "Profit factor",
                pf_str,
                value_color=ui.C_GREEN
                if pf >= 1.2
                else ui.C_AMBER
                if pf >= 1.0
                else ui.C_RED,
            )
            + ui.metric_row(
                "Win rate",
                f"{wr:.0f}% ({stats.get('wins', 0)}W / {stats.get('losses', 0)}L)",
            )
            + ui.metric_row(
                "EV / trade",
                f"${ev:+.2f}",
                value_color=ui.C_GREEN if ev > 0 else ui.C_RED,
            )
            + ui.metric_row("Fees", f"${fees:.2f}", value_color=ui.C_AMBER)
        )
        footer = (
            f"{closes} closed trade{'s' if closes != 1 else ''} since current rollout"
            if closes
            else "No completed trades since current rollout"
        )
        st.markdown(
            ui.detail_card("CURRENT STRATEGY", chip_l, body, footer=footer),
            unsafe_allow_html=True,
        )

    with c4:
        issue, why, action, issue_status = _derive_biggest_issue(snap, stats, dd, ex)
        st.markdown(
            ui.summary_card(
                "BIGGEST ISSUE",
                issue,
                issue_status.capitalize(),
                issue_status,
                f"{why} {action}",
            ),
            unsafe_allow_html=True,
        )

    st.markdown('<div class="ds-spacer-md"></div>', unsafe_allow_html=True)
    left, right = st.columns([1.15, 0.85])

    with left:
        try:
            from widgets.mission_control.open_positions import render_positions_compact

            render_positions_compact()
        except Exception as e:
            st.caption(f"Positions unavailable: {e}")

        st.markdown('<div class="ds-spacer-sm"></div>', unsafe_allow_html=True)
        try:
            from widgets.mission_control.activity_log import render_smart_logs

            render_smart_logs()
        except Exception as e:
            st.caption(f"Activity log unavailable: {e}")

    with right:
        crypto_funnel = snap.get("crypto_funnel") or {}
        funnel = crypto_funnel.get("funnel") or {}
        blockers = crypto_funnel.get("top_blockers") or []
        top_b = blockers[0].get("reason", "") if blockers else ""
        top_b_n = blockers[0].get("n", 0) if blockers else 0
        funnel_body = (
            ui.metric_row(
                "Scanned", str(int(funnel.get("scanner_candidates_total") or 0))
            )
            + ui.metric_row(
                "Passed signal score", str(int(funnel.get("scored_total") or 0))
            )
            + ui.metric_row(
                "Passed economics", str(int(funnel.get("econ_passed_total") or 0))
            )
            + ui.metric_row("Trades entered", str(int(funnel.get("entered") or 0)))
        )
        if top_b:
            funnel_body += ui.metric_row(
                "Top blocker", f"{_plain_blocker(top_b)} ({top_b_n}x)"
            )
        fee_trap_rate = float(ex.get("fee_trap_rate", 0.0) or 0.0)
        funnel_body += ui.metric_row("Fee trap rate", f"{fee_trap_rate:.1f}%")
        st.markdown(
            ui.detail_card(
                "CURRENT PIPELINE",
                "Only current-window blocker flow",
                funnel_body,
                footer=f"Blocker window: last {window} · performance window starts {snap.get('metrics_since', '')}",
            ),
            unsafe_allow_html=True,
        )

        rows_html = ""
        if trades:
            for t in trades:
                pnl = float(t.get("pnl_usd") or 0)
                p_c = ui.C_GREEN if pnl >= 0 else ui.C_RED
                p_sign = "+" if pnl >= 0 else ""
                sym = t.get("symbol", "?")
                action = t.get("action", "")
                rows_html += (
                    f'<div style="display:flex;justify-content:space-between;'
                    f'padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-size:0.79em;">'
                    f'<div><span style="color:{ui._TEXT_PRI};font-weight:600;">{sym}</span> '
                    f'<span style="color:{ui._TEXT_CAP};font-size:0.88em;">{action}</span></div>'
                    f'<span style="color:{p_c};font-weight:700;">{p_sign}${abs(pnl):.2f}</span></div>'
                )
            footer = f"Showing last {len(trades)} completed trade{'s' if len(trades) != 1 else ''} since current rollout"
        else:
            rows_html = ui.empty_state(
                "No completed trades yet",
                "This section only shows trades closed after the latest strategy rollout.",
            )
            footer = ""
        st.markdown(
            ui.detail_card(
                "RECENT CLOSED TRADES", "Current rollout only", rows_html, footer=footer
            ),
            unsafe_allow_html=True,
        )
