"""Sovereign Kalshi cockpit Streamlit app."""

from __future__ import annotations

import html
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
os.chdir(_ROOT)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dashboard.cockpit_data import get_cockpit_payload
from config import DB_PATH, get_kalshi_hub_exposure_cap

st.set_page_config(
    page_title="Sovereign Kalshi Cockpit",
    page_icon="🌪",
    layout="wide",
    initial_sidebar_state="expanded",
)

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;800&family=IBM+Plex+Mono:wght@400;500&display=swap');

:root {
  --bg-1: #050816;
  --bg-2: #0b132c;
  --panel: rgba(10, 18, 38, 0.82);
  --panel-2: rgba(8, 13, 28, 0.90);
  --line: rgba(74, 242, 214, 0.22);
  --cyan: #4af2d6;
  --mint: #8cffb2;
  --amber: #ffd166;
  --red: #ff6b88;
  --blue: #6fd3ff;
  --text: #eaf6ff;
  --muted: #91a3c2;
}

.stApp {
  background:
    radial-gradient(circle at 15% 18%, rgba(74, 242, 214, 0.16), transparent 24%),
    radial-gradient(circle at 82% 12%, rgba(111, 211, 255, 0.14), transparent 22%),
    radial-gradient(circle at 78% 78%, rgba(140, 255, 178, 0.11), transparent 20%),
    linear-gradient(140deg, #040610 0%, #08111f 42%, #040914 100%);
  color: var(--text);
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(6, 11, 24, 0.98), rgba(4, 8, 18, 0.96));
  border-right: 1px solid rgba(74, 242, 214, 0.10);
}

body, .stMarkdown, .stDataFrame, .stMetric {
  color: var(--text);
}

h1, h2, h3 {
  font-family: "Orbitron", sans-serif;
  letter-spacing: 0.06em;
}

p, li, div, span, label {
  font-family: "IBM Plex Mono", monospace;
}

.hero {
  position: relative;
  overflow: hidden;
  border: 1px solid rgba(74, 242, 214, 0.22);
  background:
    linear-gradient(140deg, rgba(10, 17, 34, 0.94), rgba(5, 8, 20, 0.96)),
    linear-gradient(90deg, rgba(74, 242, 214, 0.18), rgba(111, 211, 255, 0.08));
  border-radius: 24px;
  padding: 1.4rem 1.5rem 1.3rem 1.5rem;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.28), inset 0 1px 0 rgba(255,255,255,0.03);
}

.hero:before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(transparent 0%, rgba(255,255,255,0.03) 50%, transparent 100%);
  transform: translateY(-40%);
  pointer-events: none;
}

.eyebrow {
  color: var(--cyan);
  text-transform: uppercase;
  letter-spacing: 0.24em;
  font-size: 0.78rem;
}

.hero-title {
  font-family: "Orbitron", sans-serif;
  font-size: 2.4rem;
  font-weight: 800;
  margin: 0.28rem 0 0.4rem 0;
}

.hero-sub {
  color: var(--muted);
  max-width: 58rem;
  line-height: 1.5;
}

.chip-row {
  margin-top: 0.8rem;
  display: flex;
  flex-wrap: wrap;
  gap: 0.55rem;
}

.chip {
  border: 1px solid rgba(74, 242, 214, 0.24);
  color: var(--text);
  background: rgba(15, 27, 50, 0.72);
  border-radius: 999px;
  padding: 0.35rem 0.75rem;
  font-size: 0.78rem;
}

.section-title {
  margin-top: 0.3rem;
  margin-bottom: 0.65rem;
  color: var(--blue);
  text-transform: uppercase;
  letter-spacing: 0.16em;
  font-size: 0.92rem;
}

.panel {
  background: linear-gradient(180deg, rgba(11, 19, 41, 0.92), rgba(6, 10, 22, 0.94));
  border: 1px solid rgba(74, 242, 214, 0.14);
  border-radius: 22px;
  padding: 1rem 1rem 0.9rem 1rem;
  min-height: 100%;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 0.85rem;
}

.metric-card {
  border: 1px solid rgba(74, 242, 214, 0.14);
  background: linear-gradient(180deg, rgba(10, 18, 38, 0.90), rgba(5, 10, 22, 0.94));
  border-radius: 20px;
  padding: 0.9rem 1rem;
}

.metric-label {
  font-size: 0.78rem;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.metric-value {
  font-size: 1.65rem;
  margin-top: 0.3rem;
  font-family: "Orbitron", sans-serif;
}

.metric-sub {
  font-size: 0.75rem;
  color: var(--muted);
  margin-top: 0.2rem;
}

.tone-good { color: var(--mint); }
.tone-warn { color: var(--amber); }
.tone-bad { color: var(--red); }
.tone-cyan { color: var(--cyan); }
.tone-blue { color: var(--blue); }

.banner {
  border-left: 4px solid var(--red);
  background: rgba(255, 107, 136, 0.09);
  border-radius: 14px;
  padding: 0.95rem 1rem;
  margin-top: 1rem;
}

.feed-card {
  border: 1px solid rgba(74, 242, 214, 0.10);
  border-radius: 18px;
  padding: 0.85rem 0.95rem;
  margin-bottom: 0.7rem;
  background: rgba(10, 18, 38, 0.70);
}

.feed-top {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
}

.feed-title {
  color: var(--text);
  font-weight: 600;
}

.feed-meta {
  color: var(--muted);
  font-size: 0.76rem;
}

.formula {
  border: 1px solid rgba(111, 211, 255, 0.14);
  border-radius: 16px;
  padding: 0.8rem 0.9rem;
  background: rgba(8, 14, 31, 0.84);
  margin-bottom: 0.65rem;
  color: var(--text);
}

.label-wrap {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
}

.tooltip-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.info-dot {
  width: 1.05rem;
  height: 1.05rem;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(111, 211, 255, 0.30);
  background: rgba(111, 211, 255, 0.10);
  color: var(--blue);
  font-size: 0.72rem;
  cursor: help;
}

.tooltip-bubble {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 10px);
  transform: translateX(-50%);
  width: min(280px, 70vw);
  padding: 0.75rem 0.8rem;
  border-radius: 14px;
  border: 1px solid rgba(74, 242, 214, 0.18);
  background: rgba(5, 10, 22, 0.98);
  color: var(--text);
  font-size: 0.76rem;
  line-height: 1.45;
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.32);
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transition: opacity 0.18s ease, transform 0.18s ease;
  z-index: 20;
}

.tooltip-wrap:hover .tooltip-bubble {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) translateY(-2px);
}

.stage-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.8rem;
}

.stage-card {
  position: relative;
  min-height: 198px;
  border-radius: 22px;
  padding: 1rem 1rem 0.95rem 1rem;
  border: 1px solid rgba(74, 242, 214, 0.16);
  background: linear-gradient(180deg, rgba(9, 17, 36, 0.96), rgba(5, 10, 22, 0.94));
  overflow: hidden;
}

.stage-card:after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(74, 242, 214, 0.08), transparent 55%);
  pointer-events: none;
}

.stage-no {
  color: var(--cyan);
  font-size: 0.75rem;
  letter-spacing: 0.18em;
}

.stage-title {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  margin-top: 0.55rem;
  font-family: "Orbitron", sans-serif;
  font-size: 1rem;
}

.stage-headline {
  margin-top: 0.7rem;
  font-size: 1.06rem;
  color: var(--text);
}

.stage-detail {
  margin-top: 0.7rem;
  color: var(--muted);
  font-size: 0.79rem;
  line-height: 1.5;
}

.stage-pill {
  margin-top: 0.9rem;
  display: inline-flex;
  padding: 0.35rem 0.65rem;
  border-radius: 999px;
  background: rgba(74, 242, 214, 0.10);
  border: 1px solid rgba(74, 242, 214, 0.18);
  color: var(--cyan);
  font-size: 0.74rem;
}

.mini-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.75rem;
}

.mini-card {
  border-radius: 18px;
  padding: 0.85rem 0.9rem;
  border: 1px solid rgba(111, 211, 255, 0.12);
  background: rgba(9, 17, 36, 0.84);
}

