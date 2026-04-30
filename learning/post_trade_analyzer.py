"""
learning/post_trade_analyzer.py — Runs immediately after every trade close.

Extracts structured attribution from the closed trade:
  - which signals were active at entry
  - what regime it was in
  - which agents voted what
  - what actually happened

Stores to trade_attribution table.
Updates signal_stats (Bayesian weights shift).
Updates agent_stats.
Writes a 'lesson' string fed back into LanceDB memory.

Called from job_runner._execute_crypto_exit() and _execute_equity_exit().
"""

import os
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from learning.signal_performance import (
    record_trade_attribution,
    record_agent_votes,
    SIGNAL_PRIOR_PTS,
)


# ── Signal extraction ─────────────────────────────────────────────────────────

# v10 Tier 1 setup names — the actual signal names fired by signal_engine.detect_primary_setup()
_V10_ALL_SETUP_NAMES = {
    "wt_reversal",
    "squeeze_breakout",
    "wae_explosion",
    "tv_confirmed_long",
    "tv_confirmed_short",
    "supertrend_cross_long",
    "supertrend_cross_short",
    "kst_cross_long",
    "kst_cross_short",
    "ichimoku_cloud_breakout_long",
    "ichimoku_cloud_breakout_short",
    "ranging_mr_long",
    "ranging_mr_short",
    "wt_overbought_reversal",
    "squeeze_breakout_short",
    "wae_explosion_short",
}


def extract_signals_from_market_data(market_data: dict) -> dict[str, bool]:
    """
    Map the market_data / features dict to canonical signal names for Bayesian attribution.

    v10 path: market_data contains the 57 ML features + 'regime' + 'primary_setup'
              (primary_setup is the Tier 1 setup name from signal_engine.detect_primary_setup).
              One setup → True, all others → False.  Clean, unambiguous attribution.

    v9 legacy path: falls back to indicator-flag extraction for historical records
                    that don't have a primary_setup key.

    Returns {signal_name: bool} — True if the signal was active at entry.
    """
    md = market_data or {}

    def _b(key, default=False):
        v = md.get(key, default)
        return bool(v) if v is not None else default

    def _f(key, default=0.0):
        try:
            return float(md.get(key) or default)
        except Exception:
            return default

    # ── spot-native path: setup_family + structural confirm language ─────────
    setup_family = str(md.get("setup_family") or "").strip()
    if setup_family:
        return {
            "supertrend_bullish": _b("supertrend_bullish"),
            "ichimoku_bullish": _b("cloud_bullish"),
            "kst_bullish": _b("kst_bullish"),
            "price_above_vwap": _b("price_above_vwap"),
            "momentum_impulse_positive": _f("momentum_impulse", 0.0) > 0,
            "participation_positive": _f("participation_component", 0.0) > 0,
            "compression_release": _b("compression_release"),
            f"setup_family::{setup_family}": True,
        }

    # ── v10 path: primary_setup key present ──────────────────────────────────
    primary_setup = str(md.get("primary_setup") or "").strip()
    if primary_setup and primary_setup in _V10_ALL_SETUP_NAMES:
        signals = {name: (name == primary_setup) for name in _V10_ALL_SETUP_NAMES}
        # Also track tradingview_signal for TV-confirmed setups
        signals["tradingview_signal"] = "tv_confirmed" in primary_setup
        return signals

    # ── v9 legacy path: extract from indicator flags ──────────────────────────
    # Used for historical records that predate the primary_setup field.
    _active = set(md.get("active_signals") or [])
    _sig_type = str(md.get("signal_type") or "")

    signals = {
        # v9 engine signals
        "engine_cascade": "cascade" in _active or _sig_type == "cascade",
        "engine_divergence": "divergence" in _active or _sig_type == "divergence",
        "engine_obi": "obi" in _active or _sig_type == "obi",
        "engine_vwap_reclaim": "vwap_reclaim" in _active or _sig_type == "vwap_reclaim",
        "engine_macd_fallback": "macd_fallback" in _active
        or _sig_type == "macd_fallback",
        # v9 indicator flags
        "macd_consensus": _b("macd_consensus"),
        "williams_r": _f("williams_r", 0) <= -80,
        "momentum_volume": _f("momentum_score", 0) > 0.6 and _f("vol_spike", 1) > 1.3,
        "squeeze_fired": _b("squeeze_fired") and _f("squeeze_bars", 0) >= 20,
        "supertrend_bullish": _b("supertrend_bullish"),
        "wavetrend_cross": _b("wt_oversold_cross"),
        "ichimoku_bullish": _b("cloud_bullish"),
        "fisher_cross_up": _b("fisher_cross_up"),
        "lrsi_oversold": (_f("lrsi", 0.5) or 0.5) < 0.15,
        "wae_bullish_exploding": _b("wae_bullish") and _b("wae_exploding"),
        "wae_bullish": _b("wae_bullish") and not _b("wae_exploding"),
        "chop_trending": _b("chop_trending"),
        "lrsi_mild_oversold": 0.15 <= (_f("lrsi", 0.5) or 0.5) < 0.25,
        "tradingview_signal": _b("tv_signal_active"),
    }
    return signals


