"""
dashboard/widgets/pages/engineering_console.py — ENGINEERING CONSOLE page.

Premium design: truth summary strip, 6 subtabs (Safe Controls, Risk Rules,
Signal Logic, Scanner Rules, Raw Config, Event Log), each with a plain-English
intro so non-engineers can understand what they're looking at.
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

import ui
from data.engineering_console import get_engineering_truth_summary


def render_engineering_console():
    # ── Engineering truth strip ────────────────────────────────────────────────
    try:
        summary = get_engineering_truth_summary()

        version = summary.get("version", "unknown")
        proof = summary.get("proof_count")
        integrity = summary.get("integrity_summary") or {}
        verified = integrity.get("verified", 0)
        total_cl = integrity.get("total_closes", 0)
        runtime_ts = summary.get("runtime_truth_age")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(
                ui.summary_card(
                    "System Version",
                    version,
                    "Current",
                    "neutral",
                    "Active code version running on this bot instance",
                ),
                unsafe_allow_html=True,
            )
        with c2:
            proof_label = str(proof) if proof else "None"
            st.markdown(
                ui.summary_card(
                    "Proof Tests",
                    proof_label,
                    "Passing" if proof else "Not run",
                    "good" if proof and int(proof) > 400 else "watch",
                    "Automated test suite verifying system invariants — run `python3 -m pytest`",
                ),
                unsafe_allow_html=True,
            )
        with c3:
            close_label = f"{verified}/{total_cl}" if total_cl else "0/0"
            close_pct = verified / total_cl * 100 if total_cl else 0
            st.markdown(
                ui.summary_card(
                    "Verified Closes",
                    close_label,
                    f"{close_pct:.0f}% trusted",
                    "good" if close_pct >= 80 else "watch",
                    "Trades with integrity tier = 'verified' are used for Bayesian learning and ML training",
                ),
                unsafe_allow_html=True,
            )
        with c4:
            rt_label = runtime_ts[:16] if runtime_ts else "Unknown"
            st.markdown(
                ui.summary_card(
                    "Runtime State Since",
                    rt_label,
                    "Recorded",
                    "neutral",
                    "When the bot last wrote its runtime state to the database",
                ),
                unsafe_allow_html=True,
            )
    except Exception as e:
        st.caption(f"Engineering summary unavailable: {e}")

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Subtabs ────────────────────────────────────────────────────────────────
    (
        tab_controls,
        tab_risk,
        tab_signals,
        tab_scanner,
        tab_config,
        tab_events,
    ) = st.tabs(
        [
            "Safe Controls",
            "Risk Rules",
            "Signal Logic",
            "Scanner Rules",
            "Raw Config",
            "Event Log",
        ]
    )

    # ── TAB 1: Safe Controls ───────────────────────────────────────────────────
    with tab_controls:
        st.markdown(
            ui.info_callout(
                "The trading control plane — where trades are being blocked and why. "
                "This tab answers: is the bot trying to trade, and if not, what's in the way? "
                "All blocks here are protective, not errors.",
                "info",
            ),
            unsafe_allow_html=True,
        )
        try:
            from widgets.system_settings.master_control import render_master_control

            render_master_control()
        except Exception as e:
            st.caption(f"Master control unavailable: {e}")

    # ── TAB 2: Risk Rules ──────────────────────────────────────────────────────
    with tab_risk:
        st.markdown(
            ui.info_callout(
                "The guardrails that protect your account. These rules fire automatically — "
                "no manual action needed. The kill switch is the last line of defense.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        try:
            import config

            rk_left, rk_right = st.columns(2)

            with rk_left:
                body_risk = ""
                body_risk += ui.metric_row(
                    "Max risk per trade",
                    "1% of account",
                    ui.C_CYAN,
                )
                body_risk += ui.metric_row(
                    "Daily loss cap",
                    "4% → halt all trading (live)",
                )
                ks_pct = getattr(config, "KILL_SWITCH_PCT", 0.75)
                body_risk += ui.metric_row(
                    "Kill switch threshold",
                    f"{ks_pct * 100:.0f}% of account (paper)",
                )
                body_risk += ui.metric_row(
                    "Kill switch — live mode",
                    "50% of live baseline balance",
                )
                acct = getattr(config, "ACCOUNT_SIZE", 5000)
                body_risk += ui.metric_row(
                    "Paper account size (config)",
                    f"${acct:,.0f}",
                )
                body_risk += ui.metric_row(
                    "Max deployed capital",
                    "95% of account",
                )
                st.markdown(
                    ui.detail_card(
                        "Position & Account Risk",
                        "Hard limits applied on every entry",
                        body_risk,
                    ),
                    unsafe_allow_html=True,
                )

            with rk_right:
                body_lev = ""
                default_lev = getattr(config, "DEFAULT_LEVERAGE", 3)
                max_lev = getattr(config, "MAX_LEVERAGE", 10)
                body_lev += ui.metric_row("Default leverage", f"{default_lev}×")
                body_lev += ui.metric_row("Max leverage allowed", f"{max_lev}×")
                body_lev += ui.metric_row("Margin mode", "ISOLATED — never CROSS")
                body_lev += ui.metric_row("Max single position", "12% of account")
                max_perps = getattr(config, "MAX_LIVE_PERPS", 3)
                body_lev += ui.metric_row("Max concurrent live perps", str(max_perps))
                body_lev += ui.metric_row("Spot deployment cap", "40% of USD available")
                st.markdown(
                    ui.detail_card(
                        "Leverage & Position Limits",
                        "Size constraints applied by position_manager.py",
                        body_lev,
                    ),
                    unsafe_allow_html=True,
                )

            # Kill switch live status
            try:
                from db import _q1

                ks_row = _q1(
                    "SELECT ts, reason, balance, trigger_type FROM kill_switch_log "
                    "ORDER BY ts DESC LIMIT 1"
                )
                if ks_row:
                    body_ks = (
                        ui.metric_row("Last trigger", ks_row.get("ts", "")[:16])
                        + ui.metric_row("Reason", ks_row.get("reason", "—"))
                        + ui.metric_row(
                            "Balance at trigger", f"${ks_row.get('balance', 0):.2f}"
                        )
                        + ui.metric_row("Type", ks_row.get("trigger_type", "—"))
                    )
                    st.markdown(
                        ui.detail_card(
                            "Kill Switch History",
                            "Last time the kill switch fired",
                            body_ks,
                        ),
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        ui.info_callout(
                            "Kill switch has never fired — account is healthy.", "good"
                        ),
                        unsafe_allow_html=True,
                    )
            except Exception:
                pass

        except Exception as e:
            st.caption(f"Risk config unavailable: {e}")

    # ── TAB 3: Signal Logic ────────────────────────────────────────────────────
    with tab_signals:
        st.markdown(
            ui.info_callout(
                "How the bot decides whether a trade is worth taking. "
                "Two towers — a rule-based technical score and an ML model — are combined into "
                "a composite score. A trade only enters if that composite clears the regime threshold.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        try:
            import config

            sl_left, sl_right = st.columns(2)

            with sl_left:
                body_entry = ""
                body_entry += ui.metric_row(
                    "Composite threshold — TRENDING UP/DOWN",
                    f"{getattr(config, 'REGIME_THRESH_TRENDING', 58)}",
                )
                body_entry += ui.metric_row(
                    "Composite threshold — RANGING",
                    f"{getattr(config, 'REGIME_THRESH_RANGING', 58)}",
                )
                body_entry += ui.metric_row(
                    "Composite threshold — HIGH VOL",
                    f"{getattr(config, 'REGIME_THRESH_HIGH_VOL', 60)}",
                )
                body_entry += ui.metric_row(
                    "Composite threshold — LOW VOL",
                    f"{getattr(config, 'REGIME_THRESH_LOW_VOL', 56)}",
                )
                body_entry += ui.metric_row(
                    "Composite threshold — UNKNOWN",
                    f"{getattr(config, 'REGIME_THRESH_UNKNOWN', 58)}",
                )
                body_entry += ui.metric_row("Tier-2 floor", "55 composite")
                body_entry += ui.metric_row("Tier-1 floor", "45 composite")
                st.markdown(
                    ui.detail_card(
                        "Entry Thresholds",
                        "Minimum composite score required per market regime",
                        body_entry,
                    ),
                    unsafe_allow_html=True,
                )

            with sl_right:
                body_ml = ""
                body_ml += ui.metric_row(
                    "ML architecture", "XGBoost 60% + LightGBM 40%"
                )
                body_ml += ui.metric_row("ML task", "PnL regressor (not classifier)")
                body_ml += ui.metric_row(
                    "Score formula",
                    "50 + 50 × tanh(predicted_pnl / pnl_scale)",
                )
                body_ml += ui.metric_row(
                    "Feature count", "57 features across 11 groups"
                )
                body_ml += ui.metric_row(
                    "ML fallback", "50.0 when no model file exists"
                )
                body_ml += ui.metric_row(
                    "Technical tower weight", "Blended with ML tower"
                )
                body_ml += ui.metric_row(
                    "Bayesian signal updates", "After every closed trade"
                )
                st.markdown(
                    ui.detail_card(
                        "ML & Signal Engine",
                        "Two-tower architecture driving the composite score",
                        body_ml,
                    ),
                    unsafe_allow_html=True,
                )

            # Exit logic summary
            body_exit = ""
            body_exit += ui.metric_row(
                "1. Trailing stop",
                "Regime-aware: RANGING 2.5×ATR · TRENDING 4.5×ATR · HIGH_VOL 5.5×ATR",
            )
            body_exit += ui.metric_row(
                "2. Take-profit scale-out",
                "First cut 20–30% at 2–4R · second cut 25% at 4.5–8R",
            )
            body_exit += ui.metric_row(
                "3. Thesis score exit",
                "Composite < entry × regime fraction — exits stale thesis",
            )
            body_exit += ui.metric_row(
                "4. Hard stop", "Stop-market on exchange, never widened"
            )
            body_exit += ui.metric_row(
                "5. Risk forced exit", "Margin breach / drawdown / correlation"
            )
            body_exit += ui.metric_row(
                "6. Kill switch", "Balance threshold / API errors / latency"
            )
            st.markdown(
                ui.detail_card(
                    "6-Priority Exit Stack",
                    "Exits fire in this priority order — higher = tried first",
                    body_exit,
                ),
                unsafe_allow_html=True,
            )

        except Exception as e:
            st.caption(f"Signal logic config unavailable: {e}")

    # ── TAB 4: Scanner Rules ───────────────────────────────────────────────────
    with tab_scanner:
        st.markdown(
            ui.info_callout(
                "How the scanner finds candidates before the signal engine scores them. "
                "7 filters run in sequence — a pair must pass all of them to become a scored candidate. "
                "The economics gate then checks whether fees still leave a profit after the signal clears.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        try:
            import config

            sc_left, sc_right = st.columns(2)

            with sc_left:
                body_scan = ""
                body_scan += ui.metric_row(
                    "Sources",
                    "Kraken Futures · Binance USDM · Hyperliquid",
                )
                body_scan += ui.metric_row(
                    "Live execution universe",
                    "BTC · ETH · SOL · XRP (Coinbase nano perps)",
                )
                vol_floor = getattr(config, "MIN_VOLUME_24H", 2_500_000)
                body_scan += ui.metric_row(
                    "Volume floor (24h)",
                    f"${vol_floor / 1e6:.1f}M",
                )
                body_scan += ui.metric_row(
                    "Scanner EV cap",
                    "Position capped at $100 to prevent phantom high-EV from tiny stops",
                )
                body_scan += ui.metric_row(
                    "Candidates per cycle", "Top 50 after 7-step filter"
                )
                body_scan += ui.metric_row("Scan interval", "Every 3–5 minutes")
                st.markdown(
                    ui.detail_card(
                        "Scanner Configuration",
                        "How pairs are found and filtered before scoring",
                        body_scan,
                    ),
                    unsafe_allow_html=True,
                )

            with sc_right:
                body_econ = ""
                body_econ += ui.metric_row(
                    "Coinbase taker fee",
                    "0.030% per side (0.060% round-trip)",
                )
                body_econ += ui.metric_row("EV tier A+", "≥ 0.8% expected edge")
                body_econ += ui.metric_row("EV tier A", "≥ 0.4% expected edge")
                body_econ += ui.metric_row("EV tier B", "≥ 0.15% expected edge")
                spread_gate = getattr(config, "SPREAD_GATE_BPS", 25)
                body_econ += ui.metric_row("Spread gate", f"{spread_gate} bps max")
                depth_gate = getattr(config, "DEPTH_GATE_USD", 5000)
                body_econ += ui.metric_row(
                    "Order book depth gate", f"${depth_gate:,}/side minimum"
                )
                body_econ += ui.metric_row("Stop multiplier", "3.0× ATR for EV calc")
                st.markdown(
                    ui.detail_card(
                        "Economics Gate",
                        "Pre-trade fee/funding EV veto — runs after signal scores",
                        body_econ,
                    ),
                    unsafe_allow_html=True,
                )

        except Exception as e:
            st.caption(f"Scanner config unavailable: {e}")

    # ── TAB 5: Raw Config ──────────────────────────────────────────────────────
    with tab_config:
        st.markdown(
            ui.info_callout(
                "All configuration constants exactly as the running bot sees them. "
                "These come from config.py + .env overrides. Read-only — changes require "
                "editing config.py or .env and restarting.",
                "info",
            ),
            unsafe_allow_html=True,
        )
        try:
            from widgets.system_settings.dev_config import render_dev_config

            render_dev_config()
        except Exception as e:
            st.caption(f"Dev config unavailable: {e}")

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        with st.expander("Archived MES Futures (dormant)", expanded=False):
            st.markdown(
                ui.info_callout(
                    "MES (Micro E-mini S&P 500) lane is dormant — code preserved, not running. "
                    "Traded via IBKR paper port 7497. Strategies: Opening Range Breakout + VWAP Mean Reversion. "
                    "To reactivate: set FUTURES_LANE_ACTIVE=true in .env and restart.",
                    "info",
                ),
                unsafe_allow_html=True,
            )
            try:
                from widgets.futures.mes_dashboard import render_futures

                render_futures()
            except Exception as e:
                st.caption(f"MES dashboard unavailable: {e}")

    # ── TAB 6: Event Log ───────────────────────────────────────────────────────
    with tab_events:
        st.markdown(
            ui.info_callout(
                "Raw system events written by the bot — the ground truth of what happened. "
                "Errors here are genuine problems. INFO rows are normal operation. "
                "Heartbeat rows confirm the bot is alive.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        ev_left, ev_right = st.columns([2, 1])

        with ev_right:
            body_int = ""
            try:
                from widgets.mission_control.system_health import (
                    render_system_integrity,
                )

                render_system_integrity()
            except Exception as e:
                st.caption(f"System integrity unavailable: {e}")

        with ev_left:
            try:
                from widgets.mission_control.alert_feed import render_alert_feed

                render_alert_feed()
            except Exception as e:
                st.caption(f"Alert feed unavailable: {e}")

            try:
                from db import _qrows = _q(
                    """SELECT ts, source, level, message
                       FROM system_events
                       ORDER BY ts DESC
                       LIMIT 80""",
                )
                if rows:
                    import pandas as pd

                    df_ev = pd.DataFrame(rows)
                    df_ev["ts"] = df_ev["ts"].str[:19]

                    def _row_color(level):
                        if level == "ERROR":
                            return ui.C_RED
                        if level == "WARNING":
                            return ui.C_AMBER
                        return ui._TEXT_CAP

                    st.markdown(
                        ui.section_header(
                            "RAW SYSTEM EVENTS", "Last 80 events from the database"
                        ),
                        unsafe_allow_html=True,
                    )
                    rows_html = ""
                    for _, row in df_ev.iterrows():
                        lvl = row.get("level", "INFO")
                        color = _row_color(lvl)
                        msg = str(row.get("message", ""))[:100]
                        src = row.get("source", "")
                        ts = row.get("ts", "")
                        rows_html += (
                            f'<div style="display:flex;gap:8px;padding:4px 0;'
                            f"border-bottom:1px solid rgba(255,255,255,0.03);"
                            f'font-size:0.73em;">'
                            f'<span style="color:{ui._TEXT_CAP};flex-shrink:0;width:130px;">{ts}</span>'
                            f'<span style="color:{color};font-weight:700;flex-shrink:0;width:60px;">{lvl}</span>'
                            f'<span style="color:{ui._TEXT_CAP};flex-shrink:0;width:90px;">{src}</span>'
                            f'<span style="color:{ui._TEXT_PRI};flex:1;">{msg}</span>'
                            f"</div>"
                        )
                    st.markdown(
                        f'<div style="background:{ui._BG_CARD};border:1px solid {ui._BORDER};'
                        f"border-radius:{ui._RADIUS_SM};padding:10px 14px;"
                        f'max-height:400px;overflow-y:auto;">{rows_html}</div>',
                        unsafe_allow_html=True,
                    )
            except Exception as e:
                st.caption(f"Event log unavailable: {e}")
