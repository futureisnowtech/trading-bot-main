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

from dashboard import panels
from dashboard.cockpit_data import get_cockpit_payload
from config import DB_PATH, get_kalshi_hub_exposure_cap

st.set_page_config(
    page_title="Sovereign Kalshi Cockpit",
    page_icon="🌪",
    layout="wide",
    initial_sidebar_state="collapsed",
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


_fmt_dt = panels.fmt_dt


@st.cache_data(ttl=45, show_spinner=False)
def _load_payload(live_sync: bool) -> dict:
    return get_cockpit_payload(live_sync=live_sync)


_render_html = panels.render_html


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


_mini_card = panels.mini_card


_feed_card = panels.feed_card


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

# Orb-only surface: hide every piece of Streamlit chrome so nothing frames the orb.
_render_html("""
<style>
  #MainMenu, header, footer, [data-testid="stToolbar"],
  [data-testid="stDecoration"], [data-testid="stStatusWidget"],
  [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display: none !important; }
  .stApp { background: radial-gradient(ellipse at 50% 30%, #0a1424 0%, #03060d 60%, #010307 100%); }
  .block-container { padding: 1.2rem 1rem 2rem 1rem !important; max-width: 1100px; }
</style>
""")

# Broker sync is always on. The sidebar that used to toggle it is gone: this is an
# orb-only surface, and a 45s-cached read is cheap enough not to need a switch.
live_sync = True

payload = _load_payload(live_sync)
truth = payload["truth"]
release_status = payload["release_status"]
lane = truth.get("forecast_lane") or {}
regime = payload["regime"]
deploy = payload["deploy"]
positions_live = payload["positions_live"]
positions_db_only = payload["positions_db_only"]
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
cerebro = payload.get("cerebro") or {}
rbi2 = payload.get("rbi2") or {}
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
    # ── Vitals rendered into the orb itself ──────────────────────────
    # No cards, no tables. The orb encodes system state: ring arc = 7-day win
    # rate, core hue = 48h PnL sign, halo pulse = open position count, center
    # readout = equity. Health is legible without tapping, which is what keeps an
    # orb-only screen honest rather than merely dramatic.
    _wr = max(0.0, min(100.0, float(win_rate_7d)))
    _ring_deg = _wr * 3.6
    _healthy = pnl_48h >= 0
    _core = "#00ff88" if _healthy else "#ff5470"
    _core_soft = "rgba(0,255,136,0.35)" if _healthy else "rgba(255,84,112,0.35)"
    try:
        from runtime import approvals as _approvals

        _pending_approvals = len(_approvals.list_pending())
    except Exception:
        _pending_approvals = 0
    _alert = (not release_status.get("entries_allowed")) or bool(drift.get("has_drift")) or _pending_approvals > 0
    _ring_track = "rgba(255,213,79,0.55)" if _alert else "rgba(0,229,255,0.18)"
    # More open positions -> faster pulse, floored so an idle book still breathes.
    _pulse_s = max(1.6, 4.0 - 0.25 * float(positions_count))
    import math as _math
    _signal_nodes = []
    _node_colors = {"ACTIVE": "#ffd54f", "CONFIRMED": "#00ff88", "FALSIFIED": "#ff5470", "INCONCLUSIVE": "#8aa4b8"}
    for _idx, _insight in enumerate((cerebro.get("latest_insights") or [])[:6]):
        _angle = (_idx * 60 - 90) * _math.pi / 180.0
        _radius = 146 if _idx % 2 == 0 else 126
        _x = 170 + _math.cos(_angle) * _radius
        _y = 170 + _math.sin(_angle) * _radius
        _state = str(_insight.get("status") or "ACTIVE").upper()
        _color = _node_colors.get(_state, "#00e5ff")
        _title = html.escape(str(_insight.get("title") or "Cerebro signal"))
        _confidence = float(_insight.get("confidence") or 0.0)
        _signal_nodes.append(
            f'<div class="signal-node" title="{_title}" style="left:{_x:.0f}px;top:{_y:.0f}px;'
            f'--signal:{_color};--tempo:{max(1.2, 3.4 - 2.0 * _confidence):.2f}s"></div>'
        )
    _nodes_html = "".join(_signal_nodes)
    _active_count = int((cerebro.get("insight_counts") or {}).get("ACTIVE", 0) or 0)
    _artifact = str(((rbi2.get("champion") or {}).get("artifact_id") or "baseline"))

    orb_html = f"""
    <div class="orb-stage">
      <div class="orb">
        {_nodes_html}
        <!-- rotating outer lattice -->
        <div class="ring lattice"></div>
        <!-- win-rate arc: filled portion of the ring is the 7d win rate -->
        <div class="ring arc"></div>
        <!-- counter-rotating tick bezel -->
        <div class="ring bezel"></div>
        <!-- radar sweep -->
        <div class="sweep"></div>
        <!-- glass shell + reactor core -->
        <div class="shell"></div>
        <div class="core"></div>
        <div class="readout">
          <div class="cap">Equity</div>
          <div class="eq">${total_equity:,.2f}</div>
          <div class="delta">{pnl_48h:+.2f}<span class="dim"> 48h</span></div>
          <div class="rule"></div>
          <div class="stats">
            <span class="hot">{_wr:.0f}%</span><span class="dim"> win</span>
            <span class="sep">·</span>
            <span class="hot">{positions_count}</span><span class="dim"> open</span>
          </div>
          <div class="status">{'&#9888; ATTENTION' if _alert else 'NOMINAL'}</div>
          <div class="cerebro-state">CEREBRO {_active_count} · {html.escape(_artifact[:18])}</div>
        </div>
      </div>
      <div class="prov">{html.escape(str(deploy.get('sha') or 'unknown')[:7])} &middot; {html.escape(_fmt_dt(payload['generated_at']))}</div>
    </div>

    <style>
      .orb-stage {{ display:flex; flex-direction:column; align-items:center; justify-content:center;
                    font-family:'IBM Plex Mono',ui-monospace,monospace; }}
      .orb {{ position:relative; width:340px; height:340px; }}
      .ring {{ position:absolute; border-radius:50%; }}
      .signal-node {{ position:absolute; width:10px; height:10px; border-radius:50%; z-index:8;
        transform:translate(-50%,-50%); background:var(--signal); border:1px solid #dff;
        box-shadow:0 0 7px var(--signal),0 0 18px var(--signal); animation:signal var(--tempo) ease-in-out infinite; }}
      .signal-node::after {{ content:''; position:absolute; inset:-6px; border:1px solid var(--signal);
        border-radius:50%; opacity:.45; }}

      .lattice {{ inset:0;
        background:
          repeating-conic-gradient(from 0deg, rgba(0,229,255,0.22) 0deg 1.2deg, rgba(0,0,0,0) 1.2deg 9deg);
        -webkit-mask:radial-gradient(circle, transparent 61%, #000 62%, #000 70%, transparent 71%);
                mask:radial-gradient(circle, transparent 61%, #000 62%, #000 70%, transparent 71%);
        animation:spin 26s linear infinite; }}

      .arc {{ inset:26px;
        background:conic-gradient(from -90deg, {_core} 0deg, {_core} {_ring_deg}deg, {_ring_track} {_ring_deg}deg 360deg);
        -webkit-mask:radial-gradient(circle, transparent 70%, #000 71%);
                mask:radial-gradient(circle, transparent 70%, #000 71%);
        filter:drop-shadow(0 0 16px {_core_soft}); }}

      .bezel {{ inset:44px;
        background:repeating-conic-gradient(from 0deg, rgba(74,242,214,0.5) 0deg 0.5deg, rgba(0,0,0,0) 0.5deg 6deg);
        -webkit-mask:radial-gradient(circle, transparent 84%, #000 85%);
                mask:radial-gradient(circle, transparent 84%, #000 85%);
        animation:spin 14s linear infinite reverse; }}

      .sweep {{ position:absolute; inset:52px; border-radius:50%;
        background:conic-gradient(from 0deg, rgba(0,229,255,0.30), rgba(0,229,255,0) 55deg);
        animation:spin 3.6s linear infinite; }}

      .shell {{ position:absolute; inset:56px; border-radius:50%;
        background:radial-gradient(circle at 50% 34%, rgba(14,28,50,0.96), rgba(2,5,12,0.99) 72%);
        border:1px solid rgba(0,229,255,0.28);
        box-shadow:inset 0 0 46px rgba(0,229,255,0.10), 0 0 60px rgba(0,229,255,0.10); }}

      .core {{ position:absolute; inset:96px; border-radius:50%;
        background:radial-gradient(circle at 50% 45%, {_core_soft}, rgba(0,0,0,0) 70%);
        animation:pulse {_pulse_s}s ease-in-out infinite; }}

      .readout {{ position:absolute; inset:0; display:flex; flex-direction:column;
                  align-items:center; justify-content:center; text-align:center; }}
      .cap    {{ font-size:9px; letter-spacing:3.4px; color:#4af2d6; text-transform:uppercase; }}
      .eq     {{ font-family:'Orbitron',monospace; font-size:31px; font-weight:800; color:#fff;
                 line-height:1.15; text-shadow:0 0 22px {_core_soft}; }}
      .delta  {{ font-size:12px; color:{_core}; margin-top:1px; }}
      .rule   {{ width:104px; height:1px; margin:9px 0;
                 background:linear-gradient(90deg, transparent, rgba(0,229,255,0.5), transparent); }}
      .stats  {{ font-size:11.5px; letter-spacing:0.4px; }}
      .hot    {{ color:#00e5ff; font-weight:600; }}
      .dim    {{ color:#61788c; }}
      .sep    {{ color:#2f4657; margin:0 6px; }}
      .status {{ margin-top:8px; font-size:9px; letter-spacing:2.2px;
                 color:{'#ffd54f' if _alert else '#3d5768'}; }}
      .cerebro-state {{ margin-top:5px; max-width:170px; white-space:nowrap; overflow:hidden;
        text-overflow:ellipsis; color:#466b7d; font-size:7px; letter-spacing:1px; }}
      .prov   {{ margin-top:6px; font-size:8.5px; letter-spacing:1.4px; color:#24384a; }}

      @keyframes spin  {{ to {{ transform:rotate(360deg); }} }}
      @keyframes pulse {{ 0%,100% {{ opacity:.40; transform:scale(.95); }}
                          50%     {{ opacity:.95; transform:scale(1.05); }} }}
      @keyframes signal {{ 0%,100% {{ opacity:.45; transform:translate(-50%,-50%) scale(.75); }}
                           50% {{ opacity:1; transform:translate(-50%,-50%) scale(1.35); }} }}
    </style>
    """
    components.html(orb_html, height=384)

    # Nothing else renders in the dormant state. The orb is the whole surface; the
    # prompt shortcuts that used to sit under it live inside the console, one tap away
    # instead of permanently on screen.

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

    # Suggestion chips. Each points at one specific, verified tool by name rather than
    # a freeform multi-step prompt -- the prior versions invited the model to
    # improvise SQL or reasoned over a hardcoded, long-expired contract
    # (KXLOWTMIA-26AUG01-T80), which is exactly the kind of arbitrary preload that
    # tells you nothing about current system health.
    sug_cols = st.columns(5)
    labels = [
        "🧭 What Needs Attention?",
        "🛑 Why Isn't It Trading?",
        "💸 Are Fees Hurting Us?",
        "🎯 Are Entries Working?",
        "📂 What Bets Are Live?",
    ]
    prompts_map = {
        "🧭 What Needs Attention?": "Call get_operator_brief. In plain English, tell me whether the bot is healthy, whether it can trade, and the one thing I should pay attention to right now.",
        "🛑 Why Isn't It Trading?": "Call get_trading_readiness_summary. Explain in plain English whether the bot is allowed to place new trades, and if not, exactly what is stopping it.",
        "💸 Are Fees Hurting Us?": "Call get_fee_drag. Explain in plain English how much of our trading edge fees are eating and whether that is materially hurting us.",
        "🎯 Are Entries Working?": "Call get_maker_fill_stats. Explain in plain English whether our entry approach is getting good fills or forcing us to overpay.",
        "📂 What Bets Are Live?": "Call get_open_positions. List our live weather bets in plain English, including side, size, and entry price.",
    }
    for col, label in zip(sug_cols, labels):
        if col.button(label, use_container_width=True, key=f"sug_{label}"):
            st.session_state.jarvis_prompt_input = prompts_map[label]
            st.session_state.show_jarvis = True
            st.rerun()

    with st.expander(f"CEREBRO SIGNAL ARCHIVE · {sum((cerebro.get('insight_counts') or {}).values())}"):
        _experiment_by_insight = {
            str(_experiment.get("insight_id") or ""): _experiment
            for _experiment in (cerebro.get("latest_experiments") or [])
        }
        _pending_cerebro_msgs = st.session_state.pop("cerebro_queue_message", "")
        if _pending_cerebro_msgs:
            st.info(_pending_cerebro_msgs)
        _archive = cerebro.get("latest_insights") or []
        if not _archive:
            st.caption("Cerebro is collecting point-in-time evidence. Insights appear only when a falsifiable pattern clears the evidence floor.")
        for _insight in _archive:
            _insight_id = str(_insight.get("insight_id") or "")
            _state = str(_insight.get("status") or "ACTIVE")
            st.markdown(f"**{_state} · {float(_insight.get('confidence') or 0):.0%}**  {_insight.get('title', '')}")
            st.caption(str(_insight.get("summary") or ""))
            st.caption(f"Test: {_insight.get('falsification_rule', '')}")
            _existing = _experiment_by_insight.get(_insight_id)
            if _existing:
                st.caption(
                    f"Experiment: {_existing.get('status', 'UNKNOWN')} · "
                    f"{(_existing.get('change_spec') or {}).get('proposal_type', 'manual_review')}"
                )
            elif _state in {"ACTIVE", "CONFIRMED"} and _insight_id:
                if st.button("Queue Shadow Experiment", key=f"cerebro_queue_{_insight_id}", use_container_width=False):
                    from runtime import approvals as _approvals

                    st.session_state.cerebro_queue_message = _approvals.request_change(
                        "create_cerebro_experiment",
                        {"insight_id": _insight_id},
                        f"Queued from cockpit archive for insight {_insight_id}.",
                        surface="cockpit",
                        dedupe_pending=True,
                    )
                    st.rerun()
        _experiments = cerebro.get("latest_experiments") or []
        if _experiments:
            st.markdown("**Shadow Experiments**")
            for _experiment in _experiments[:6]:
                _kind = str((_experiment.get("change_spec") or {}).get("proposal_type") or "manual_review")
                st.caption(
                    f"{_experiment.get('status', 'UNKNOWN')} · {_experiment.get('experiment_id', '')} · "
                    f"{_kind} · insight {_experiment.get('insight_id', '')}"
                )
        _runs = cerebro.get("latest_runs") or []
        if _runs:
            _latest_run = _runs[0]
            st.caption(
                f"Latest intelligence cycle: {_latest_run.get('status', 'UNKNOWN')} · "
                f"{_latest_run.get('started_at', '')}"
            )

    # Chat history
    if "jarvis_history" not in st.session_state:
        st.session_state.jarvis_history = [
            {"role": "assistant", "content": "Jarvis is live. Ask what the bot is doing, why it is or is not trading, what bets are open, or whether anything needs your attention."}
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
                    placeholder="Ask in plain English (e.g., Why isn't it trading? What bets are live? Does anything need me?)",
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
# ── Summoned panels ─────────────────────────────────────────────────
# The permanent dashboard is gone. JARVIS calls show_panel(name) and the panel
# renders here until dismissed, so nothing is on screen that was not asked for.
_panel_ctx = {
    "payload": payload,
    "release_status": release_status,
    "drift": drift,
    "open_book_visual": open_book_visual,
    "open_book_summary": open_book_summary,
    "regime_cards": regime_cards,
    "storage": storage,
    "market_counts": market_counts,
    "recent_vetoes": recent_vetoes,
    "recent_events": recent_events,
    "recent_trades": recent_trades,
}

_active = st.session_state.get("active_panels") or []
if _active:
    st.markdown('<div class="panel-deck">', unsafe_allow_html=True)
    for _name in list(_active):
        panels.render_panel(_name, _panel_ctx)
    if st.button("✕ Dismiss panels", key="dismiss_panels", use_container_width=True):
        st.session_state.active_panels = []
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