def _generate_lesson(
    symbol: str,
    regime: str,
    signals: dict,
    won: bool,
    pnl_usd: float,
    pnl_pct: float,
    fee_usd: float,
    exit_reason: str,
    hold_minutes: float,
    agent_votes: dict,
) -> str:
    """
    Generate a concise, structured 'why this trade worked/failed' lesson string.
    Stored in trade_attribution.lesson and fed into LanceDB memory.
    """
    outcome = "WIN" if won else "LOSS"
    active = [s for s, v in signals.items() if v]
    inactive_key = [
        s
        for s, v in signals.items()
        if not v
        and s
        in ("supertrend_bullish", "ichimoku_bullish", "squeeze_fired", "rv_expansion")
    ]

    buy_agents = [a for a, v in agent_votes.items() if str(v).upper() == "BUY"]
    hold_agents = [a for a, v in agent_votes.items() if str(v).upper() == "HOLD"]
    sell_agents = [a for a, v in agent_votes.items() if str(v).upper() == "SELL"]

    net_after_fee = pnl_usd - fee_usd
    fee_pct_of_move = (
        abs(fee_usd / max(abs(pnl_usd), 0.01)) * 100 if pnl_usd != 0 else 0
    )

    lines = [
        f"OUTCOME: {outcome} | {symbol} | {regime} regime | held {hold_minutes:.0f}min",
        f"P&L: ${pnl_usd:+.2f} gross | ${net_after_fee:+.2f} net | fee ${fee_usd:.2f} ({fee_pct_of_move:.0f}% of move)",
        f"ACTIVE SIGNALS ({len(active)}): {', '.join(active) if active else 'none'}",
        f"ABSENT KEY SIGNALS: {', '.join(inactive_key) if inactive_key else 'none'}",
        f"AGENTS: BUY={buy_agents} HOLD={hold_agents} SELL={sell_agents}",
        f"EXIT: {exit_reason[:120]}",
    ]

    # Add interpretation
    if won:
        if len(active) >= 4:
            lines.append(
                f"PATTERN: Multi-signal confluence in {regime} regime → confirmed edge"
            )
        if "supertrend_bullish" in active and "wavetrend_cross" in active:
            lines.append(
                "PATTERN: SuperTrend + WaveTrend combo → strong trend-momentum confluence"
            )
        if "squeeze_fired" in active and "rv_expansion" in active:
            lines.append("PATTERN: Squeeze + vol expansion → textbook breakout setup")
    else:
        if fee_pct_of_move > 50:
            lines.append(
                "FAILURE: Fees consumed > 50% of gross move — setup too small to trade"
            )
        if hold_minutes < 5:
            lines.append(
                "FAILURE: Very short hold — likely choppy entry or premature exit"
            )
        if not active:
            lines.append("FAILURE: No signals active at entry — should not have traded")
        if regime == "ranging" and "supertrend_bullish" in active:
            lines.append(
                "WARNING: SuperTrend in ranging regime — trend signal unreliable"
            )

    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────


