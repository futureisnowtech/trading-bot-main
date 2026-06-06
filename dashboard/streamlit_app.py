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
from config import get_kalshi_hub_exposure_cap

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
  grid-template-columns: repeat(5, minmax(0, 1fr));
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
  grid-template-columns: repeat(4, minmax(0, 1fr));
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
    return f"""
    <div class="mini-card">
      <div class="mini-label">
        <span class="label-wrap">{html.escape(label)}{_tooltip_dot(tooltip)}</span>
      </div>
      <div class="mini-value">{html.escape(value)}</div>
      <div class="mini-detail">{html.escape(detail)}</div>
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

    chart = (
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

    zero_rule = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        color="rgba(255,255,255,0.24)",
        strokeDash=[6, 4],
    ).encode(y="y:Q")
    st.altair_chart((chart + zero_rule), width="stretch")


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
drift = truth.get("position_drift") or {}
positions_count = len(positions_live)
realized_curve = payload["realized_pnl_curve"]
realized_pnl = realized_curve[-1]["cumulative_pnl"] if realized_curve else 0.0
hub_cap_now = get_kalshi_hub_exposure_cap(balance)

_render_html(
    f"""
    <div class="hero">
      <div class="eyebrow">Sovereign Weather Engine</div>
      <div class="hero-title">Kalshi Cockpit</div>
      <div class="hero-sub">
        Broker-first command HUD for live weather trading. This board fuses Kalshi balance and positions,
        local ledger drift, recent veto reasons, notifications, regime math, and deployment provenance into
        one read-only cockpit.
      </div>
      <div class="chip-row">
        <div class="chip">Version {html.escape(str(regime['version']))}</div>
        <div class="chip">Lane {html.escape(str(lane.get('readiness_state') or 'UNKNOWN'))}</div>
        <div class="chip">Health {html.escape(str(lane.get('health') or 'UNKNOWN'))}</div>
        <div class="chip">Release {html.escape(str(release_status.get('current_release_verdict') or 'UNKNOWN'))}</div>
        <div class="chip">Broker {'CONNECTED' if truth.get('broker_connected') else 'DISCONNECTED'}</div>
        <div class="chip">Model {html.escape(str(regime['reasoning_model']))}</div>
        <div class="chip">Deploy {html.escape(str(deploy.get('sha') or 'local'))[:7]}</div>
      </div>
    </div>
    """,
    )

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
    _render_html(
        """
        <div class="banner">
          <strong>Truth drift detected.</strong> Broker reality and SQLite do not fully agree right now.
          The cockpit is showing both layers explicitly so you can see whether the issue is a stale local
          ledger, a manual broker action, or a runtime reconciliation lag.
        </div>
        """,
    )

st.markdown("### Live Core")
metric_html = f"""
<div class="metric-grid">
  {_metric_card("Live Cash", _fmt_money(balance), "Broker-reported balance", "tone-mint", metric_explainers.get("Live Cash"))}
  {_metric_card("Open Positions", str(positions_count), "Broker-truth live positions", "tone-cyan", metric_explainers.get("Open Positions"))}
  {_metric_card("Active Markets", str(market_counts['active_markets']), f"{market_counts['active_contracts']} active contracts", "tone-blue", metric_explainers.get("Active Markets"))}
  {_metric_card("Release Gate", str(release_status.get('current_release_verdict') or 'UNKNOWN'), "fresh-entry permission state", "tone-mint" if release_status.get('entries_allowed') else "tone-amber", metric_explainers.get("Release Gate"))}
  {_metric_card("Drift", "YES" if drift.get('has_drift') else "NO", f"{len(positions_db_only)} db-only remnants", "tone-bad" if drift.get('has_drift') else "tone-mint", metric_explainers.get("Drift"))}
  {_metric_card("Realized P&L", _fmt_money(realized_pnl), "From Kalshi trade ledger", "tone-amber" if realized_pnl < 0 else "tone-mint", metric_explainers.get("Realized P&L"))}