.mini-label {
  color: var(--muted);
  font-size: 0.74rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.mini-value {
  margin-top: 0.35rem;
  font-family: "Orbitron", sans-serif;
  font-size: 1.18rem;
  color: var(--blue);
}

.mini-detail {
  margin-top: 0.2rem;
  color: var(--muted);
  font-size: 0.74rem;
}

.insight-shell {
  border-radius: 20px;
  padding: 0.95rem 1rem;
  margin-bottom: 0.8rem;
  border: 1px solid rgba(74, 242, 214, 0.12);
  background: linear-gradient(180deg, rgba(11, 19, 41, 0.86), rgba(5, 10, 22, 0.92));
}

.insight-good { border-left: 4px solid var(--mint); }
.insight-warn { border-left: 4px solid var(--amber); }
.insight-info { border-left: 4px solid var(--blue); }
.insight-bad { border-left: 4px solid var(--red); }

.insight-title {
  font-family: "Orbitron", sans-serif;
  font-size: 0.98rem;
}

.insight-meta {
  color: var(--muted);
  font-size: 0.75rem;
  margin-top: 0.18rem;
}

.insight-body {
  color: var(--text);
  font-size: 0.79rem;
  line-height: 1.5;
  margin-top: 0.55rem;
}

.toggle-shell {
  border-radius: 18px;
  padding: 0.9rem 1rem;
  border: 1px solid rgba(74, 242, 214, 0.12);
  background: rgba(8, 14, 31, 0.72);
}

.book-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.8rem;
  margin-bottom: 0.85rem;
}

.book-card {
  border-radius: 18px;
  padding: 0.9rem 0.95rem;
  background: linear-gradient(180deg, rgba(11, 19, 41, 0.88), rgba(5, 10, 22, 0.95));
  border: 1px solid rgba(74, 242, 214, 0.12);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
}

.book-card-good { border-left: 4px solid var(--mint); }
.book-card-warn { border-left: 4px solid var(--amber); }
.book-card-bad { border-left: 4px solid var(--red); }

.book-card-top {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  align-items: flex-start;
}

.book-card-title {
  font-family: "Orbitron", sans-serif;
  font-size: 0.88rem;
  color: var(--text);
}

.book-card-meta {
  color: var(--muted);
  font-size: 0.72rem;
  margin-top: 0.22rem;
}

.book-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 3.2rem;
  padding: 0.3rem 0.55rem;
  border-radius: 999px;
  border: 1px solid rgba(111, 211, 255, 0.22);
  background: rgba(111, 211, 255, 0.10);
  color: var(--blue);
  font-size: 0.72rem;
}

.book-stat-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.55rem;
  margin-top: 0.75rem;
}

.book-stat {
  border-radius: 14px;
  padding: 0.55rem 0.65rem;
  background: rgba(7, 14, 28, 0.78);
  border: 1px solid rgba(111, 211, 255, 0.10);
}

.book-stat-label {
  color: var(--muted);
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.10em;
}

.book-stat-value {
  margin-top: 0.2rem;
  color: var(--text);
  font-size: 0.86rem;
}

.book-bar {
  height: 8px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(255,255,255,0.06);
  margin-top: 0.7rem;
}

.book-fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(74, 242, 214, 0.9), rgba(111, 211, 255, 0.9));
}