def analyze_closed_trade(
    symbol: str,
    strategy: str,
    entry_price: float,
    exit_price: float,
    qty: float,
    fee_usd: float,
    entry_ts: str,
    exit_ts: str,
    exit_reason: str,
    market_data_at_entry: dict,
    agent_votes: Optional[dict] = None,
    source: str = "paper",
    paper: bool = True,
    trade_ref: str = "",
    mae_pct: float = 0,
    mfe_pct: float = 0,
    exit_type: str = "unknown",
    ml_p_win: float = 0,
    super_score: float = 0,
    composite_score: float = 0,
    # v14.0: lineage / integrity fields
    close_order_id: str = "",
    entry_order_id: str = None,
    feature_snapshot_id: int = None,
) -> dict:
    """
    Full post-trade attribution analysis. Call this immediately after every trade close.

    Args:
        market_data_at_entry: The market_data dict from _build_market_data() at entry time.
                              If unavailable, pass the closest available snapshot.
        agent_votes: {'agent_name': 'BUY'|'HOLD'|'SELL'} from debate_result.

    Returns a dict with: won, pnl_usd, net_pnl, lesson, signals, regime
    """
    md = market_data_at_entry or {}
    agent_votes = agent_votes or {}

    # Compute P&L
    pnl_usd = (exit_price - entry_price) * qty
    pnl_pct = (exit_price - entry_price) / entry_price if entry_price > 0 else 0
    net_pnl = pnl_usd - fee_usd
    won = net_pnl > 0

    # Regime
    regime = str(md.get("regime", "unknown")).lower()

    # Hold time
    hold_minutes = 0.0
    try:
        t0 = datetime.fromisoformat(entry_ts)
        t1 = datetime.fromisoformat(exit_ts) if exit_ts else datetime.now(timezone.utc)
        if not t0.tzinfo:
            t0 = t0.replace(tzinfo=timezone.utc)
        if not t1.tzinfo:
            t1 = t1.replace(tzinfo=timezone.utc)
        hold_minutes = (t1 - t0).total_seconds() / 60
    except Exception:
        pass

    # Extract signals
    signals = extract_signals_from_market_data(md)

    # Conviction score at entry (if available)
    conviction = float(md.get("conviction_score", 0) or 0)

    # Generate lesson
    lesson = _generate_lesson(
        symbol=symbol,
        regime=regime,
        signals=signals,
        won=won,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        fee_usd=fee_usd,
        exit_reason=exit_reason,
        hold_minutes=hold_minutes,
        agent_votes=agent_votes,
    )

    # v14.0: Compute integrity tier and write record before attribution update.
    # Fail-closed: any missing lineage or suspect source → at most 'suspect'.
    _integrity_tier = "suspect"
    _lineage_complete = False
    _lineage_notes = []

    try:
        from logging_db.trade_logger import log_trade_integrity, get_integrity_tier

        _src_lower = (source or "").lower()
        _EXCLUDED_SOURCES = (
            "contaminated",
            "synthetic",
            "replay",
            "bootstrap",
            "backtest",
        )
        if any(tag in _src_lower for tag in _EXCLUDED_SOURCES):
            _integrity_tier = "excluded"
            _lineage_notes.append(f"source_excluded:{source}")
        elif not entry_order_id:
            _integrity_tier = "suspect"
            _lineage_notes.append("missing_entry_order_id")
        elif int(feature_snapshot_id or 0) <= 0:
            _integrity_tier = "suspect"
            _lineage_notes.append("missing_feature_snapshot")
        else:
            _integrity_tier = "verified"
            _lineage_complete = True

        # Write integrity record if not already present
        if close_order_id:
            existing = get_integrity_tier(close_order_id)
            if existing == "suspect" and close_order_id:  # not yet set by backfill
                log_trade_integrity(
                    close_order_id=close_order_id,
                    tier=_integrity_tier,
                    reason="; ".join(_lineage_notes) or "ok",
                    source_check="post_trade_analyzer",
                )
    except Exception as _ie:
        _integrity_tier = "suspect"
        _lineage_notes.append(f"integrity_error:{_ie}")

    # Record attribution (updates signal_stats + Bayesian weights)
    # Bayesian updates are blocked for quarantined/excluded trades (see signal_performance.py).
    attr_id = record_trade_attribution(
        symbol=symbol,
        strategy=strategy,
        regime=regime,
        signals=signals,
        won=won,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        fee_usd=fee_usd,
        conviction=conviction,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_ts=entry_ts,
        exit_ts=exit_ts or datetime.now(timezone.utc).isoformat(),
        exit_reason=exit_reason,
        hold_minutes=hold_minutes,
        source=source,
        paper=paper,
        trade_ref=trade_ref,
        lesson=lesson,
        mae_pct=mae_pct,
        mfe_pct=mfe_pct,
        exit_type=exit_type,
        ml_p_win=ml_p_win,
        super_score=super_score,
        composite_score=composite_score,
        entry_order_id=entry_order_id,
        feature_snapshot_id=feature_snapshot_id,
        lineage_complete=_lineage_complete,
        lineage_notes="; ".join(_lineage_notes) if _lineage_notes else None,
        integrity_tier=_integrity_tier,
        candidate_id=int(md.get("candidate_id") or 0),
        scan_id=str(md.get("scan_id") or ""),
        raw_scanner_symbol=str(md.get("raw_scanner_symbol") or ""),
        base_asset=str(md.get("base_asset") or symbol),
        executed_symbol=str(md.get("executed_symbol") or symbol),
        route_type=str(md.get("route_type") or md.get("execution_route") or ""),
        setup_family=str(md.get("setup_family") or ""),
        setup_score=float(md.get("setup_score") or 0.0),
        tv_profile_name=str(md.get("tv_profile_name") or ""),
        tv_signal_age_sec=float(md.get("tv_signal_age_sec") or 0.0),
        tv_htf_bias=str(md.get("tv_htf_bias") or ""),
        tv_veto_state=str(md.get("tv_veto_state") or ""),
        reconstructed=bool(md.get("reconstructed")),
    )

    # Update agent accuracy
    if agent_votes:
        record_agent_votes(agent_votes, regime, won)
        record_agent_votes(agent_votes, "any", won)  # also update global accuracy

    result = {
        "attr_id": attr_id,
        "won": won,
        "pnl_usd": pnl_usd,
        "net_pnl": net_pnl,
        "pnl_pct": pnl_pct,
        "fee_usd": fee_usd,
        "regime": regime,
        "signals": signals,
        "active_signals": [s for s, v in signals.items() if v],
        "hold_minutes": hold_minutes,
        "lesson": lesson,
        "conviction": conviction,
    }

    print(
        f"[learning] {'✅' if won else '❌'} {symbol} attributed | "
        f"regime={regime} | {len(result['active_signals'])} signals | "
        f"net ${net_pnl:+.2f} | {exit_reason[:60]}"
    )

    # ── Tax lot tracking ───────────────────────────────────────────────────────
    try:
        from learning.tax_tracker import record_tax_lot

        # Map strategy name to asset class for tax treatment
        asset_class_map = {
            "crypto": "crypto",
            "crypto_macd": "crypto",
            "mean_reversion": "crypto",
            "equity": "equity",
            "equity_momentum": "equity",
            "futures": "futures",
            "futures_scalper": "futures",
            "perp": "perp",
        }
        strat_lower = strategy.lower()
        if "perp" in strat_lower:
            asset_class = "perp"
        elif "futures" in strat_lower:
            asset_class = "futures"
        elif "equity" in strat_lower:
            asset_class = "equity"
        else:
            asset_class = asset_class_map.get(strat_lower.split("_")[0], "crypto")
        record_tax_lot(
            symbol=symbol,
            strategy=strategy,
            asset_class=asset_class,
            entry_ts=entry_ts,
            exit_ts=exit_ts or datetime.now(timezone.utc).isoformat(),
            entry_price=entry_price,
            exit_price=exit_price,
            qty=qty,
            fees_usd=fee_usd,
            paper=paper,
        )
    except ModuleNotFoundError:
        pass
    except Exception as _te:
        print(f"[tax_tracker] record error: {_te}")

    return result
