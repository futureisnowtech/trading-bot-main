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

from data.health import get_error_rate_1h
from data.scanner_data import get_last_scan_age
from data.performance import get_performance_stats
from data.account import get_today_pnl, get_drawdown
from data.positions import get_open_positions
from db import LAUNCH_DATE


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
    no_errors = error_rate == 0

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

    # ── plain-English narrative ───────────────────────────────────────────────
    try:
        launch_dt = datetime.strptime(LAUNCH_DATE[:10], "%Y-%m-%d")
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