@media (max-width: 1100px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .stage-grid, .mini-grid {
    grid-template-columns: repeat(1, minmax(0, 1fr));
  }
}
</style>
"""


def _fmt_money(value: float | None) -> str:
    value = float(value or 0.0)
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    value = float(value or 0.0)
    return f"{value:.1%}"


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "N/A"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return str(value)


@st.cache_data(ttl=45, show_spinner=False)
def _load_payload(live_sync: bool) -> dict:
    return get_cockpit_payload(live_sync=live_sync)


def _render_html(block: str) -> None:
    if hasattr(st, "html"):
        st.html(block)
    else:
        st.markdown(block, unsafe_allow_html=True)


def _tooltip_dot(text: str | None) -> str:
    if not text:
        return ""
    return (
        '<span class="tooltip-wrap">'
        '<span class="info-dot">i</span>'
        f'<span class="tooltip-bubble">{html.escape(text)}</span>'
        "</span>"
    )


def _metric_card(
    label: str,
    value: str,
    subtitle: str,
    tone: str = "tone-cyan",
    tooltip: str | None = None,
) -> str:
    return f"""
    <div class="metric-card">
      <div class="metric-label">
        <span class="label-wrap">{html.escape(label)}{_tooltip_dot(tooltip)}</span>
      </div>
      <div class="metric-value {tone}">{html.escape(value)}</div>
      <div class="metric-sub">{html.escape(subtitle)}</div>
    </div>
    """


def _mini_card(label: str, value: str, detail: str, tooltip: str | None = None) -> str:
    explain_html = f'<div style="font-size: 0.73em; color: #bbb; margin-top: 5px; line-height: 1.25; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 4px;">{html.escape(tooltip)}</div>' if tooltip else ""
    return f"""
    <div class="mini-card" style="height: auto; min-height: 110px; padding: 12px; margin-bottom: 8px;">
      <div class="mini-label">
        <span class="label-wrap" style="font-weight: bold; color: #888; text-transform: uppercase; font-size: 0.8em; letter-spacing: 0.5px;">{html.escape(label)}</span>
      </div>
      <div class="mini-value" style="font-size: 1.4em; font-weight: bold; color: #fff; margin-top: 2px;">{html.escape(value)}</div>
      <div class="mini-detail" style="font-size: 0.8em; color: #00e5ff; margin-top: 1px;">{html.escape(detail)}</div>
      {explain_html}
    </div>
    """


def _feed_card(title: str, meta: str, body: str, tone: str = "tone-cyan") -> str:
    return f"""
    <div class="feed-card">
      <div class="feed-top">
        <div class="feed-title {tone}">{html.escape(title)}</div>
        <div class="feed-meta">{html.escape(meta)}</div>
      </div>
      <div class="feed-meta" style="margin-top:0.55rem; white-space:pre-wrap;">{html.escape(body)}</div>
    </div>
    """


def _insight_card(title: str, meta: str, body: str, tone: str = "info") -> str:
    tone_class = {
        "good": "insight-good",
        "warn": "insight-warn",
        "bad": "insight-bad",
    }.get(tone, "insight-info")
    return f"""
    <div class="insight-shell {tone_class}">
      <div class="insight-title">{html.escape(title)}</div>
      <div class="insight-meta">{html.escape(meta)}</div>
      <div class="insight-body">{html.escape(body)}</div>
    </div>
    """


def _fmt_hours(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.1f}h"


def _render_open_book_cards(rows: list[dict]) -> None:
    if not rows:
        st.info("No live Kalshi positions are open right now.")
        return

    ordered_rows = sorted(
        rows,
        key=lambda row: (float(row.get("exposure_usd") or 0.0), float(row.get("qty") or 0.0)),
        reverse=True,
    )
    cards: list[str] = []
    for row in ordered_rows:
        exit_pnl = float(row.get("exit_pnl_est") or 0.0)
        tone = "book-card-good" if exit_pnl > 0 else "book-card-warn" if exit_pnl > -0.5 else "book-card-bad"
        weight = max(8.0, min(100.0, float(row.get("book_weight_pct") or 0.0)))
        cards.append(
            f"""
            <div class="book-card {tone}">
              <div class="book-card-top">
                <div>
                  <div class="book-card-title">{html.escape(str(row.get("ticker") or ""))}</div>
                  <div class="book-card-meta">{html.escape(str(row.get("contract_short") or ""))}</div>
                </div>
                <div class="book-chip">{html.escape(str(row.get("side") or ""))}</div>
              </div>
              <div class="book-stat-row">
                <div class="book-stat">
                  <div class="book-stat-label">Exposure</div>
                  <div class="book-stat-value">{html.escape(_fmt_money(row.get("exposure_usd")))}</div>
                </div>
                <div class="book-stat">
                  <div class="book-stat-label">Mark P&L</div>
                  <div class="book-stat-value">{html.escape(_fmt_money(row.get("gross_mark_pnl")))}</div>
                </div>
                <div class="book-stat">
                  <div class="book-stat-label">Exit P&L</div>
                  <div class="book-stat-value">{html.escape(_fmt_money(row.get("exit_pnl_est")))}</div>
                </div>
                <div class="book-stat">
                  <div class="book-stat-label">Resolves</div>
                  <div class="book-stat-value">{html.escape(_fmt_hours(row.get("hours_to_resolution")))}</div>
                </div>
              </div>
              <div class="book-card-meta" style="margin-top:0.7rem;">
                {html.escape(str(row.get("hub") or "UNKNOWN"))} hub • {html.escape(str(int(float(row.get("qty") or 0.0))))} contracts • {html.escape(str(row.get("state_label") or ""))}
              </div>
              <div class="book-bar"><div class="book-fill" style="width:{weight:.2f}%"></div></div>
              <div class="book-card-meta" style="margin-top:0.45rem;">{weight:.1f}% of live book exposure</div>
            </div>
            """
        )

    _render_html('<div class="book-grid">' + "".join(cards) + "</div>")


def _render_open_book_heatmap(rows: list[dict]) -> None:
    if not rows:
        st.info("No live Kalshi positions are open right now.")
        return

    book_df = pd.DataFrame(rows)
    long_rows: list[dict] = []
    for row in rows:
        for label, value in [
            ("Book Weight %", row.get("book_weight_pct")),
            ("Mark % on Risk", row.get("mark_pnl_pct_on_risk")),
            ("Exit % on Risk", row.get("exit_pnl_pct_on_risk")),
        ]:
            if value is None:
                continue
            long_rows.append(
                {
                    "display_label": row.get("display_label"),
                    "ticker": row.get("ticker"),
                    "contract_short": row.get("contract_short"),
                    "hub": row.get("hub"),
                    "side": row.get("side"),
                    "metric_label": label,
                    "metric_value": float(value),
                    "exposure_usd": float(row.get("exposure_usd") or 0.0),
                    "gross_mark_pnl": float(row.get("gross_mark_pnl") or 0.0),
                    "exit_pnl_est": float(row.get("exit_pnl_est") or 0.0),
                    "hours_to_resolution": row.get("hours_to_resolution"),
                }
            )

    if not long_rows:
        st.info("Open positions do not yet have enough price data for a heat map.")
        return

    long_df = pd.DataFrame(long_rows)
    display_order = (
        book_df.sort_values(["exposure_usd", "exit_pnl_est"], ascending=[False, True])["display_label"]
        .drop_duplicates()
        .tolist()
    )
    metric_order = ["Book Weight %", "Mark % on Risk", "Exit % on Risk"]

    heat = (
        alt.Chart(long_df)
        .mark_rect(cornerRadius=8)
        .encode(
            x=alt.X("metric_label:N", sort=metric_order, title=None),
            y=alt.Y("display_label:N", sort=display_order, title=None),
            color=alt.Color(
                "metric_value:Q",
                title="Percent",
                scale=alt.Scale(domainMid=0, range=["#ff6b88", "#15243d", "#8cffb2"]),
            ),
            tooltip=[
                alt.Tooltip("ticker:N", title="Ticker"),
                alt.Tooltip("contract_short:N", title="Contract"),
                alt.Tooltip("hub:N", title="Hub"),
                alt.Tooltip("side:N", title="Side"),
                alt.Tooltip("metric_label:N", title="Metric"),
                alt.Tooltip("metric_value:Q", title="Percent", format=".2f"),
                alt.Tooltip("exposure_usd:Q", title="Exposure", format=".2f"),
                alt.Tooltip("gross_mark_pnl:Q", title="Mark P&L", format=".2f"),
                alt.Tooltip("exit_pnl_est:Q", title="Exit P&L", format=".2f"),
                alt.Tooltip("hours_to_resolution:Q", title="Hours Left", format=".1f"),
            ],
        )
    )
    text = heat.mark_text(color="#eaf6ff", fontSize=11).encode(
        text=alt.Text("metric_value:Q", format=".1f")
    )
    chart = (
        (heat + text)
        .properties(height=max(240, len(display_order) * 40))
        .configure_view(strokeOpacity=0)
        .configure_axis(
            labelColor="#eaf6ff",
            titleColor="#91a3c2",
            gridColor="rgba(145,163,194,0.18)",
            domainColor="rgba(145,163,194,0.12)",
            tickColor="rgba(145,163,194,0.12)",
        )
        .configure_legend(labelColor="#eaf6ff", titleColor="#91a3c2")
    )
    st.altair_chart(chart, width="stretch")


def _render_open_book_expiry_chart(rows: list[dict]) -> None:
    if not rows:
        st.info("No live Kalshi positions are open right now.")
        return

    df = pd.DataFrame(rows)
    df = df[df["hours_to_resolution"].notna()].copy()
    if df.empty:
        st.info("No open positions currently have a valid resolution timestamp.")
        return

    base_chart = (
        alt.Chart(df)
        .mark_circle(opacity=0.88, stroke="#eaf6ff", strokeWidth=0.7)
        .encode(
            x=alt.X(
                "hours_to_resolution:Q",
                title="Hours To Resolution",
                axis=alt.Axis(grid=True, tickCount=6),
            ),
            y=alt.Y(
                "exit_pnl_est:Q",
                title="Estimated Exit P&L ($)",
                axis=alt.Axis(grid=True),
            ),
            size=alt.Size(
                "exposure_usd:Q",
                title="Exposure",
                scale=alt.Scale(range=[140, 2200]),
            ),
            color=alt.Color("hub:N", title="Hub"),
            shape=alt.Shape("side:N", title="Side"),
            tooltip=[
                alt.Tooltip("ticker:N", title="Ticker"),
                alt.Tooltip("contract_short:N", title="Contract"),
                alt.Tooltip("hub:N", title="Hub"),
                alt.Tooltip("side:N", title="Side"),
                alt.Tooltip("exposure_usd:Q", title="Exposure", format=".2f"),
                alt.Tooltip("gross_mark_pnl:Q", title="Mark P&L", format=".2f"),
                alt.Tooltip("exit_pnl_est:Q", title="Exit P&L", format=".2f"),
                alt.Tooltip("hours_to_resolution:Q", title="Hours Left", format=".1f"),
                alt.Tooltip("book_weight_pct:Q", title="Book Weight %", format=".1f"),
            ],
        )
        .properties(height=340)
    )

    zero_rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        color="rgba(255,255,255,0.24)",
        strokeDash=[6, 4],
    ).encode(y="y:Q")

    final_chart = (
        (base_chart + zero_rule)
        .configure_view(strokeOpacity=0)
        .configure_axis(
            labelColor="#eaf6ff",
            titleColor="#91a3c2",
            gridColor="rgba(145,163,194,0.18)",
            domainColor="rgba(145,163,194,0.12)",
            tickColor="rgba(145,163,194,0.12)",
        )
        .configure_legend(labelColor="#eaf6ff", titleColor="#91a3c2")
    )
    st.altair_chart(final_chart, width="stretch")


def _funnel_stage_card(stage: dict) -> str:
    return f"""
    <div class="stage-card">
      <div class="stage-no">{html.escape(str(stage.get('stage') or ''))}</div>
      <div class="stage-title">
        <span>{html.escape(str(stage.get('label') or ''))}</span>
        {_tooltip_dot(str(stage.get('tooltip') or ''))}
      </div>
      <div class="stage-headline">{html.escape(str(stage.get('headline') or ''))}</div>
      <div class="stage-detail">{html.escape(str(stage.get('detail') or ''))}</div>
      <div class="stage-pill">{html.escape(str(stage.get('pill') or ''))}</div>
    </div>
    """


def _render_trade_edge_chart(rows: list[dict]) -> None:
    if not rows:
        st.info("No recent BUY trades with stored model probabilities are available for edge visualization yet.")
        return

    edge_df = pd.DataFrame(rows)
    symbol_order = edge_df["symbol"].drop_duplicates().tolist()
    long_df = edge_df.melt(
        id_vars=["symbol", "side", "strategy", "ts", "market_price_pct"],
        value_vars=["model_confidence_pct", "edge_pct"],
        var_name="metric",
        value_name="percent",
    )
    long_df["metric_label"] = long_df["metric"].map(
        {
            "model_confidence_pct": "Model Confidence",
            "edge_pct": "Model Edge",
        }
    )

    chart = (
        alt.Chart(long_df)
        .mark_bar(cornerRadiusEnd=6, size=16)
        .encode(
            y=alt.Y("symbol:N", sort=symbol_order, title=None),
            yOffset=alt.YOffset("metric_label:N"),
            x=alt.X(
                "percent:Q",
                title="Percent",
                scale=alt.Scale(domain=[0, 100]),
                axis=alt.Axis(grid=True, tickCount=6),
            ),
            color=alt.Color(
                "metric_label:N",
                legend=alt.Legend(title=None, orient="top"),
                scale=alt.Scale(
                    domain=["Model Confidence", "Model Edge"],
                    range=["#4af2d6", "#ffd166"],
                ),
            ),
            tooltip=[
                alt.Tooltip("symbol:N", title="Symbol"),
                alt.Tooltip("side:N", title="Side"),
                alt.Tooltip("strategy:N", title="Strategy"),
                alt.Tooltip("ts:N", title="Logged At"),
                alt.Tooltip("market_price_pct:Q", title="Paid Price %", format=".1f"),
                alt.Tooltip("metric_label:N", title="Bar"),
                alt.Tooltip("percent:Q", title="Percent", format=".1f"),
            ],
        )
        .properties(height=max(220, len(symbol_order) * 58))
        .configure_view(strokeOpacity=0)
        .configure_axis(
            labelColor="#eaf6ff",
            titleColor="#91a3c2",
            gridColor="rgba(145,163,194,0.18)",
            domainColor="rgba(145,163,194,0.12)",
            tickColor="rgba(145,163,194,0.12)",
        )
        .configure_legend(labelColor="#eaf6ff", titleColor="#91a3c2")
    )
    st.altair_chart(chart, width="stretch")


def _render_weather_type_boards(
    boards: list[dict],
    market_type_counts: list[dict],
) -> None:
    counts_map = {
        str(row.get("bucket") or ""): int(row.get("active_contracts") or 0)
        for row in (market_type_counts or [])
    }
    if not boards:
        st.info("No weather-type boards are available yet.")
        return

    tabs = st.tabs(
        [
            f"{board.get('bucket')} ({int(board.get('position_count') or 0)})"
            for board in boards
        ]
    )
    for tab, board in zip(tabs, boards):
        with tab:
            rows = list(board.get("rows") or [])
            summary = board.get("summary") or {}
            bucket = str(board.get("bucket") or "Weather")
            active_contracts = counts_map.get(bucket, 0)
            if rows:
                _render_html(
                    '<div class="mini-grid">'
                    + _mini_card(
                        "Open Positions",
                        str(board.get("position_count") or 0),
                        f"{int(board.get('contract_count') or 0)} contracts live",
                    )
                    + _mini_card(
                        "Book Exposure",
                        _fmt_money(summary.get("total_exposure_usd")),
                        f"{active_contracts} active contracts in scan universe",
                    )
                    + _mini_card(
                        "Emergency Exit P&L",
                        _fmt_money(summary.get("total_exit_pnl_est_usd")),
                        "same liquidation math as the main board",
                    )
                    + "</div>"
                )
                _render_open_book_cards(rows)
            else:
                st.info(
                    f"No open {bucket.lower()} positions right now. "
                    f"The live universe still has {active_contracts} active contract rows in this lane."
                )


_render_html(_CSS)

with st.sidebar:
    st.markdown("## Cockpit Controls")
    st.caption("Broker truth is cached for 45 seconds to keep the cockpit sharp without burning Kalshi calls.")
    live_sync = st.toggle("Broker Sync", value=True, help="When on, the cockpit pulls live balance, positions, and mark data from Kalshi.")
    if st.button("Refresh Now", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.markdown("### Display")
    st.caption("Read-only cockpit. No order writes, no state mutations.")

payload = _load_payload(live_sync)
truth = payload["truth"]
release_status = payload["release_status"]
lane = truth.get("forecast_lane") or {}
regime = payload["regime"]
deploy = payload["deploy"]
positions_live = payload["positions_live"]
positions_db_only = payload["positions_db_only"]
positions_paper_a = payload.get("positions_paper_a") or []
positions_paper_b = payload.get("positions_paper_b") or []
history_paper_a = payload.get("history_paper_a") or []
history_paper_b = payload.get("history_paper_b") or []
open_book_visual = payload["open_book_visual"]
open_book_summary = payload["open_book_summary"]
recent_trades = payload["recent_trades"]
trade_edge_rows = payload["trade_edge_rows"]
recent_events = payload["recent_events"]
notifications = payload["notifications"]
recent_vetoes = payload["recent_vetoes"]
storage = payload["storage"]
market_counts = payload["market_counts"]
snapshot = payload.get("snapshot") or {}
metric_explainers = payload["metric_explainers"]
decision_funnel = payload["decision_funnel"]
regime_cards = payload["regime_cards"]
ai_insights = payload["ai_insights"]
weather_type_boards = payload.get("weather_type_boards") or []
weather_type_counts = payload.get("weather_type_counts") or []

balance = float(truth.get("balance_usd") or 0.0)
port_val = float(truth.get("portfolio_value", 0.0) or 0.0) / 100.0 if "portfolio_value" in truth else 0.0
total_equity = balance + port_val

stark_matrix = payload.get("stark_matrix") or {}
win_rate_7d = float(stark_matrix.get("win_rate_7d") or 0.0)
pnl_48h = float(stark_matrix.get("pnl_48h") or 0.0)
wins_7d = int(stark_matrix.get("wins_7d") or 0)
losses_7d = int(stark_matrix.get("losses_7d") or 0)
total_7d = int(stark_matrix.get("total_7d") or 0)

drift = truth.get("position_drift") or {}
positions_count = len(positions_live)
realized_curve = payload["realized_pnl_curve"]
win_rate_stats = payload.get("session_win_rate", {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_won_usd": 0.0, "total_lost_usd": 0.0})
win_rate_val = win_rate_stats["win_rate"]
total_won = win_rate_stats.get("total_won_usd", 0.0)
total_lost = win_rate_stats.get("total_lost_usd", 0.0)
realized_pnl = total_won + total_lost
hub_cap_now = get_kalshi_hub_exposure_cap(balance)

# ════════════════════════════════════════════════════════════════════
# WEATHERMAN BOT & PHYSICS PAPER TRIAL TIMER
# ════════════════════════════════════════════════════════════════════
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Load or init Paper Trial Start Time
TRIAL_FILE = Path("/app/logs/paper_trial_start.json")
if not TRIAL_FILE.parent.exists():
    TRIAL_FILE = Path(DB_PATH).parent / "paper_trial_start.json"

if not TRIAL_FILE.exists():
    try:
        with open(TRIAL_FILE, "w") as f:
            json.dump({"start_time": datetime.now(timezone.utc).isoformat()}, f)
    except Exception:
        pass

try:
    with open(TRIAL_FILE) as f:
        trial_data = json.load(f)
    trial_start = datetime.fromisoformat(trial_data["start_time"])
except Exception:
    trial_start = datetime.now(timezone.utc)

trial_end = trial_start + timedelta(days=7)
now_utc = datetime.now(timezone.utc)
remaining = trial_end - now_utc
remaining_seconds = max(0, int(remaining.total_seconds()))

# Load paper lane balances & PnL
paper_bal_a = 1000.0
paper_bal_b = 1000.0
bal_a_file = Path(DB_PATH).parent / "paper_balance.json"
bal_b_file = Path(DB_PATH).parent / "paper_balance_lane_b.json"

if bal_a_file.exists():
    try:
        with open(bal_a_file) as f:
            paper_bal_a = float(json.load(f).get("balance", 1000.0))
    except Exception:
        pass

if bal_b_file.exists():
    try:
        with open(bal_b_file) as f:
            paper_bal_b = float(json.load(f).get("balance", 1000.0))
    except Exception:
        pass

pnl_a = paper_bal_a - 1000.0
pnl_b = paper_bal_b - 1000.0

try:
    conn = sqlite3.connect(DB_PATH)
    rows_a_act = conn.execute("SELECT count(*) FROM forecast_positions_paper WHERE active = 1").fetchone()
    paper_active_a = rows_a_act[0] if rows_a_act else 0
    rows_b_act = conn.execute("SELECT count(*) FROM forecast_positions_paper_lane_b WHERE active = 1").fetchone()
    paper_active_b = rows_b_act[0] if rows_b_act else 0
    rows_a_tot = conn.execute("SELECT count(*) FROM forecast_positions_paper").fetchone()
    paper_total_a = rows_a_tot[0] if rows_a_tot else 0
    rows_b_tot = conn.execute("SELECT count(*) FROM forecast_positions_paper_lane_b").fetchone()
    paper_total_b = rows_b_tot[0] if rows_b_tot else 0
    conn.close()
except Exception:
    paper_active_a = 0
    paper_active_b = 0
    paper_total_a = 0
    paper_total_b = 0

# ── Jarvis Expanding Orb Widget ─────────────────────────────────────
jarvis_open = st.session_state.get("show_jarvis", False)

try:
    from dashboard.jarvis_assets import JARVIS_REACTOR_BASE64
except ImportError:
    JARVIS_REACTOR_BASE64 = ""

# Dimensions: dormant = 500px, active = 400px (80% size)
_size = 400 if jarvis_open else 500
_mobile_size = 200 if jarvis_open else 260
_mobile_ring = _mobile_size + 20
_ring_inset = -12 if jarvis_open else -15
_border_style = "solid" if jarvis_open else "dashed"
_border_opacity = "0.5" if jarvis_open else "0.85"

import streamlit.components.v1 as components

st.markdown(
    f"""
    <style>
    /* Fix Streamlit chat input text visibility */
    div[data-testid="stChatInput"] textarea,
    div[data-testid="stChatInput"] input {{
        color: #ffffff !important;
        background-color: rgba(6, 15, 35, 0.95) !important;
        font-family: 'IBM Plex Mono', monospace !important;
        font-size: 15px !important;
        caret-color: #00e5ff !important;
    }}
    div[data-testid="stChatInput"] textarea::placeholder,
    div[data-testid="stChatInput"] input::placeholder {{
        color: rgba(0, 229, 255, 0.6) !important;
    }}
    /* Force st.chat_input to render inline directly inside Jarvis HUD instead of screen bottom */
    div[data-testid="stChatInput"] {{
        position: relative !important;
        bottom: auto !important;
        margin: 15px auto !important;
        width: 100% !important;
        max-width: 700px !important;
        border: 1px solid rgba(0, 229, 255, 0.4) !important;
        border-radius: 12px !important;
        box-shadow: 0 0 15px rgba(0, 229, 255, 0.2) !important;
    }}

    /* Completely hide un-needed floating outer dotted circle */
    div.element-container:has(div.jarvis-orb-anchor) + div.element-container div.stButton::after {{
        display: none !important;
    }}

    .jarvis-orb-wrap {{
        display: flex;
        flex-direction: column;
        align-items: center;
        margin: 10px auto;
        width: 100%;
    }}
    .jarvis-orb-anchor {{
        display: block;
        height: 0px;
        margin: 0;
        padding: 0;
    }}
    
    /* Target the st.button container wrapper */
    div.element-container:has(div.jarvis-orb-anchor) + div.element-container {{
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        width: 100% !important;
        margin: 10px auto !important;
        padding: 0 !important;
    }}

    div.element-container:has(div.jarvis-orb-anchor) + div.element-container div.stButton {{
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        width: 100% !important;
        margin: 0 auto !important;
        position: relative !important;
    }}
    
    /* Target the button itself to style it as the pulsing reactor core */
    div.element-container:has(div.jarvis-orb-anchor) + div.element-container div.stButton > button {{
        width: {_size}px !important;
        height: {_size}px !important;
        border-radius: 50% !important;
        background-image: url("data:image/jpeg;base64,{JARVIS_REACTOR_BASE64}") !important;
        background-size: cover !important;
        background-position: center !important;
        border: 3px solid rgba(0, 229, 255, 0.4) !important;
        box-shadow: 0 0 50px rgba(0, 229, 255, 0.6), inset 0 0 30px rgba(0, 229, 255, 0.3) !important;
        cursor: pointer !important;
        color: transparent !important;
        font-size: 0px !important;
        padding: 0 !important;
        margin: 0 auto !important;
        transition: all 0.4s ease-in-out !important;
        animation: pulseJ 3s infinite alternate ease-in-out;
    }}
    
    div.element-container:has(div.jarvis-orb-anchor) + div.element-container div.stButton > button:hover {{
        box-shadow: 0 0 80px rgba(0, 229, 255, 0.95), inset 0 0 45px rgba(0, 229, 255, 0.4) !important;
        transform: scale(1.02) !important;
        border-color: rgba(0, 229, 255, 0.8) !important;
    }}
    
    /* Outer rotating data ring mathematically centered using 50% transform */
    div.element-container:has(div.jarvis-orb-anchor) + div.element-container div.stButton::after {{
        content: '';
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        width: {_size + 30}px;
        height: {_size + 30}px;
        border-radius: 50%;
        border: 4px {_border_style} rgba(0, 229, 255, {_border_opacity});
        animation: rotJ 20s linear infinite;
        box-shadow: 0 0 35px rgba(0, 229, 255, 0.35);
        pointer-events: none;
        z-index: 2;
        transition: all 0.4s ease-in-out;
    }}
    
    @keyframes rotJ {{
        to {{ transform: rotate(360deg); }}
    }}
    @keyframes pulseJ {{
        0% {{ transform: scale(0.98); filter: brightness(0.9); }}
        100% {{ transform: scale(1.02); filter: brightness(1.15); }}
    }}
    .jarvis-label {{
        text-align: center;
        margin-top: 15px;
        font-weight: bold;
        letter-spacing: 3px;
        color: #00e5ff;
        font-size: 0.95em;
        text-shadow: 0 0 8px rgba(0, 229, 255, 0.4);
    }}

    /* Mobile/iOS Safari Responsive Constraints */
    @media (max-width: 600px) {{
        div.element-container:has(div.jarvis-orb-anchor) + div.element-container div.stButton > button {{
            width: {_mobile_size}px !important;
            height: {_mobile_size}px !important;
        }}
        div.element-container:has(div.jarvis-orb-anchor) + div.element-container div.stButton::after {{
            width: {_mobile_ring}px !important;
            height: {_mobile_ring}px !important;
            border-width: 3px !important;
            box-shadow: 0 0 25px rgba(0, 229, 255, 0.4) !important;
        }}
        .jarvis-label {{
            margin-top: 12px !important;
            font-size: 0.8em !important;
            letter-spacing: 1.5px !important;
        }}
        .jarvis-chat-bubble {{
            padding: 12px 8px 8px 8px !important;
            border-radius: 18px !important;
            margin: 10px auto !important;
        }}
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

# Render the anchor and the button (which acts as the orb)
st.markdown('<div class="jarvis-orb-anchor"></div>', unsafe_allow_html=True)
if st.button("⚡", key="reactor_toggle_btn"):
    st.session_state.show_jarvis = not st.session_state.get("show_jarvis", False)
    st.rerun()

# ── Dormant state countdown & Autonomous 4-Hour Holographic Crystal Tips ──
if not jarvis_open:
    st.markdown('<div class="jarvis-orb-wrap">', unsafe_allow_html=True)
    # Countdown sits right under the dormant orb
    str_pnl_a = f"${pnl_a:+.2f}"
    str_pnl_b = f"${pnl_b:+.2f}"
    str_pnl_48h = f"${pnl_48h:+.2f}"
    str_eq = f"${total_equity:,.2f}"
    str_cash = f"${balance:,.2f}"

    countdown_html = f"""
    <div style="font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; padding:6px 2px; max-width:750px; margin:0 auto;">
        <!-- Stark Holographic Matrix Cards Grid -->
        <div style="display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:10px; margin-bottom:10px;">
            <div style="background:radial-gradient(ellipse at top left, rgba(0,229,255,0.15), rgba(5,10,22,0.95)); border:1px solid rgba(0,229,255,0.35); border-radius:18px; padding:12px; box-shadow:0 0 20px rgba(0,229,255,0.15);">
                <div style="font-size:0.7em; font-weight:bold; letter-spacing:1.5px; color:#4af2d6; text-transform:uppercase;">🏆 WIN RATE (7-DAY)</div>
                <div style="font-size:1.6em; font-weight:bold; color:#ffffff; font-family:monospace; margin-top:2px;">{win_rate_7d:.1f}%</div>
                <div style="font-size:0.75em; color:#a5d6a7; margin-top:1px;">{wins_7d} Wins / {losses_7d} Losses ({total_7d} Total)</div>
            </div>
            
            <div style="background:radial-gradient(ellipse at top right, rgba(0,255,136,0.15), rgba(5,10,22,0.95)); border:1px solid rgba(0,255,136,0.35); border-radius:18px; padding:12px; box-shadow:0 0 20px rgba(0,255,136,0.15);">
                <div style="font-size:0.7em; font-weight:bold; letter-spacing:1.5px; color:#69ffb4; text-transform:uppercase;">📈 REALIZED P&L (48-HOUR)</div>
                <div style="font-size:1.6em; font-weight:bold; color:#00ff88; font-family:monospace; margin-top:2px;">{str_pnl_48h}</div>
                <div style="font-size:0.75em; color:#a5d6a7; margin-top:1px;">Net Bank Cashflow Last 48H</div>
            </div>

            <div style="background:radial-gradient(ellipse at bottom left, rgba(0,229,255,0.15), rgba(5,10,22,0.95)); border:1px solid rgba(0,229,255,0.35); border-radius:18px; padding:12px; box-shadow:0 0 20px rgba(0,229,255,0.15);">
                <div style="font-size:0.7em; font-weight:bold; letter-spacing:1.5px; color:#4af2d6; text-transform:uppercase;">🛡️ ACCOUNT EQUITY</div>
                <div style="font-size:1.6em; font-weight:bold; color:#00e5ff; font-family:monospace; margin-top:2px;">{str_eq}</div>
                <div style="font-size:0.75em; color:#8899a6; margin-top:1px;">Cash: {str_cash} | Active: {positions_count}</div>
            </div>

            <div style="background:radial-gradient(ellipse at bottom right, rgba(255,213,79,0.15), rgba(5,10,22,0.95)); border:1px solid rgba(255,213,79,0.35); border-radius:18px; padding:12px; box-shadow:0 0 20px rgba(255,213,79,0.15);">
                <div style="font-size:0.7em; font-weight:bold; letter-spacing:1.5px; color:#ffd54f; text-transform:uppercase;">⏳ 7-DAY AUDIT TRIAL</div>
                <div id="timer" style="font-size:1.6em; font-weight:bold; color:#ffd54f; font-family:monospace; margin-top:2px;">--:--:--</div>
                <div style="font-size:0.75em; color:#8899a6; margin-top:1px;">A: {str_pnl_a} | B: {str_pnl_b}</div>
            </div>
        </div>

        <script>
            var endTime = {int(trial_end.timestamp() * 1000)};
            function updateTimer() {{
                var now = new Date().getTime(), d = endTime - now;
                if (d < 0) {{ document.getElementById("timer").innerHTML = "AUDIT COMPLETE"; document.getElementById("timer").style.color = "#ffd54f"; return; }}
                var days = Math.floor(d / 86400000);
                var h = Math.floor((d % 86400000) / 3600000);
                var m = Math.floor((d % 3600000) / 60000);
                var s = Math.floor((d % 60000) / 1000);
                document.getElementById("timer").innerHTML = days + "d " + (h<10?"0":"")+h + ":" + (m<10?"0":"")+m + ":" + (s<10?"0":"")+s;
            }}
            updateTimer(); setInterval(updateTimer, 1000);
        </script>
    </div>
    """
    components.html(countdown_html, height=195)
    st.markdown('<div class="jarvis-label">TAP REACTOR ORB TO INITIATE J.A.R.V.I.S. INTEL</div>', unsafe_allow_html=True)
    
    # ── Autonomous 4-Hour Holographic Crystal Shards Pool ──
    ALL_CRYSTAL_TIPS = [
        {"label": "💧 Evaporative Cooling", "prompt": "Audit the evaporative cooling precip deltas on our active contracts and check if high temps are suppressed.", "icon": "💧"},
        {"label": "💨 Nocturnal Wind Shear", "prompt": "Check overnight wind shear metrics and explain how low temp floors are being adjusted.", "icon": "💨"},
        {"label": "☀️ Dry Soil Memory", "prompt": "Inspect rolling 7-day soil moisture totals and explain dry soil warm bias modeling.", "icon": "☀️"},
        {"label": "🛡️ Hub Risk Cap", "prompt": "Check our hub concentration exposure across Miami, Austin, Phoenix, Chicago, and NYC.", "icon": "🛡️"},
        {"label": "📊 GFS vs ECMWF Spread", "prompt": "Compare GFS vs ECMWF ensemble path divergence across open contracts.", "icon": "📊"},
        {"label": "🚀 Paper Lane B Alpha", "prompt": "Query Paper Lane B 10X Maker positions and analyze order book queue fill rates.", "icon": "🚀"},
        {"label": "⚖️ Dynamic Kelly Sizing", "prompt": "Explain how fractional Kelly sizing ($15-$35 brackets) is capping capital drawdown.", "icon": "⚖️"},
        {"label": "🎯 Take Profit Trigger", "prompt": "Audit open positions near the 85¢ take-profit exit threshold.", "icon": "🎯"},
        {"label": "🔄 Truth Drift Audit", "prompt": "Run full reconciliation between local SQLite position ledger and live Kalshi exchange.", "icon": "🔄"},
        {"label": "⏱️ Forecast Freshness", "prompt": "Check forecast model freshness and list any recent model age vetoes.", "icon": "⏱️"},
        {"label": "📈 PnL Performance", "prompt": "Provide a complete breakdown of our PnL since July 29th across all trading lanes.", "icon": "📈"},
        {"label": "🤖 Droplet Integrity", "prompt": "Inspect container disk space, bot logs, and background process uptime on the droplet.", "icon": "🤖"},
    ]

    st.markdown("""
    <style>
    /* Crystal tip buttons — force visible white text on dark background */
    div[data-testid="stButton"] > button {
        background: rgba(0, 229, 255, 0.08) !important;
        border: 1px solid rgba(0, 229, 255, 0.35) !important;
        color: #e0f7fa !important;
        font-weight: 600 !important;
        font-size: 0.82em !important;
        letter-spacing: 0.5px !important;
        border-radius: 8px !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="stButton"] > button:hover {
        background: rgba(0, 229, 255, 0.22) !important;
        border-color: rgba(0, 229, 255, 0.7) !important;
        color: #ffffff !important;
        box-shadow: 0 0 12px rgba(0, 229, 255, 0.4) !important;
    }
    div[data-testid="stButton"] > button p {
        color: #e0f7fa !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # Rotate 4 active crystal tips every 4 hours based on epoch
    epoch_4h = int(datetime.now(timezone.utc).timestamp()) // (4 * 3600)
    active_crystals = [ALL_CRYSTAL_TIPS[(epoch_4h + i) % len(ALL_CRYSTAL_TIPS)] for i in range(4)]

    st.markdown("<div style='text-align: center; margin-top: 15px; font-size: 0.75em; letter-spacing: 2px; color: #81c784; font-weight: bold;'>💎 AUTONOMOUS HUD CRYSTAL INSIGHTS (AUTO-ROTATES EVERY 4H)</div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    for idx, tip in enumerate(active_crystals):
        col = c1 if idx % 2 == 0 else c2
        if col.button(f"{tip['icon']} {tip['label']}", use_container_width=True, key=f"crystal_btn_{idx}_{epoch_4h}"):
            st.session_state.jarvis_prompt_input = tip["prompt"]
            st.session_state.show_jarvis = True
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

# ── Active state: expanded orb becomes chat interface ───────────────
else:
    st.markdown(
        """
        <style>
        .jarvis-chat-bubble {
            max-width: 700px;
            margin: 20px auto;
            background: radial-gradient(ellipse at top, rgba(0,30,60,0.95) 0%, rgba(5,8,22,0.98) 100%);
            border: 2px solid rgba(0, 229, 255, 0.4);
            border-radius: 28px;
            padding: 20px 18px 12px 18px;
            box-shadow: 0 0 50px rgba(0, 229, 255, 0.25), inset 0 0 30px rgba(0, 229, 255, 0.05);
            position: relative;
            overflow: hidden;
        }
        .jarvis-chat-bubble::before {
            content: '';
            position: absolute;
            inset: -2px;
            border-radius: 28px;
            border: 2px solid transparent;
            background: conic-gradient(from 0deg, transparent 0%, rgba(0,229,255,0.6) 25%, transparent 50%, rgba(0,229,255,0.3) 75%, transparent 100%) border-box;
            -webkit-mask: linear-gradient(#fff 0 0) padding-box, linear-gradient(#fff 0 0);
            -webkit-mask-composite: xor;
            mask-composite: exclude;
            animation: rotJ 8s linear infinite;
            pointer-events: none;
            z-index: 0;
        }
        .jarvis-header-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 10px;
        }
        .jarvis-header-title {
            font-size: 1.1em;
            font-weight: bold;
            color: #00e5ff;
            letter-spacing: 1px;
            text-shadow: 0 0 10px rgba(0,229,255,0.5);
        }
        .jarvis-header-sub {
            font-size: 0.75em;
            color: #81c784;
        }
        .jarvis-close-hint {
            position: relative;
            z-index: 10;
        }
        .jarvis-close-hint div.stButton > button {
            background: rgba(255,82,82,0.15) !important;
            border: 1px solid rgba(255,82,82,0.4) !important;
            border-radius: 50% !important;
            width: 36px !important;
            height: 36px !important;
            min-width: 36px !important;
            font-size: 16px !important;
            color: #ff5252 !important;
            padding: 0 !important;
            line-height: 36px !important;
        }
        .jarvis-close-hint div.stButton > button:hover {
            background: rgba(255,82,82,0.35) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="jarvis-chat-bubble">', unsafe_allow_html=True)

    # Header row with title + close button
    hdr_l, hdr_r = st.columns([5, 1])
    with hdr_l:
        st.markdown(
            f'''
            <div style="display: flex; align-items: center; gap: 15px;">
                <div style="width: 50px; height: 50px; border-radius: 50%; border: 2px solid #00e5ff; box-shadow: 0 0 10px rgba(0,229,255,0.5); overflow: hidden; flex-shrink: 0;">
                    <img src="data:image/jpeg;base64,{JARVIS_REACTOR_BASE64}" style="width: 100%; height: 100%; object-fit: cover;" />
                </div>
                <div>
                    <div class="jarvis-header-title">⚡ WEATHERMAN BOT</div>
                    <div class="jarvis-header-sub">Diagnostic agent · droplet sqlite · live log trails</div>
                </div>
            </div>
            ''',
            unsafe_allow_html=True,
        )
    with hdr_r:
        st.markdown('<div class="jarvis-close-hint">', unsafe_allow_html=True)
        if st.button("✕", key="jarvis_close_btn"):
            st.session_state.show_jarvis = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # Suggestion chips
    sug_cols = st.columns(5)
    labels = [
        "📊 Health",
        "🌡️ Edge",
        "📑 Trades",
        "🔄 Drift",
        "📈 Paper v Live",
    ]
    prompts_map = {
        "📊 Health": "Run a complete audit on our runtime container logs, check the disk free space, and print the last 20 warning or error log lines.",
        "🌡️ Edge": "Retrieve the model forecast probabilities (GFS vs ECMWF) for our Miami low contract KXLOWTMIA-26AUG01-T80, check what the current recorded low is, and verify if we have any edge.",
        "📑 Trades": "Query the SQLite database for the last 5 trades, calculate our net edge vs paid market prices, and give me a summary of how we performed.",
        "🔄 Drift": "Fetch our live holdings from the Kalshi API, cross-reference them with our local database positions table, and run reconciliation to ensure there is zero truth drift.",
        "📈 Paper v Live": "Provide a comparative analysis of our live realized PnL versus our new dynamic physics paper-trading curve to see if the boundary models are outperforming.",
    }
    for col, label in zip(sug_cols, labels):
        if col.button(label, use_container_width=True, key=f"sug_{label}"):
            st.session_state.jarvis_prompt_input = prompts_map[label]
            st.session_state.show_jarvis = True
            st.rerun()

    # Chat history
    if "jarvis_history" not in st.session_state:
        st.session_state.jarvis_history = [
            {"role": "assistant", "content": "Weatherman Bot online. Ask me to audit trades, explain positions, pull logs, or flatten a contract."}
        ]

    for msg in st.session_state.jarvis_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Process queued prompt from suggestion chip
    if st.session_state.get("jarvis_prompt_input"):
        prompt = st.session_state.pop("jarvis_prompt_input")
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.jarvis_history.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            with st.spinner("Executing..."):
                try:
                    from dashboard.jarvis_brain import run_jarvis_chat
                    reply = run_jarvis_chat(st.session_state.jarvis_history)
                    st.markdown(reply)
                    st.session_state.jarvis_history.append({"role": "assistant", "content": reply})
                except Exception as e:
                    st.error(f"Command failed: {e}")
        st.rerun()
    else:
        with st.form(key="jarvis_inline_cmd_form", clear_on_submit=True):
            cmd_l, cmd_r = st.columns([4.8, 1.2])
            with cmd_l:
                user_typed_prompt = st.text_input(
                    "Command Input",
                    placeholder="Ask JARVIS anything (e.g., PnL since July 29, open positions, bot logs)...",
                    label_visibility="collapsed",
                    key="jarvis_form_input_val",
                )
            with cmd_r:
                submitted = st.form_submit_button("⚡ EXECUTE", use_container_width=True)

        if submitted and user_typed_prompt and user_typed_prompt.strip():
            prompt = user_typed_prompt.strip()
            with st.chat_message("user"):
                st.markdown(prompt)
            st.session_state.jarvis_history.append({"role": "user", "content": prompt})

            with st.chat_message("assistant"):
                with st.spinner("Analyzing droplet database & live logs..."):
                    try:
                        from dashboard.jarvis_brain import run_jarvis_chat
                        reply = run_jarvis_chat(st.session_state.jarvis_history)
                        st.markdown(reply)
                        st.session_state.jarvis_history.append({"role": "assistant", "content": reply})
                    except Exception as e:
                        st.error(f"Command failed: {e}")
            st.rerun()
    # Pin viewport scroll to top of Jarvis console to prevent auto-scrolling away
    scroll_align_html = """
    <script>
        var targetEl = window.parent.document.querySelector('.jarvis-orb-anchor') || window.parent.document.querySelector('.jarvis-chat-bubble');
        if (targetEl) {
            targetEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    </script>
    """
    components.html(scroll_align_html, height=0)

    st.markdown('</div>', unsafe_allow_html=True)
# ═══════════════════════════════════════════════════════════════════
# DORMANT PANEL — only render when Jarvis console is closed
# ═══════════════════════════════════════════════════════════════════
if not jarvis_open:

    if not release_status.get("entries_allowed"):
        blockers = release_status.get("top_infrastructure_blockers") or []
        blocker_text = blockers[0] if blockers else "release audit not yet promoted"
        _render_html(
            f"""
            <div class="banner">
              <strong>Fresh entries are paused by the release gate.</strong>
              The runtime is still live for monitoring and exits, but new trades stay blocked until the production blockers clear.
              Current blocker: {html.escape(str(blocker_text))}.
            </div>
            """,
        )

    if drift.get("has_drift"):
        drift_details = []
        if drift.get("broker_only"):
            for p in drift["broker_only"]:
                drift_details.append(f"<li>Broker-Only Position: <code>{html.escape(str(p.get('ticker')))}</code> ({html.escape(str(p.get('side')))}) &mdash; Qty: {p.get('qty')}, Entry: ${p.get('entry_price', 0.0):.2f}</li>")
        if drift.get("db_only"):
            for p in drift["db_only"]:
                drift_details.append(f"<li>DB-Only Remnant: <code>{html.escape(str(p.get('ticker')))}</code> ({html.escape(str(p.get('side')))}) &mdash; Qty: {p.get('qty')}, Entry: ${p.get('entry_price', 0.0):.2f}</li>")
        if drift.get("qty_mismatches"):
            for p in drift["qty_mismatches"]:
                drift_details.append(f"<li>Quantity Mismatch: <code>{html.escape(str(p.get('ticker')))}</code> ({html.escape(str(p.get('side')))}) &mdash; Broker has <b>{p.get('broker_qty')}</b>, DB has <b>{p.get('db_qty')}</b></li>")
        if drift.get("entry_mismatches"):
            for p in drift["entry_mismatches"]:
                drift_details.append(f"<li>Entry Price Mismatch: <code>{html.escape(str(p.get('ticker')))}</code> ({html.escape(str(p.get('side')))}) &mdash; Broker: ${p.get('broker_entry_price', 0.0):.2f}, DB: ${p.get('db_entry_price', 0.0):.2f}</li>")

        details_html = f"<ul style='margin-top: 5px; margin-bottom: 0px;'>{''.join(drift_details)}</ul>" if drift_details else ""
        _render_html(
            f"""
            <div class="banner">
              <strong>Truth drift detected.</strong> Broker reality and SQLite do not fully agree right now.
              The cockpit is showing both layers explicitly so you can see whether the issue is a stale local
              ledger, a manual broker action, or a runtime reconciliation lag.
              <div style="margin-top: 10px; font-size: 0.9em; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 8px;">
                <strong>Mismatched Positions:</strong>
                {details_html}
              </div>
            </div>
            """,
        )

        st.write("🔧 **Drift Intervention Controls**")
        for mismatch in (drift.get("qty_mismatches") or []):
            ticker = mismatch["ticker"]
            side = mismatch["side"]
            broker_qty = float(mismatch["broker_qty"] or 0.0)
            col1, col2 = st.columns([3, 1])
            col1.write(f"Flatten mismatched **{ticker}** ({side}) &mdash; Broker has {broker_qty} open contracts.")
            if col2.button(f"Flatten {ticker[:15]}...", key=f"flat_{ticker}", use_container_width=True):
                with st.spinner(f"Flattening {ticker}..."):
                    try:
                        from execution.kalshi_broker import get_kalshi_broker
                        from forecast.db import mark_forecast_position_closed
                        broker = get_kalshi_broker()
                        broker.connect()
                        right = "C" if side == "YES" else "P"
                        broker.flatten_position(ticker, right, int(round(broker_qty)))
                        mark_forecast_position_closed(ticker, exit_type="manual_reconcile")
                        st.success(f"Position {ticker} successfully flattened!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to flatten: {e}")

    # ── 7-Day Paper Lane Audit Countdown Timer ───────────────────────────
    countdown_data = payload.get("paper_trial_countdown") or {}
    cd_fmt = countdown_data.get("formatted", "7d 00h 00m 00s")

    _render_html(
        f"""
        <div style="background: linear-gradient(135deg, rgba(74, 242, 214, 0.12), rgba(0, 229, 255, 0.05)); border: 1px solid rgba(74, 242, 214, 0.3); border-radius: 16px; padding: 16px 20px; margin: 16px 0; display: flex; align-items: center; justify-content: space-between;">
            <div>
                <div style="font-size: 0.8em; color: #4af2d6; text-transform: uppercase; letter-spacing: 1px; font-weight: bold;">⏳ 7-DAY PAPER LANE AUDIT COUNTDOWN</div>
                <div style="font-size: 1.3em; font-weight: bold; color: #ffffff; margin-top: 4px;">Target Completion: August 16, 2026</div>
                <div style="font-size: 0.85em; color: #8899a6; margin-top: 2px;">Paper Lane A (Physics Delta) vs Paper Lane B (10X Maker) vs Live Lane</div>
            </div>
            <div style="text-align: right;">
                <div style="font-family: 'Orbitron', monospace; font-size: 1.8em; font-weight: bold; color: #00e5ff; text-shadow: 0 0 10px rgba(0,229,255,0.4);">{html.escape(cd_fmt)}</div>
                <div style="font-size: 0.75em; color: #4af2d6; margin-top: 2px;">TIME REMAINING</div>
            </div>
        </div>
        """
    )

    # ── Full-width Trading Lane Matrix ───────────────────────────────────────
    st.markdown('<div class="section-title" style="margin-top:16px;">⚡ Trading Lane Matrix</div>', unsafe_allow_html=True)

    lane_tab1, lane_tab2, lane_tab3 = st.tabs([
        f"🟢 Live Trading ({len(open_book_visual)} open)",
        f"🧪 Paper Lane A — Physics ({len(positions_paper_a)} active | {len(history_paper_a)} total)",
        f"🚀 Paper Lane B — 10X Maker ({len(positions_paper_b)} active | {len(history_paper_b)} total)",
    ])

    with lane_tab1:
        rows = open_book_visual
        if rows:
            pos_df = pd.DataFrame(rows)
            pos_df["gross_mark_pnl"] = pd.to_numeric(pos_df["gross_mark_pnl"], errors="coerce").fillna(0.0)
            pos_df["exposure_usd"] = pd.to_numeric(pos_df["exposure_usd"], errors="coerce").fillna(0.0)
            pos_df["hours_to_resolution"] = pd.to_numeric(pos_df["hours_to_resolution"], errors="coerce").fillna(0.0)

            m1, m2, m3, m4 = st.columns(4)
            total_exp = float(open_book_summary.get("total_exposure_usd") or 0.0)
            total_pnl = pos_df["gross_mark_pnl"].sum()
            m1.metric("Open Positions", len(rows))
            m2.metric("Total Exposure", f"${total_exp:,.2f}")
            m3.metric("Mark PnL", f"${total_pnl:+.2f}", delta=f"{'↑' if total_pnl >= 0 else '↓'} unrealized")
            m4.metric("Soonest Expiry", f"{pos_df['hours_to_resolution'].min():.1f}h" if len(pos_df) > 0 else "—")

            st.dataframe(
                pos_df[["ticker", "side", "qty", "entry_price", "mark", "gross_mark_pnl", "hours_to_resolution", "hub"]].rename(columns={
                    "gross_mark_pnl": "mark_pnl",
                    "hours_to_resolution": "hrs_left",
                }),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("No live Kalshi positions are open right now.")

    with lane_tab2:
        if positions_paper_a:
            pa_m1, pa_m2, pa_m3, pa_m4 = st.columns(4)
            tot_exp_a = sum(float(p.get("market_exposure_dollars") or 0.0) for p in positions_paper_a)
            closed_a = [p for p in history_paper_a if p.get("exit_type") not in (None, "ACTIVE", "")]
            wins_a = sum(1 for p in closed_a if p.get("exit_type") == "resolved")
            pa_m1.metric("Active Positions", len(positions_paper_a))
            pa_m2.metric("Capital Deployed", f"${tot_exp_a:,.2f}")
            pa_m3.metric("Total Trades", len(history_paper_a))
            pa_m4.metric("Resolved Wins", wins_a)

            df_pa = pd.DataFrame(positions_paper_a)
            st.dataframe(
                df_pa[["ticker", "side", "qty", "entry_price", "market_exposure_dollars", "opened_at"]].rename(columns={
                    "market_exposure_dollars": "exposure_$",
                    "opened_at": "opened",
                }),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("🧪 Paper Lane A has no active positions. The runner fires nightly — check back after the next forecast cycle.")

        if history_paper_a:
            with st.expander(f"📜 Full Trade Ledger — Lane A ({len(history_paper_a)} trades)", expanded=False):
                df_ha = pd.DataFrame(history_paper_a)
                st.dataframe(
                    df_ha[["ticker", "side", "qty", "entry_price", "opened_at", "closed_at", "exit_type"]].rename(columns={
                        "entry_price": "entry_¢",
                        "opened_at": "opened",
                        "closed_at": "closed",
                        "exit_type": "outcome",
                    }),
                    use_container_width=True, hide_index=True
                )

    with lane_tab3:
        if positions_paper_b:
            pb_m1, pb_m2, pb_m3, pb_m4 = st.columns(4)
            tot_exp_b = sum(float(p.get("market_exposure_dollars") or 0.0) for p in positions_paper_b)
            closed_b = [p for p in history_paper_b if p.get("exit_type") not in (None, "ACTIVE", "")]
            wins_b = sum(1 for p in closed_b if p.get("exit_type") == "resolved")
            pb_m1.metric("Active Positions", len(positions_paper_b))
            pb_m2.metric("Capital Deployed", f"${tot_exp_b:,.2f}")
            pb_m3.metric("Total Trades", len(history_paper_b))
            pb_m4.metric("Resolved Wins", wins_b)

            df_pb = pd.DataFrame(positions_paper_b)
            st.dataframe(
                df_pb[["ticker", "side", "qty", "entry_price", "market_exposure_dollars", "opened_at"]].rename(columns={
                    "market_exposure_dollars": "exposure_$",
                    "opened_at": "opened",
                }),
                use_container_width=True, hide_index=True
            )
        else:
            st.info("🚀 Paper Lane B has no active positions. The maker bid runner fires nightly — check back after the next forecast cycle.")

        if history_paper_b:
            with st.expander(f"📜 Full Trade Ledger — Lane B ({len(history_paper_b)} trades)", expanded=False):
                df_hb = pd.DataFrame(history_paper_b)
                st.dataframe(
                    df_hb[["ticker", "side", "qty", "entry_price", "opened_at", "closed_at", "exit_type"]].rename(columns={
                        "entry_price": "entry_¢",
                        "opened_at": "opened",
                        "closed_at": "closed",
                        "exit_type": "outcome",
                    }),
                    use_container_width=True, hide_index=True
                )

    # Collapsible Advanced System Telemetry Matrix
    st.markdown('<div class="section-title">Risk Matrix & Controls</div>', unsafe_allow_html=True)
    _render_html(
        '<div class="mini-grid">' + "".join(
            _mini_card(card["label"], card["value"], card["detail"], card.get("tooltip"))
            for card in regime_cards
        ) + "</div>",
    )

    st.markdown('<div class="section-title">Runtime System Integrity</div>', unsafe_allow_html=True)
    _render_html(
        '<div class="mini-grid">'
        + _mini_card("Disk Free", f"{round(float(storage['free_mb']), 0):,.0f} MB", "server headroom")
        + _mini_card("DB Footprint", f"{storage['db_mb']} MB", "local SQLite ledger")
        + _mini_card("Quote Cache", f"{market_counts['quote_rows']:,}", "forecast quote rows")
        + "</div>",
    )

    # Collapsible Advanced System Telemetry Matrix (cutting clutter)
    with st.expander("🔍 View Advanced Telemetry, Veto Tape &amp; System Feed"):
        t_col1, t_col2 = st.columns(2)
        with t_col1:
            st.markdown("##### 🛡️ Regional Hub Allocations")
            hub_df = pd.DataFrame(payload["hub_exposure"])
            if not hub_df.empty:
                st.dataframe(hub_df, width="stretch", hide_index=True)
            else:
                st.info("No active hub exposure.")
        with t_col2:
            st.markdown("##### 🚫 Veto Cluster Tape")
            if recent_vetoes.get("top_reasons"):
                veto_df = pd.DataFrame(recent_vetoes["top_reasons"])
                st.dataframe(veto_df, width="stretch", hide_index=True)
            else:
                st.success("No recent hard veto clusters.")

    st.markdown("### Event Tape")
    show_raw_events = st.toggle(
        "Show Raw Event Tape",
        value=False,
        help="By default the cockpit translates telemetry into plain-English insights. Turn this on to inspect the underlying raw system events.",
    )
    if show_raw_events:
        evt_left, evt_right = st.columns(2, gap="large")
        with evt_left:
            st.markdown('<div class="section-title">System Events</div>', unsafe_allow_html=True)
            if recent_events:
                for event in recent_events[:12]:
                    tone = "tone-bad" if event.get("level") in {"ERROR", "CRITICAL"} else "tone-amber" if event.get("level") == "WARNING" else "tone-cyan"
                    st.markdown(
                        _feed_card(
                            f"{event.get('source')} [{event.get('level')}]",
                            _fmt_dt(event.get("ts")),
                            str(event.get("message") or ""),
                            tone=tone,
                        ),
                        unsafe_allow_html=True,
                    )
            else:
                st.info("No recent system events.")

        with evt_right:
            st.markdown('<div class="section-title">Recent Trade Rows</div>', unsafe_allow_html=True)
            if recent_trades:
                trades_df = pd.DataFrame(recent_trades)
                trades_df["ts"] = trades_df["ts"].map(_fmt_dt)
                st.dataframe(
                    trades_df[
                        [
                            "ts",
                            "symbol",
                            "action",
                            "qty",
                            "price",
                            "fee_usd",
                            "pnl_usd",
                            "strategy",
                            "contract_side",
                            "forecast_yes_prob",
                        ]
                    ],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("No recent Kalshi trades found.")
    else:
        _render_html(
            """
            <div class="toggle-shell">
              Raw telemetry is hidden right now. The cockpit is showing translated insights by default so you can read what the system means, not just what it logged.
            </div>
            """
        )

st.divider()
st.caption(
    f"Generated {_fmt_dt(payload['generated_at'])} | "
    f"Lane updated {_fmt_dt(lane.get('updated_at'))} | "
    f"Deployed SHA {str(deploy.get('sha') or 'unknown')[:12]} | "
    f"Broker sync {'ON' if live_sync else 'OFF'}"
)
