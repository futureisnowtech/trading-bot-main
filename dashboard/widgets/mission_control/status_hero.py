"""
Widget: Status Hero
Answers in plain English: Is the bot running? Am I making money? What should I know?

Replaces the old 3-column "System Status" row (System Integrity + Edge Quality + Alert Feed)
with a single, human-readable panel that non-quants can understand at a glance.

Refresh: 10s
"""

import re
import streamlit as st
from datetime import datetime

from data.health import (
    get_error_rate_1h,
    get_health_check_failures,
    get_recent_errors_detail,
)
from data.scanner_data import get_last_scan_age
from data.performance import get_performance_stats
from data.account import get_today_pnl, get_drawdown
from data.positions import get_open_positions
from data.balance import get_all_balances
from db import get_effective_launch_date


def _sign(v: float) -> str:
    return "+" if v > 0 else ""


def _md_to_html(text: str) -> str:
    """Convert **bold** markers to <strong> HTML so they render inside an HTML block."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong style='color:#e2e8f0'>\1</strong>", text)


@st.fragment(run_every=10)
def render_status_hero():
    # ── pull data ─────────────────────────────────────────────────────────────
    scan_age = get_last_scan_age()
    error_rate = get_error_rate_1h()
    stats = get_performance_stats()
    today_pnl = get_today_pnl()
    dd = get_drawdown()
    n_pos = len(get_open_positions())

    total_pnl = stats["total_pnl"]
    closes = stats["closes"]
    win_rate = stats["win_rate"]

    # ── determine bot status ──────────────────────────────────────────────────
    scan_ok = scan_age < 600  # scanned within 10 minutes
    _health_issues = get_health_check_failures()
    no_errors = error_rate == 0 and not _health_issues

    if scan_ok and no_errors:
        dot, bg = "#4ade80", "rgba(74,222,128,0.07)"
        label = "BOT IS RUNNING"
        when = (
            f"scanned {scan_age}s ago"
            if scan_age < 120
            else f"last scan {scan_age // 60}m ago"
        )
        detail = f"{when}  ·  no errors"
    elif scan_ok:
        dot, bg = "#facc15", "rgba(250,204,21,0.07)"
        label = "RUNNING  —  ERRORS DETECTED"
        detail = (
            f"last scan {scan_age // 60}m ago  ·  {error_rate} error(s) in last hour"
        )
    else:
        dot, bg = "#f87171", "rgba(248,113,113,0.07)"
        label = "BOT MAY BE OFFLINE"
        detail = (
            "no scan data found — is  main.py  running?"
            if scan_age >= 9999
            else f"last scan was {scan_age // 60}m ago — expected every 5 min"
        )

    # ── hero banner ───────────────────────────────────────────────────────────
    st.markdown(
        f"""
        <div style="background:{bg}; border-left:4px solid {dot};
             padding:13px 18px; border-radius:8px; margin-bottom:18px;
             display:flex; justify-content:space-between; align-items:center;
             flex-wrap:wrap; gap:8px;">
          <span style="font-size:1.25em; font-weight:800; color:{dot};
                letter-spacing:0.03em;">● {label}</span>
          <span style="color:#64748b; font-size:0.84em;">{detail}</span>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── error breakdown ───────────────────────────────────────────────────────
    # _health_issues already computed above for banner logic — reuse it here.
    # other_errors: last 1h of non-health_check ERROR rows (scanner, broker, ml, etc).
    # The panel disappears automatically when both are empty — that IS the all-clear UX.
    _other_errors = get_recent_errors_detail() if error_rate > 0 else []
    _all_issues = _health_issues + _other_errors

    if _all_issues:
        _checked_at = datetime.now().strftime("%H:%M:%S")
        with st.expander(
            f"{len(_all_issues)} issue type(s) detected",
            expanded=True,
        ):
            st.markdown(
                f"<span style='color:#475569; font-size:0.78em;'>Live — checked at "
                f"<strong style='color:#94a3b8;'>{_checked_at}</strong> · "
                f"updates every 10s · panel disappears when all resolved</span>",
                unsafe_allow_html=True,
            )
            for err in _all_issues:
                _is_live = err.get("live", False)
                badge_color = (
                    "#7c3aed" if err["fix_type"] == "Gemini Code" else "#0ea5e9"
                )
                fix_label = err["fix_type"].upper()
                count_str = f"×{err['count']}" if err["count"] > 1 else ""
                right_badges = (
                    '<span style="font-size:0.71em; color:#4ade80; '
                    "background:rgba(74,222,128,0.12); padding:2px 7px; "
                    'border-radius:4px; font-weight:600;">LIVE</span>'
                    if _is_live
                    else (
                        f'<span style="font-size:0.71em; color:#64748b;">{count_str}</span>'
                        if count_str
                        else ""
                    )
                )
                st.markdown(
                    f"""<div style="background:rgba(248,113,113,0.06);
                         border:1px solid rgba(248,113,113,0.18);
                         border-radius:7px; padding:10px 14px; margin-bottom:4px; margin-top:8px;">
                      <div style="display:flex; justify-content:space-between;
                           align-items:center; margin-bottom:5px; flex-wrap:wrap; gap:6px;">
                        <span style="font-weight:700; color:#fca5a5;
                              font-size:0.91em;">{err["category"]}</span>
                        <span style="display:flex; gap:7px; align-items:center;">
                          <span style="font-size:0.71em; color:#94a3b8;
                               background:rgba(255,255,255,0.05);
                               padding:2px 7px; border-radius:4px;">{err["source"]}</span>
                          <span style="font-size:0.71em; color:#fff;
                               background:{badge_color};
                               padding:2px 9px; border-radius:4px;
                               font-weight:700; letter-spacing:0.04em;">{fix_label}</span>
                          {right_badges}
                        </span>
                      </div>
                      <div style="font-size:0.75em; color:#64748b; font-family:monospace;
                           white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
                           max-width:100%;">{err["sample_msg"]}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
                st.code(err["fix_prompt"], language=None)
    elif error_rate > 0:
        # DB has errors in last 1h but health_check now reads healthy and no other
        # errors — issues resolved mid-hour. Show a brief confirmation.
        st.markdown(
            "<div style='background:rgba(74,222,128,0.06); border:1px solid "
            "rgba(74,222,128,0.2); border-radius:7px; padding:9px 14px; "
            "margin-bottom:12px; font-size:0.85em; color:#4ade80;'>"
            "✓ All issues resolved — system is healthy</div>",
            unsafe_allow_html=True,
        )

    # ── 4 big metric cards ────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)

    def _card(col, heading, big, sub, color):
        col.markdown(
            f"""<div style="background:rgba(255,255,255,0.03); border-radius:8px;
                     padding:14px 16px; min-height:92px;">
              <div style="font-size:0.69em; color:#64748b; text-transform:uppercase;
                   letter-spacing:0.09em; margin-bottom:5px;">{heading}</div>
              <div style="font-size:1.65em; font-weight:800; color:{color};
                   line-height:1.1;">{big}</div>
              <div style="font-size:0.74em; color:#64748b; margin-top:4px;">{sub}</div>
            </div>""",
            unsafe_allow_html=True,
        )

    today_c = "#4ade80" if today_pnl >= 0 else "#f87171"
    total_c = "#4ade80" if total_pnl >= 0 else "#f87171"
    wr_c = "#4ade80" if win_rate >= 55 else "#facc15" if win_rate >= 45 else "#f87171"
    pos_c = "#60a5fa" if n_pos > 0 else "#64748b"

    _card(
        c1,
        "Today's P&L",
        f"{_sign(today_pnl)}${abs(today_pnl):.2f}",
        "since midnight",
        today_c,
    )
    _card(
        c2,
        "Total earned (since launch)",
        f"{_sign(total_pnl)}${abs(total_pnl):.2f}",
        f"after all fees  ·  {closes} trades",
        total_c,
    )
    _card(
        c3,
        "Win rate",
        f"{win_rate:.0f}%",
        f"{stats['wins']} wins  ·  {stats['losses']} losses",
        wr_c,
    )
    _card(
        c4,
        "Open right now",
        str(n_pos),
        "active trade" + ("s" if n_pos != 1 else ""),
        pos_c,
    )

    # ── live account balances ─────────────────────────────────────────────────
    try:
        balances = get_all_balances()
        cb = balances["coinbase"]
        ib = balances["ibkr"]

        def _source_badge(source: str, connected: bool) -> str:
            if source == "live_api":
                return "<span style='color:#4ade80;font-size:0.72em;font-weight:700;'>● LIVE API</span>"
            if source == "live_tws":
                return "<span style='color:#4ade80;font-size:0.72em;font-weight:700;'>● LIVE TWS</span>"
            if source == "paper_computed":
                return "<span style='color:#60a5fa;font-size:0.72em;font-weight:700;'>● LIVE</span>"
            if source == "disabled":
                return "<span style='color:#475569;font-size:0.72em;'>DISABLED</span>"
            return "<span style='color:#f87171;font-size:0.72em;'>● UNAVAILABLE</span>"

        def _balance_card(col, title, bal_dict, subtitle=""):
            bal = bal_dict["balance"]
            src = bal_dict["source"]
            connected = bal_dict.get("connected", False)
            badge = _source_badge(src, connected)
            color = "#e2e8f0"
            sub = subtitle or src.replace("_", " ")
            # Show realized P&L delta for paper accounts
            if src == "paper_computed":
                rpnl = bal_dict.get("realized_pnl", 0.0)
                upnl = bal_dict.get("unrealized_pnl", 0.0)
                sign = "+" if rpnl >= 0 else ""
                pnl_color = "#4ade80" if rpnl >= 0 else "#f87171"
                sub = f"<span style='color:{pnl_color};'>{sign}${rpnl:.2f} realized</span>"
                if abs(upnl) > 0.01:
                    u_sign = "+" if upnl >= 0 else ""
                    u_color = "#4ade80" if upnl >= 0 else "#f87171"
                    sub += f"  <span style='color:{u_color};font-size:0.9em;'>{u_sign}${upnl:.2f} open</span>"
            col.markdown(
                f"""<div style="background:rgba(255,255,255,0.03); border-radius:8px;
                         padding:14px 16px; min-height:92px;">
                  <div style="display:flex; justify-content:space-between; align-items:center;
                       margin-bottom:5px;">
                    <span style="font-size:0.69em; color:#64748b; text-transform:uppercase;
                         letter-spacing:0.09em;">{title}</span>
                    {badge}
                  </div>
                  <div style="font-size:1.65em; font-weight:800; color:{color};
                       line-height:1.1;">${bal:,.2f}</div>
                  <div style="font-size:0.74em; color:#64748b; margin-top:4px;">{sub}</div>
                </div>""",
                unsafe_allow_html=True,
            )

        st.markdown(
            "<div style='margin-top:10px; margin-bottom:4px; font-size:0.72em; "
            "color:#475569; text-transform:uppercase; letter-spacing:0.1em;'>"
            "Account Balances</div>",
            unsafe_allow_html=True,
        )
        b1, b2, b3 = st.columns(3)
        _balance_card(b1, "Coinbase  (crypto perps)", cb)
        _balance_card(b2, "IBKR  (MES futures)", ib)
        total = balances["total_usd"]
        b3.markdown(
            f"""<div style="background:rgba(255,255,255,0.03); border-radius:8px;
                     padding:14px 16px; min-height:92px;">
              <div style="font-size:0.69em; color:#64748b; text-transform:uppercase;
                   letter-spacing:0.09em; margin-bottom:5px;">Total portfolio</div>
              <div style="font-size:1.65em; font-weight:800; color:#e2e8f0;
                   line-height:1.1;">${total:,.2f}</div>
              <div style="font-size:0.74em; color:#475569; margin-top:4px;">
                Coinbase + IBKR combined
              </div>
            </div>""",
            unsafe_allow_html=True,
        )
    except Exception as _be:
        st.caption(f"Balance panel error: {_be}")

    # ── plain-English narrative ───────────────────────────────────────────────
    try:
        launch_dt = datetime.strptime(get_effective_launch_date()[:10], "%Y-%m-%d")
        days_running = (datetime.now() - launch_dt).days
    except Exception:
        days_running = 0

    if closes == 0:
        narrative = (
            f"The bot has been running for **{days_running} day{'s' if days_running != 1 else ''}** "
            "and hasn't completed its first trade yet. "
            "It's scanning for setups — this is normal early on."
        )
    else:
        direction = "up" if total_pnl >= 0 else "down"
        pnl_str = f"${abs(total_pnl):.2f}"
        wr_plain = (
            "winning most of the time"
            if win_rate >= 60
            else "winning slightly more than losing"
            if win_rate >= 52
            else "close to 50/50"
            if win_rate >= 47
            else "losing more trades than it's winning"
        )
        narrative = (
            f"The bot has been running for **{days_running} day{'s' if days_running != 1 else ''}**. "
            f"Across **{closes} trade{'s' if closes != 1 else ''}** it is **{direction} {pnl_str}** "
            f"after all fees. It is currently **{wr_plain}** ({win_rate:.0f}% win rate). "
        )
        if dd["max_dd_usd"] > 5:
            narrative += (
                f"The deepest losing stretch was **${dd['max_dd_usd']:.2f}** "
                f"({dd['max_dd_pct']:.1f}% of the account). "
            )
        if n_pos > 0:
            narrative += (
                f"There {'are' if n_pos != 1 else 'is'} **{n_pos} position"
                f"{'s' if n_pos != 1 else ''}** open right now."
            )
        else:
            narrative += (
                "There are **no open positions** right now — the bot is watching."
            )

    st.markdown(
        f"""<div style="background:rgba(255,255,255,0.02); border-radius:6px;
                 padding:11px 16px; margin-top:6px; margin-bottom:2px;">
          <p style="color:#94a3b8; font-size:0.88em; line-height:1.7; margin:0;">
            {_md_to_html(narrative)}
          </p>
        </div>""",
        unsafe_allow_html=True,
    )
