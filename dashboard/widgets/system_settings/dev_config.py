"""
Widget: System Settings / Dev Config
Question: What are all the system constants, thresholds, and signal rules?
Tab: SYSTEM SETTINGS
Refresh: manual (no fragment)
Asset class: BOTH
"""

import streamlit as st

from formatters import _time_ago
from data.execution import get_recent_events


def render_dev_config():
    st.subheader("System Settings")
    st.caption("All tuning knobs, signal scoring rules, and raw system constants.")

    col_left, col_right = st.columns(2)

    with col_left:
        with st.expander("Economics gate (risk/economics_gate.py)", expanded=True):
            try:
                from risk.economics_gate import (
                    TAKER_FEE_PCT,
                    ROUND_TRIP_COST,
                    _TIER_APLUS_EV,
                    _TIER_A_EV,
                    _TIER_B_EV,
                    TIER_MULTIPLIERS,
                    _MIN_NET_RR,
                )

                st.text(
                    f"  Taker fee (per side):    {TAKER_FEE_PCT * 100:.3f}%  (Kraken Futures)"
                )
                st.text(f"  Round-trip cost:         {ROUND_TRIP_COST * 100:.3f}%")
                st.text(f"  Min net R:R:             ≥ {_MIN_NET_RR}:1 after fees")
                st.text(
                    f"  Tier A+ (EV ≥ {_TIER_APLUS_EV * 100:.2f}%): {TIER_MULTIPLIERS.get('A+', 1.0)}× size"
                )
                st.text(
                    f"  Tier A  (EV ≥ {_TIER_A_EV * 100:.2f}%):  {TIER_MULTIPLIERS.get('A', 1.0)}× size"
                )
                st.text(
                    f"  Tier B  (EV ≥ {_TIER_B_EV * 100:.2f}%):  {TIER_MULTIPLIERS.get('B', 0.75)}× size"
                )
                st.text(f"  Below B:                 VETO — trade blocked")
            except Exception as e:
                st.error(f"economics_gate: {e}")

        with st.expander("Position sizer (risk/unified_sizer.py)"):
            try:
                from risk.unified_sizer import (
                    BASE_RISK_PCT,
                    MAX_HEAT_PCT,
                    MAX_SINGLE_NOTIONAL_PCT,
                    _QUALITY_MULT,
                )
                from config import ACCOUNT_SIZE

                acct = float(ACCOUNT_SIZE)
                st.text(
                    f"  Formula: size = (acct × {BASE_RISK_PCT * 100:.1f}% × quality_mult) / stop_pct"
                )
                st.text(f"  Account: ${acct:,.0f}")
                st.text(
                    f"  Base risk per trade: {BASE_RISK_PCT * 100:.1f}% = ${acct * BASE_RISK_PCT:.0f}"
                )
                st.text(
                    f"  Portfolio heat cap:  {MAX_HEAT_PCT * 100:.0f}% = ${acct * MAX_HEAT_PCT:.0f}"
                )
                st.text(
                    f"  Hard position cap:   {MAX_SINGLE_NOTIONAL_PCT * 100:.0f}% per symbol"
                )
                st.text(f"  Default leverage:    3× ISOLATED margin")
                for tier, mult in sorted(_QUALITY_MULT.items(), key=lambda x: -x[1]):
                    st.text(f"  Quality {tier}: {mult}× size")
            except Exception as e:
                st.error(f"unified_sizer: {e}")

        with st.expander("6-priority exit stack (position_manager.py)"):
            exits = [
                ("6", "Kill Switch", "Balance < 75% of account / API errors / latency"),
                (
                    "5",
                    "Risk Forced Exit",
                    "Margin breach / VaR breach / correlation limit",
                ),
                ("4", "Hard Stop", "STOP_MARKET at entry − ATR×1.5 · NEVER widened"),
                (
                    "3",
                    "Thesis Invalidated",
                    "composite < entry_score × regime_pct → close (TRENDING=30%, RANGING=15%, HIGH_VOL=35%, default=25%)",
                ),
                ("2", "TP Scale-Out", "2R → 33% · 3.5R → 33% · remainder trails"),
                (
                    "1",
                    "Trailing Stop",
                    "Activates after 1× ATR in favor · trails 1.5× ATR from peak",
                ),
            ]
            for num, title, detail in exits:
                st.text(f"  [{num}] {title}: {detail}")

    with col_right:
        with st.expander("Kill switch & risk rules", expanded=True):
            try:
                from config import ACCOUNT_SIZE, MAX_DAILY_LOSS_PCT

                acct = float(ACCOUNT_SIZE)
                st.text(f"  Kill switch:         Balance < 75% = ${acct * 0.75:,.0f}")
                st.text(
                    f"  Max daily loss:      {MAX_DAILY_LOSS_PCT * 100:.0f}% → halt all trading"
                )
                st.text(f"  Max deployed:        90%")
                st.text(f"  Max risk per trade:  1% of account")
                st.text(f"  Margin type:         ISOLATED — never CROSS")
                st.text(f"  Kraken taker fee:    0.065%")
                st.text(f"  No double-entry:     one position per symbol, ever")
                st.text(f"  No chase:            skip if price moved > 3% since signal")
                st.text(f"  Stop sacred:         never moved wider after entry")
            except Exception as e:
                st.error(f"config: {e}")

        with st.expander("Signal engine entry thresholds"):
            try:
                from signal_engine import _ENTRY_THRESHOLDS, _LONG_SETUPS, _SHORT_SETUPS
                import pandas as pd

                thresh_rows = [
                    {"Regime": r, "Min Score": f"≥ {t} / 100"}
                    for r, t in sorted(_ENTRY_THRESHOLDS.items())
                ]
                st.dataframe(
                    pd.DataFrame(thresh_rows),
                    use_container_width=False,
                    hide_index=True,
                )
            except Exception as e:
                st.error(f"signal_engine: {e}")

        with st.expander("Scanner config (live from scanner.py)"):
            try:
                from scanner import (
                    _MIN_VOLUME_24H_USD,
                    _MIN_VOL_SPIKE,
                    _MIN_PRICE_MOVE_1H,
                    _MIN_ADX_MOMENTUM,
                    _MIN_OB_DEPTH_USD,
                    _MAX_SPREAD_PCT,
                    _MIN_EXPECTED_PROFIT,
                    _ROUND_TRIP_FEE_PCT,
                )

                st.text(f"  Min 24h volume:  ${_MIN_VOLUME_24H_USD / 1e6:.1f}M")
                st.text(f"  Min vol spike:   ≥ {_MIN_VOL_SPIKE}×")
                st.text(f"  Min price move:  ≥ {_MIN_PRICE_MOVE_1H:.2f}%")
                st.text(f"  Min ADX:         ≥ {_MIN_ADX_MOMENTUM}")
                st.text(
                    f"  Min OB depth:    ≥ ${_MIN_OB_DEPTH_USD / 1e3:.0f}K each side"
                )
                st.text(f"  Max spread:      < {_MAX_SPREAD_PCT:.2f}%")
                st.text(f"  Min EV:          ≥ ${_MIN_EXPECTED_PROFIT:.2f}")
                st.text(f"  Round-trip fee:  {_ROUND_TRIP_FEE_PCT * 100:.3f}%")
                st.text(
                    f"  Sources:         Kraken Futures + Binance USDM + Hyperliquid"
                )
            except Exception as e:
                st.error(f"scanner: {e}")

    with st.expander("Full config.py constants"):
        try:
            import config as _cfg
            import pandas as pd

            items = sorted(
                {
                    k: str(v)
                    for k, v in vars(_cfg).items()
                    if not k.startswith("_") and isinstance(v, (int, float, str, bool))
                }.items()
            )
            st.dataframe(
                pd.DataFrame(items, columns=["Key", "Value"]),
                use_container_width=True,
                hide_index=True,
            )
        except Exception as e:
            st.error(str(e))

    with st.expander("Technical tower — all scoring conditions (LONG side)"):
        long_signals = [
            ("CVD bullish divergence", "+25"),
            ("MACD all variants aligned long", "+20"),
            ("TradingView webhook confirmed", "+20"),
            ("RSI bullish divergence", "+15"),
            ("Funding squeeze (< −0.3 norm)", "+15"),
            ("VWAP reclaim on volume", "+15"),
            ("Liquidation cascade → long magnet", "+15"),
            ("WaveTrend oversold cross", "+12"),
            ("SuperTrend bullish (ATR10 ×3)", "+12"),
            ("WAE Bullish + Exploding", "+10"),
            ("OB L5 imbalance > 0.60", "+10"),
            ("Williams %R < −80", "+10"),
            ("Whale accumulation signal", "+10"),
            ("Options skew bullish", "+10"),
            ("MACD fast histogram positive", "+8"),
            ("Funding favorable (−0.1 to −0.3)", "+8"),
            ("KST above signal line", "+8"),
            ("Fisher Transform cross up", "+8"),
            ("Ichimoku cloud bullish", "+8"),
            ("Laguerre RSI < 0.15 (deep OS)", "+8"),
            ("OB L5 imbalance 0.55–0.60", "+5"),
            ("Williams %R −80 to −70", "+5"),
            ("Vol spike > 1.5×", "+5"),
            ("RSI not overbought (< 60)", "+5"),
            ("Choppiness trending (< 38.2)", "+5"),
            ("WAE Bullish only", "+5"),
            ("Price > 2σ VWAP", "−25"),
            ("CVD bearish divergence", "−20"),
            ("Extreme positive funding (> 0.5)", "−20"),
            ("RSI bearish divergence", "−15"),
            ("Cascade risk > 0.70", "−15"),
            ("OB L5 < 0.40 (bear pressure)", "−10"),
            ("Fear & Greed euphoria (> 85)", "−10"),
        ]
        import pandas as pd

        st.caption("Raw range ~−115 to +150 · normalized 0–100 · mirrored for SHORT")
        st.dataframe(
            pd.DataFrame(long_signals, columns=["Condition", "Points"]),
            use_container_width=False,
            hide_index=True,
        )

    st.divider()
    st.caption("**System events log** (last 20)")
    events = get_recent_events(20)
    if events:
        import pandas as pd

        rows = [
            {
                "Time": _time_ago(e.get("ts", "")),
                "Level": e.get("level", ""),
                "Source": e.get("source", "")[:30],
                "Message": e.get("message", "")[:120],
            }
            for e in events
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
