"""
dashboard/widgets/pages/crypto_page.py — CRYPTO page.

Premium design: top lane-status strip, 6 internal subtabs, polished
candidate cards showing why-appeared / why-works / what-kills-it.
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
from data.crypto_dashboard import (
    get_crypto_header,
    get_crypto_opportunity_board,
    get_crypto_failure_summary,
)
from formatters import _time_ago
from runtime.spot_strategy import edge_policy_for_symbol, setup_preference_for_symbol


# ── Setup-name human labels ────────────────────────────────────────────────────
_SETUP_LABELS = {
    "cvd_divergence": "CVD Divergence",
    "macd_momentum": "MACD Momentum",
    "vwap_reclaim": "VWAP Reclaim",
    "ob_imbalance": "Order Book Imbalance",
    "funding_squeeze": "Funding Squeeze",
    "rsi_divergence": "RSI Divergence",
    "supertrend_cross": "SuperTrend Cross",
    "ichimoku_breakout": "Ichimoku Breakout",
    "wave_trend": "WaveTrend Signal",
    "liq_cascade": "Liquidation Cascade",
    "whale_signal": "Whale Signal",
    "ranging_mr": "Mean Reversion",
    "vol_spike": "Volume Spike",
    "tv_signal": "TradingView Alert",
}

_BLOCKED_LABELS = {
    "unknown_symbol_mapping": "Symbol not in live universe",
    "spot_lane_disabled": "Spot lane is off",
    "spot_direction_not_allowed": "No spot shorts allowed",
    "spot_position_already_open": "Spot position already open",
    "spot_outside_session": "Outside spot entry session",
    "spot_deployment_cap_exceeded": "Spot capital cap reached",
    "spot_balance_unavailable": "Spot balance unavailable",
    "underlying_exposure_already_open": "Underlying already active in another lane",
    "perp_symbol_not_supported": "Perp symbol not in Coinbase universe",
    "perp_not_autonomous_eligible": "Manual-only (not auto-eligible)",
    "perp_position_limit_reached": "Max 3 live perps already open",
    "perp_opposite_side_block": "Opposite position already open",
    "perp_deployment_cap_exceeded": "Deployed capital cap reached",
    "perp_contract_min_exceeds_policy": "Min contract > size policy",
    "execution_policy_unavailable": "Policy engine unavailable",
    "research_only_block": "Research-only — not in live universe",
    "below_threshold": "Score below entry threshold",
    "below_regime_floor": "Final spot score below live regime floor",
    "setup_score_too_low": "Setup evidence too weak for an opportunistic scalp",
    "preferred_setup_score_too_low": "Preferred setup detected, but derivative evidence is still too weak",
    "edge_setup_family_mismatch": "Doesn't match this coin's replay-derived edge setup",
    "edge_setup_score_too_low": "Setup evidence is below this coin's replay edge threshold",
    "edge_structure_component_too_low": "5m structure is too weak for this coin's edge",
    "edge_volatility_quality_too_low": "30m volatility quality is below this coin's edge",
    "edge_acceleration_too_low": "5m acceleration is too weak for this coin's edge",
    "edge_momentum_impulse_too_low": "5m momentum impulse is below this coin's edge",
    "edge_regime_mismatch": "Current regime doesn't match this coin's replay edge",
    "econ_veto": "Economics gate failed (fees/spread)",
    "projected_net_win_too_small": "Net profit too small after fees",
    "non_positive_net_target": "Target does not clear trading costs",
    "spread_cap_exceeded": "Spread too wide for scalp economics",
    "depth_below_minimum": "Book depth too thin for scalp entry",
    "spot_data_unavailable": "Spot state unavailable from live candles",
    "data_unavailable": "No candle data for this pair",
    "sizing_zero": "Position size computed to zero",
}


def _setup_label(raw: str) -> str:
    if not raw:
        return "Unknown setup"
    return _SETUP_LABELS.get(raw, raw.replace("_", " ").title())


def _blocked_label(raw: str) -> str:
    if not raw:
        return ""
    return _BLOCKED_LABELS.get(raw, raw.replace("_", " "))


def _candidate_card(row: dict) -> str:
    status = row.get("status", "not_evaluated")
    sym = row.get("underlying") or row.get("symbol", "?")
    direction = (row.get("direction") or "").upper()
    lane = row.get("recommended_lane", "")
    score = float(row.get("score") or 0)
    spot_regime = row.get("spot_regime") or ""
    setup_family = row.get("setup_family") or ""
    setup_pref = row.get("setup_preference") or (
        setup_preference_for_symbol(sym, setup_family) if setup_family else ""
    )
    setup_score = float(row.get("setup_score") or 0.0)
    structural_confirms = row.get("structural_confirms") or ""
    execution_route = row.get("execution_route") or ""
    cooldown_until = row.get("cooldown_until") or ""
    microstructure_veto = row.get("microstructure_veto") or ""
    regime_floor = float(row.get("regime_floor") or 0.0)
    auto_ex = bool(row.get("auto_executable"))
    manual_ex = bool(row.get("manual_executable"))
    blocked_raw = row.get("trade_blocked_reason") or row.get("decision") or ""
    setup_raw = row.get("setup_label") or row.get("primary_setup") or ""
    exchange = row.get("exchange") or row.get("source") or ""
    ts = row.get("ts", "")
    edge_policy = edge_policy_for_symbol(sym)
    edge_metrics = edge_policy.get("metrics") or {}
    edge_profile = str(edge_policy.get("profile") or "").title()
    edge_summary = str(edge_policy.get("conditions_summary") or "")

    # Status color + label
    if status == "executable":
        s_color, s_label = ui.C_GREEN, "EXECUTABLE"
    elif status == "blocked":
        s_color, s_label = ui.C_RED, "BLOCKED"
    else:
        s_color, s_label = ui.C_NEUTRAL, status.upper().replace("_", " ")

    dir_color = (
        ui.C_GREEN
        if direction == "LONG"
        else (ui.C_RED if direction == "SHORT" else ui._TEXT_CAP)
    )
    dir_arrow = "▲" if direction == "LONG" else ("▼" if direction == "SHORT" else "")

    # Lane badge
    lane_color = (
        ui.C_CYAN if lane == "perp" else ui.C_MAG if lane == "spot" else ui._TEXT_CAP
    )
    lane_label = lane.upper() if lane else ""

    # Score color
    sc_color = ui.C_GREEN if score >= 65 else (ui.C_AMBER if score >= 50 else ui.C_RED)

    # Auto/manual badges
    mode_parts = []
    if auto_ex:
        mode_parts.append(
            f'<span style="color:{ui.C_CYAN};font-size:0.68em;font-weight:700;'
            f'padding:1px 6px;background:rgba(88,166,255,0.10);border-radius:4px;">AUTO</span>'
        )
    if manual_ex and not auto_ex:
        mode_parts.append(
            f'<span style="color:{ui.C_MAG};font-size:0.68em;font-weight:700;'
            f'padding:1px 6px;background:rgba(188,140,255,0.10);border-radius:4px;">MANUAL ONLY</span>'
        )
    mode_html = " ".join(mode_parts)

    # Three-section body
    # WHY IT APPEARED
    why_appeared_parts = []
    if setup_raw:
        why_appeared_parts.append(f"Setup: <strong>{_setup_label(setup_raw)}</strong>")
    if setup_family:
        why_appeared_parts.append(
            f"Scalp family: <strong>{setup_family.replace('_', ' ').title()}</strong>"
        )
    if setup_pref and setup_pref not in {"unknown", "disallowed"}:
        why_appeared_parts.append(f"Policy bias: <strong>{setup_pref.title()}</strong>")
    if setup_score > 0:
        why_appeared_parts.append(f"Setup evidence: <strong>{setup_score:.2f}</strong>")
    if edge_profile:
        why_appeared_parts.append(f"Replay edge: <strong>{edge_profile}</strong>")
    if edge_metrics:
        why_appeared_parts.append(
            "Replay stats: "
            f"PF <strong>{float(edge_metrics.get('pf') or 0.0):.2f}</strong> · "
            f"WR <strong>{float(edge_metrics.get('wr') or 0.0) * 100:.1f}%</strong> · "
            f"n <strong>{int(edge_metrics.get('n') or 0)}</strong>"
        )
    if exchange:
        why_appeared_parts.append(f"Source: {exchange.replace('_', ' ').title()}")
    if ts:
        why_appeared_parts.append(f"Seen: {_time_ago(ts)}")
    why_appeared = (
        "<br>".join(why_appeared_parts) if why_appeared_parts else "Scan candidate"
    )

    # WHY IT MIGHT WORK
    why_works_parts = [
        f"Direction: <strong style='color:{dir_color};'>{dir_arrow} {direction}</strong>",
        f"Score: <strong style='color:{sc_color};'>{score:.0f}/100</strong>",
    ]
    if lane:
        why_works_parts.append(
            f"Venue: <strong style='color:{lane_color};'>{lane_label}</strong>"
        )
    if spot_regime:
        why_works_parts.append(f"Regime: <strong>{spot_regime.title()}</strong>")
    if structural_confirms:
        why_works_parts.append(
            f"Confirms: <strong>{structural_confirms.replace(',', ', ')}</strong>"
        )
    if regime_floor > 0:
        why_works_parts.append(f"Floor: <strong>{regime_floor:.0f}</strong>")
    if edge_summary:
        why_works_parts.append(f"Edge filter: <strong>{edge_summary}</strong>")
    if execution_route:
        why_works_parts.append(
            f"Route: <strong>{execution_route.replace('_', ' ')}</strong>"
        )
    why_works = "<br>".join(why_works_parts)

    # WHAT COULD KILL IT
    kill_parts = []
    if blocked_raw and status == "blocked":
        kill_parts.append(
            f'<span style="color:{ui.C_RED};">{_blocked_label(blocked_raw)}</span>'
        )
    size_block = row.get("trade_size_block_reason") or ""
    if size_block and size_block not in ("none", ""):
        kill_parts.append(
            f'Size: <span style="color:{ui.C_AMBER};">{size_block.replace("_", " ")}</span>'
        )
    source_reason = row.get("trade_source_reason") or row.get("source_reason") or ""
    if source_reason and source_reason not in (
        "none",
        "not_applicable",
        "trusted_source",
        "",
    ):
        kill_parts.append(
            f'Source: <span style="color:{ui.C_AMBER};">{source_reason.replace("_", " ")}</span>'
        )
    if cooldown_until:
        kill_parts.append(
            f'Cooldown: <span style="color:{ui.C_AMBER};">{cooldown_until}</span>'
        )
    if microstructure_veto:
        kill_parts.append(
            f'Microstructure: <span style="color:{ui.C_AMBER};">{microstructure_veto.replace("_", " ")}</span>'
        )
    if not kill_parts:
        if status == "executable":
            kill_parts.append(
                f'<span style="color:{ui.C_GREEN};">No blockers — eligible now</span>'
            )
        else:
            kill_parts.append(
                f'<span style="color:{ui._TEXT_CAP};">Status: {status.replace("_", " ")}</span>'
            )
    what_kills = "<br>".join(kill_parts)

    top_border = f"border-top:2px solid {s_color};"

    return (
        f'<div style="background:{ui._BG_CARD};{top_border}border:1px solid {ui._BORDER};'
        f'border-radius:{ui._RADIUS_SM};padding:14px 16px;margin-bottom:8px;">'
        # Header row
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'margin-bottom:10px;flex-wrap:wrap;gap:6px;">'
        f'<div style="display:flex;align-items:center;gap:10px;">'
        f'<span style="font-weight:800;color:{ui._TEXT_PRI};font-size:1.1em;">{sym}</span>'
        f'<span style="color:{dir_color};font-weight:700;font-size:0.80em;">{dir_arrow} {direction}</span>'
        f"{mode_html}"
        f"</div>"
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<span style="color:{s_color};font-size:0.70em;font-weight:700;'
        f'padding:2px 8px;background:{s_color}1a;border-radius:100px;">{s_label}</span>'
        f"</div>"
        f"</div>"
        # Three columns
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;'
        f'border-top:1px solid {ui._BORDER};padding-top:10px;">'
        # Col 1
        f"<div>"
        f'<div style="font-size:0.64em;color:{ui._TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:4px;">Why it appeared</div>'
        f'<div style="font-size:0.75em;color:{ui._TEXT_SEC};line-height:1.55;">{why_appeared}</div>'
        f"</div>"
        # Col 2
        f"<div>"
        f'<div style="font-size:0.64em;color:{ui._TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:4px;">Why it might work</div>'
        f'<div style="font-size:0.75em;color:{ui._TEXT_SEC};line-height:1.55;">{why_works}</div>'
        f"</div>"
        # Col 3
        f"<div>"
        f'<div style="font-size:0.64em;color:{ui._TEXT_CAP};text-transform:uppercase;'
        f'letter-spacing:0.08em;margin-bottom:4px;">What could stop it</div>'
        f'<div style="font-size:0.75em;color:{ui._TEXT_SEC};line-height:1.55;">{what_kills}</div>'
        f"</div>"
        f"</div>"
        f"</div>"
    )


def render_crypto_page():
    # ── Lane status strip ──────────────────────────────────────────────────────
    hdr = get_crypto_header()

    health = hdr.get("lane_health", "UNKNOWN")
    health_status = (
        "good"
        if health == "HEALTHY"
        else "problem"
        if health in ("UNHEALTHY", "ERROR")
        else "watch"
    )

    bp = hdr.get("buying_power", 0.0)
    spot_cash = hdr.get("spot_cash_available", 0.0)
    spot_symbols = hdr.get("spot_symbols", ["BTC", "ETH", "SOL", "XRP"])
    perp_pct = hdr.get("perp_deployed_pct", 0.0)
    spot_pct = hdr.get("spot_deployed_pct", 0.0)
    open_ct = hdr.get("open_count", 0)
    mode_label = hdr.get("mode_label", "UNKNOWN")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            ui.summary_card(
                "Crypto Lane",
                health,
                health.title(),
                health_status,
                f"Mode: {mode_label} · Perp {'active' if hdr.get('perp_active') else 'inactive'} · "
                f"Spot {'active' if hdr.get('spot_active') else 'inactive'}",
            ),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            ui.summary_card(
                "Buying Power",
                f"${bp:,.0f}",
                "Available" if bp > 200 else "Low",
                "good" if bp > 200 else "watch",
                f"Perp buying power ${bp:,.0f} · Spot cash ${spot_cash:,.0f}",
            ),
            unsafe_allow_html=True,
        )
    with c3:
        deployed_total = perp_pct + spot_pct
        st.markdown(
            ui.summary_card(
                "Capital Deployed",
                f"{deployed_total:.1f}%",
                "Normal"
                if deployed_total < 50
                else "High"
                if deployed_total < 85
                else "Near Cap",
                "good"
                if deployed_total < 50
                else "watch"
                if deployed_total < 85
                else "problem",
                f"Perp {perp_pct:.1f}% + Spot {spot_pct:.1f}% · Spot lane capped separately",
            ),
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            ui.summary_card(
                "Open Positions",
                str(open_ct),
                "Flat" if open_ct == 0 else f"{open_ct} open",
                "neutral" if open_ct == 0 else "good" if open_ct <= 2 else "watch",
                f"Perp max 3 live · Spot universe: {', '.join(spot_symbols)}",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # ── Internal subtabs ───────────────────────────────────────────────────────
    (
        tab_overview,
        tab_board,
        tab_console,
        tab_positions,
        tab_diag,
        tab_scanner,
        tab_history,
    ) = st.tabs(
        [
            "Overview",
            "Opportunity Board",
            "Trade Console",
            "Open Positions",
            "Diagnostics",
            "Scanner Detail",
            "Coinbase History",
        ]
    )

    # ── TAB 1: Overview ────────────────────────────────────────────────────────
    with tab_overview:
        st.markdown(
            ui.info_callout(
                "Live snapshot of the crypto lane — positions, recent fills, and scanner pulse. "
                "Refresh every 30 seconds.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        col_left, col_right = st.columns([1, 1])

        with col_left:
            try:
                from widgets.mission_control.open_positions import (
                    render_positions_compact,
                )

                render_positions_compact()
            except Exception as e:
                st.caption(f"Positions unavailable: {e}")

        with col_right:
            try:
                from widgets.mission_control.equity_curve import (
                    render_equity_curve_compact,
                )

                render_equity_curve_compact()
            except Exception as e:
                st.caption(f"Equity curve unavailable: {e}")

        try:
            from widgets.mission_control.failure_modes import render_failures_compact

            render_failures_compact()
        except Exception as e:
            st.caption(f"Failure modes unavailable: {e}")

    # ── TAB 2: Opportunity Board ────────────────────────────────────────────────
    with tab_board:
        st.markdown(
            ui.info_callout(
                "All recent scan candidates — executable, blocked, and researched. "
                "Each card shows the real live score, regime floor, and blocker category the bot used.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        ctrl_left, ctrl_mid, ctrl_right = st.columns([1, 1.5, 1])
        with ctrl_left:
            hours_sel = st.selectbox(
                "Time window",
                ["1h", "6h", "24h", "7d"],
                index=2,
                key="crypto_board_hours",
            )
        with ctrl_mid:
            filter_sel = st.radio(
                "Filter",
                [
                    "All",
                    "Executable",
                    "Spot",
                    "Perp",
                    "Blocked",
                    "Auto-only",
                    "Manual-only",
                ],
                horizontal=True,
                key="crypto_board_filter",
            )
        with ctrl_right:
            sort_sel = st.selectbox(
                "Sort by",
                ["Newest", "Executable first", "Highest score", "Blocked reason"],
                key="crypto_board_sort",
            )

        hours_map = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
        board_hours = hours_map[hours_sel]
        rows = get_crypto_opportunity_board(hours=board_hours)

        if filter_sel == "Executable":
            rows = [r for r in rows if r.get("status") == "executable"]
        elif filter_sel == "Spot":
            rows = [r for r in rows if r.get("recommended_lane") == "spot"]
        elif filter_sel == "Perp":
            rows = [r for r in rows if r.get("recommended_lane") == "perp"]
        elif filter_sel == "Blocked":
            rows = [r for r in rows if r.get("status") == "blocked"]
        elif filter_sel == "Auto-only":
            rows = [r for r in rows if r.get("auto_executable")]
        elif filter_sel == "Manual-only":
            rows = [
                r
                for r in rows
                if r.get("manual_executable") and not r.get("auto_executable")
            ]

        if sort_sel == "Executable first":
            rows = sorted(
                rows,
                key=lambda r: (
                    0 if r.get("status") == "executable" else 1,
                    r.get("ts", ""),
                ),
            )
        elif sort_sel == "Highest score":
            rows = sorted(rows, key=lambda r: float(r.get("score") or 0), reverse=True)
        elif sort_sel == "Blocked reason":
            rows = sorted(
                rows,
                key=lambda r: r.get("trade_blocked_reason") or r.get("decision") or "",
            )

        if rows:
            st.caption(f"{len(rows)} candidates in the last {hours_sel}")
            for row in rows[:50]:
                st.markdown(_candidate_card(row), unsafe_allow_html=True)
        else:
            st.markdown(
                ui.empty_state(
                    "No candidates in this window",
                    "The scanner runs every 3–5 minutes. Come back after the next cycle, "
                    "or widen the time window.",
                ),
                unsafe_allow_html=True,
            )

    # ── TAB 3: Trade Console ───────────────────────────────────────────────────
    with tab_console:
        st.markdown(
            ui.info_callout(
                "Manual trade entry — run a fresh scan, review candidates, and execute if warranted. "
                "All executions go through the same policy checks as the live bot.",
                "info",
            ),
            unsafe_allow_html=True,
        )
        try:
            from widgets.trade_approval.manual_scan import render_manual_scan

            render_manual_scan()
        except Exception as e:
            st.error(f"Manual trade console unavailable: {e}")

    # ── TAB 4: Open Positions ──────────────────────────────────────────────────
    with tab_positions:
        st.markdown(
            ui.info_callout(
                "All open crypto positions — perp and spot. Each card shows live P&L, "
                "stop distance, and position age.",
                "info",
            ),
            unsafe_allow_html=True,
        )
        try:
            from widgets.mission_control.open_positions import render_positions_compact

            render_positions_compact()
        except Exception as e:
            st.caption(f"Positions widget unavailable: {e}")

    # ── TAB 5: Diagnostics ─────────────────────────────────────────────────────
    with tab_diag:
        st.markdown(
            ui.info_callout(
                "What went wrong and why. Execution failures are broker rejections. "
                "Policy blocks are the system protecting you from bad trades.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        hours_sel2 = st.selectbox(
            "Window", ["1h", "6h", "24h", "7d"], index=2, key="diag_hours"
        )
        diag_hours = hours_map[hours_sel2]

        try:
            failure_data = get_crypto_failure_summary(hours=diag_hours)

            d_left, d_mid, d_right = st.columns(3)

            with d_left:
                exec_fails = failure_data.get("execution_failures") or []
                body_html = ""
                for f in exec_fails[:8]:
                    reason = f.get("reason", "unknown")
                    sym_f = f.get("symbol", "")
                    ts_f = f.get("ts", "")[:16]
                    body_html += ui.metric_row(
                        f"{sym_f} {f.get('direction', '')}",
                        reason.replace("_", " "),
                        ui.C_RED,
                    )
                if not body_html:
                    body_html = ui.info_callout(
                        "No execution failures in this window.", "good"
                    )
                st.markdown(
                    ui.detail_card(
                        "Execution Failures",
                        "Broker rejected the order outright",
                        body_html,
                    ),
                    unsafe_allow_html=True,
                )

            with d_mid:
                quality_blocks = failure_data.get("top_quality_blocks") or []
                body_html = ""
                for b in quality_blocks[:8]:
                    reason_b = b.get("reason", "unknown")
                    count_b = b.get("n", 0)
                    body_html += ui.metric_row(
                        _blocked_label(reason_b) or reason_b,
                        f"{count_b}×",
                        ui.C_AMBER,
                    )
                if not body_html:
                    body_html = ui.info_callout(
                        "No score / setup blocks in this window.", "good"
                    )
                st.markdown(
                    ui.detail_card(
                        "Quality Blocks",
                        "Setups the bot saw but rejected on score / regime quality",
                        body_html,
                    ),
                    unsafe_allow_html=True,
                )
            with d_right:
                econ_blocks = failure_data.get("top_econ_blocks") or []
                body_html = ""
                for b in econ_blocks[:8]:
                    reason_b = b.get("reason", "unknown")
                    count_b = b.get("n", 0)
                    body_html += ui.metric_row(
                        _blocked_label(reason_b) or reason_b,
                        f"{count_b}×",
                        ui.C_AMBER,
                    )
                if not body_html:
                    body_html = ui.info_callout(
                        "No economics / microstructure blocks in this window.", "good"
                    )
                st.markdown(
                    ui.detail_card(
                        "Economics Blocks",
                        "Trades that passed setup review but failed costs or microstructure",
                        body_html,
                    ),
                    unsafe_allow_html=True,
                )
        except Exception as e:
            st.caption(f"Diagnostic data unavailable: {e}")

    # ── TAB 6: Scanner Detail ──────────────────────────────────────────────────
    with tab_scanner:
        st.markdown(
            ui.info_callout(
                "Raw scanner filter output — how many pairs passed each gate in the most recent scan cycle.",
                "info",
            ),
            unsafe_allow_html=True,
        )
        try:
            from widgets.trade_approval.scan_breakdown import render_scan_breakdown

            render_scan_breakdown()
        except Exception as e:
            st.caption(f"Scan breakdown unavailable: {e}")

    # ── TAB 7: Coinbase Order History ──────────────────────────────────────────
    with tab_history:
        st.markdown(
            ui.info_callout(
                "Orders placed by the bot via its Coinbase API key. "
                "Purchases made through the Coinbase app or website are NOT visible here — "
                "only orders this bot placed programmatically.",
                "info",
            ),
            unsafe_allow_html=True,
        )

        _paper_mode = False
        try:
            import sys as _sys, os as _os

            _root = _os.path.join(_os.path.dirname(__file__), "../../..")
            if _root not in _sys.path:
                _sys.path.insert(0, _root)
            from dashboard.db import _runtime_paper_flag

            _paper_mode = _runtime_paper_flag()
        except Exception:
            pass

        if _paper_mode:
            st.info("Running in PAPER mode — no real Coinbase orders to show.")
        else:
            _limit = st.slider(
                "Orders to fetch", min_value=10, max_value=200, value=50, step=10
            )
            if st.button("Load Coinbase Order History", key="load_cb_history"):
                with st.spinner("Fetching from Coinbase..."):
                    try:
                        from execution.coinbase_spot_broker import get_spot_broker

                        _broker = get_spot_broker()
                        if not _broker.is_connected():
                            _broker.connect()
                        _orders = _broker.get_order_history(limit=_limit)
                        st.session_state["cb_order_history"] = _orders
                    except Exception as _e:
                        st.error(f"Failed to fetch order history: {_e}")
                        st.session_state["cb_order_history"] = []

            _orders = st.session_state.get("cb_order_history")
            if _orders is None:
                st.caption(
                    "Click the button above to load your Coinbase order history."
                )
            elif not _orders:
                st.caption("No filled orders found for this API key.")
            else:
                # Summary metrics
                _buys = [o for o in _orders if o["side"] == "BUY"]
                _sells = [o for o in _orders if o["side"] == "SELL"]
                _total_fees = sum(o["fee_usd"] for o in _orders)
                _total_vol = sum(o["total_value_usd"] for o in _orders)
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Total Orders", len(_orders))
                mc2.metric("Buys / Sells", f"{len(_buys)} / {len(_sells)}")
                mc3.metric("Total Volume", f"${_total_vol:,.2f}")
                mc4.metric("Total Fees Paid", f"${_total_fees:.4f}")

                st.markdown("---")

                # Order rows
                rows_html = ""
                for o in _orders:
                    side_color = ui.C_GREEN if o["side"] == "BUY" else ui.C_RED
                    side_label = "▲ BUY" if o["side"] == "BUY" else "▼ SELL"
                    ts_raw = o.get("created_time", "")[:19].replace("T", " ")
                    rows_html += (
                        f'<div style="display:flex;justify-content:space-between;align-items:center;'
                        f'padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-size:0.80em;">'
                        f'<div style="width:110px;color:#8b949e;">{ts_raw}</div>'
                        f'<div style="width:60px;font-weight:700;color:#e6edf3;">{o["symbol"]}</div>'
                        f'<div style="width:60px;color:{side_color};font-weight:600;">{side_label}</div>'
                        f'<div style="width:90px;color:#e6edf3;">{o["filled_size"]:.6g} units</div>'
                        f'<div style="width:100px;color:#e6edf3;">@ ${o["avg_fill_price"]:,.4g}</div>'
                        f'<div style="width:100px;font-weight:700;color:#e6edf3;">${o["total_value_usd"]:,.2f}</div>'
                        f'<div style="width:80px;color:#d29922;">fee ${o["fee_usd"]:.4f}</div>'
                        f'<div style="width:80px;color:#484f58;font-size:0.85em;">{o["order_id"][:12]}…</div>'
                        f"</div>"
                    )
                st.markdown(
                    ui.detail_card(
                        "FILLED ORDERS",
                        f"{len(_orders)} most recent — API key orders only",
                        rows_html,
                    ),
                    unsafe_allow_html=True,
                )