</div>
"""
_render_html(metric_html)

st.markdown("### Decision Funnel")
_render_html('<div class="stage-grid">' + "".join(_funnel_stage_card(stage) for stage in decision_funnel) + "</div>")

top_left, top_right = st.columns([1.3, 1.0], gap="large")

with top_left:
    st.markdown('<div class="section-title">Open Book</div>', unsafe_allow_html=True)
    with st.container(border=False):
        rows = positions_live or positions_db_only
        if rows:
            _render_html(
                '<div class="mini-grid">'
                + _mini_card(
                    "Book Exposure",
                    _fmt_money(open_book_summary.get("total_exposure_usd")),
                    f"{open_book_summary.get('position_count')} live positions",
                    metric_explainers.get("Book Exposure"),
                )
                + _mini_card(
                    "Live Mark P&L",
                    _fmt_money(open_book_summary.get("total_mark_pnl_usd")),
                    "midpoint mark across the book",
                    metric_explainers.get("Live Mark P&L"),
                )
                + _mini_card(
                    "Emergency Exit P&L",
                    _fmt_money(open_book_summary.get("total_exit_pnl_est_usd")),
                    "flatten now at the live bid",
                    metric_explainers.get("Emergency Exit P&L"),
                )
                + "</div>"
            )
            _render_html(
                '<div class="mini-grid">'
                + _mini_card(
                    "Nearest Resolution",
                    str(open_book_summary.get("nearest_resolution_label") or "N/A"),
                    "soonest contract to settle",
                    metric_explainers.get("Nearest Resolution"),
                )
                + _mini_card(
                    "Dominant Hub",
                    str(open_book_summary.get("largest_hub") or "N/A"),
                    _fmt_money(open_book_summary.get("largest_hub_exposure_usd")),
                    metric_explainers.get("Regional Hub Cap"),
                )
                + _mini_card(
                    "Largest Line",
                    str(open_book_summary.get("largest_position_ticker") or "N/A"),
                    _fmt_money(open_book_summary.get("largest_position_exposure_usd")),
                    metric_explainers.get("Open Positions"),
                )
                + "</div>"
            )

            card_tab, heat_tab, expiry_tab, raw_tab = st.tabs(
                ["Position Cards", "Heat Map", "Expiry Pressure", "Raw Table"]
            )

            with card_tab:
                _render_open_book_cards(open_book_visual)

            with heat_tab:
                _render_open_book_heatmap(open_book_visual)
                st.caption(
                    "Heat cells show book weight plus mark-to-market and emergency-exit pressure as percentages of capital at risk."
                )

            with expiry_tab:
                _render_open_book_expiry_chart(open_book_visual)
                st.caption(
                    "Bigger circles mean more capital committed. Points below zero are positions that would likely lose money if flattened right now."
                )

            with raw_tab:
                pos_df = pd.DataFrame(rows)
                pos_df = pos_df[
                    [
                        "ticker",
                        "contract_name",
                        "side",
                        "qty",
                        "entry_price",
                        "bid",
                        "ask",
                        "mark",
                        "gross_mark_pnl",
                        "exit_pnl_est",
                        "hub",
                        "state_label",
                        "resolution_at",
                    ]
                ].rename(
                    columns={
                        "contract_name": "contract",
                        "entry_price": "entry",
                        "gross_mark_pnl": "mark_pnl",
                        "exit_pnl_est": "exit_pnl_est",
                        "resolution_at": "resolves",
                        "state_label": "state",
                    }
                )
                st.dataframe(pos_df, width="stretch", hide_index=True)
                st.caption("`mark_pnl` uses current side midpoint. `exit_pnl_est` is a bid-side liquidation estimate with exit fee and any recorded entry fee.")

            st.markdown('<div class="section-title">Trade-Type Boards</div>', unsafe_allow_html=True)
            _render_weather_type_boards(weather_type_boards, weather_type_counts)
        else:
            st.info("No live Kalshi positions are open right now.")
            st.markdown('<div class="section-title">Trade-Type Boards</div>', unsafe_allow_html=True)
            _render_weather_type_boards(weather_type_boards, weather_type_counts)

    st.markdown('<div class="section-title">Trade Curve</div>', unsafe_allow_html=True)
    if realized_curve:
        curve_df = pd.DataFrame(realized_curve)
        curve_df = curve_df.rename(columns={"ts": "time", "cumulative_pnl": "realized_pnl"})
        st.line_chart(curve_df.set_index("time"))
    else:
        st.info("No realized Kalshi P&L history is available yet.")

with top_right:
    st.markdown('<div class="section-title">Risk Controls</div>', unsafe_allow_html=True)
    _render_html(
        '<div class="mini-grid">' + "".join(
            _mini_card(card["label"], card["value"], card["detail"], card.get("tooltip"))
            for card in regime_cards
        ) + "</div>",
    )

    st.markdown('<div class="section-title">Risk Radar</div>', unsafe_allow_html=True)
    hub_df = pd.DataFrame(payload["hub_exposure"])
    if not hub_df.empty:
        st.dataframe(hub_df, width="stretch", hide_index=True)
        st.caption(f"Live hub cap right now: {_fmt_money(hub_cap_now)}")
    else:
        st.info("No active hub exposure.")

    st.markdown('<div class="section-title">Veto Tape</div>', unsafe_allow_html=True)
    if recent_vetoes.get("top_reasons"):
        veto_df = pd.DataFrame(recent_vetoes["top_reasons"])
        st.dataframe(veto_df, width="stretch", hide_index=True)
    else:
        st.success("No recent hard veto cluster in the current lookback window.")

    st.markdown('<div class="section-title">Runtime Integrity</div>', unsafe_allow_html=True)
    _render_html(
        '<div class="mini-grid">'
        + _mini_card("Disk Free", f"{round(float(storage['free_mb']), 0):,.0f} MB", "headroom before writes get risky")
        + _mini_card("DB Size", f"{storage['db_mb']} MB", "local SQLite footprint")
        + _mini_card("Quote Cache", f"{market_counts['quote_rows']:,}", "stored forecast quote rows")
        + "</div>",
    )

st.markdown("### Trade Edge Tracker")
st.caption(
    "Each bar compares what the model believed for the side it bought versus the market price it paid. "
    "Hover any bar to inspect the trade in plain percentages."
)
_render_trade_edge_chart(trade_edge_rows)
if trade_edge_rows:
    edge_table = pd.DataFrame(trade_edge_rows)[
        ["ts", "symbol", "side", "model_confidence_pct", "market_price_pct", "edge_pct", "strategy"]
    ].rename(
        columns={
            "model_confidence_pct": "model_conf_%",
            "market_price_pct": "paid_price_%",
            "edge_pct": "edge_%",
        }
    )
    st.dataframe(edge_table, width="stretch", hide_index=True)

insight_left, insight_right = st.columns([1.25, 0.95], gap="large")

with insight_left:
    st.markdown("### AI Insights")
    for insight in ai_insights:
        _render_html(
            _insight_card(
                insight.get("title", "Insight"),
                insight.get("meta", ""),
                insight.get("body", ""),
                tone=insight.get("tone", "info"),
            )
        )

with insight_right:
    st.markdown("### Operator Alerts")
    if notifications:
        for event in notifications[:12]:
            tone = "tone-bad" if event.get("severity") == "CRITICAL" else "tone-amber" if event.get("severity") == "WARNING" else "tone-blue"
            why = event.get("why") or {}
            why_bits = why.get("top_3_reasons") or []
            why_text = " | ".join(str(x) for x in why_bits[:3]) if why_bits else str(event.get("message") or "")
            ts_value = datetime.fromtimestamp(float(event.get("ts") or 0), tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            st.markdown(
                _feed_card(
                    f"{event.get('category')} :: {event.get('title')}",
                    ts_value,
                    why_text,
                    tone=tone,
                ),
                unsafe_allow_html=True,
            )
    else:
        st.info("No notification feed rows available.")

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
