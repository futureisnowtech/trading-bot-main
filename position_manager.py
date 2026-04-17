"""
position_manager.py — Kelly-based position sizing + 6-priority exit stack.

Sizing:
  kelly_fraction = (win_rate * avg_win - loss_rate * avg_loss) / avg_win
  conservative_kelly = kelly_fraction × 0.33 (→ 0.40 after 50 trades → 0.50 after 100 trades w/ Sharpe>1)
  dollar_risk = account_balance × 0.02
  position_units = dollar_risk / (atr_7 × stop_multiplier)
  position_usd = position_units × current_price
  Apply: vol_regime → ml_score → fg → correlation → FINAL = min(result, account × 0.30)

Leverage schedule:
  Default 3x
  vol_regime=NORMAL AND ml_score>65: 4x
  vol_regime=LOW AND ml_score>75: 5x
  MAX 10x: ml_score>85 AND cascade_risk<20 AND vol_regime=LOW AND edge_score>0.70

6-Priority Exit Stack (higher = wins):
  1. Trailing stop — regime-aware activation and width; width further compresses as signal health fades
  2. Take profit scale-out — conviction + regime adaptive: 2–4R first cut (20–30%); 4.5–8R second cut
  3. Thesis score — current_signal_score < entry_signal_score × regime_fraction → close all (TRENDING=0.30, RANGING=0.15, HIGH_VOL=0.35, UNKNOWN=0.25)
  4. Hard stop — stop-market on exchange, never widened
  5. Risk forced exit — margin breach / drawdown / correlation
  6. Kill switch — balance < 75% of ACCOUNT_SIZE (e.g. $3,750 on a $5K account) / API errors / latency
"""

import logging
import time
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_RISK_PCT = 0.02  # 2% account risk per trade
_MAX_SINGLE_POSITION_PCT = 0.12  # 12% of account per position (scales with balance)
_MAX_DEPLOYED_PCT = 0.95  # 95% max total deployment
_MIN_NOTIONAL = 10.0  # $10 minimum

# Kelly ramp thresholds
_KELLY_RAMP = [
    (0, 0.33),  # < 50 trades: 1/3 Kelly
    (50, 0.40),  # 50+ trades: 40%
    (100, 0.50),  # 100+ trades with Sharpe > 1.0: half Kelly
]

_lock = threading.RLock()


def _get_kelly_fraction(account_balance: float, paper: bool = True) -> float:
    """
    Compute Kelly fraction from closed trade history.
    Falls back to conservative 0.33 if insufficient data.
    """
    try:
        from logging_db.trade_logger import get_logger

        db = get_logger()

        # Query ALL closed trades (won IS NOT NULL) — captures both LONG exits
        # (action='SELL') and SHORT exits (action='BUY').
        # Excludes entry legs (won IS NULL), force_test_close test trades,
        # and contaminated pre-v10 data so Kelly is computed on clean paper data only.
        rows = db.conn.execute(
            """
            SELECT pnl_usd, won FROM trades
            WHERE paper=? AND won IS NOT NULL
              AND source IN ('clean_paper_v10','live_v10')
              AND (notes IS NULL OR notes NOT LIKE '%force_test_close%')
            ORDER BY ts DESC LIMIT 200
        """,
            (1 if paper else 0,),
        ).fetchall()

        if len(rows) < 20:
            return 0.33

        pnls = [float(r[0]) for r in rows if r[0] is not None]
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p <= 0]

        if not wins or not losses:
            return 0.33

        win_rate = len(wins) / len(pnls)
        loss_rate = 1 - win_rate
        avg_win = np.mean(wins)
        avg_loss = np.mean(losses)

        kelly = (win_rate * avg_win - loss_rate * avg_loss) / (avg_win + 1e-9)
        kelly = max(0.0, kelly)

        # Apply ramp
        n = len(rows)
        fraction = 0.33
        for threshold, f in _KELLY_RAMP:
            if n >= threshold:
                fraction = f

        return round(kelly * fraction, 4)

    except Exception as e:
        logger.debug(f"[pos_mgr] kelly error: {e}")
        return 0.33


def _get_leverage(
    vol_regime: int, ml_score: float, cascade_risk: float, edge_score: float
) -> int:
    """
    Leverage schedule per spec.
    vol_regime: 1=compressing, 2=normal, 3=expanding
    """
    # MAX 10x: strict thresholds
    if ml_score > 85 and cascade_risk < 20 and vol_regime == 1 and edge_score > 0.70:
        return 10

    # 5x
    if vol_regime == 1 and ml_score > 75:
        return 5

    # 4x
    if vol_regime == 2 and ml_score > 65:
        return 4

    # Default
    return 3


def compute_position_size(
    account_balance: float,
    current_price: float,
    atr_7: float,
    stop_multiplier: float = 3.0,
    vol_regime: int = 2,
    ml_score: float = 50.0,
    fg_current: float = 50.0,
    composite_score: float = 65.0,
    correlation_penalty: float = 1.0,
    edge_score: float = 0.5,
    cascade_risk_score: float = 0,
    deployed_usd: float = 0.0,
    paper: bool = True,
) -> Dict:
    """
    Compute position size in USD and units.

    Returns:
        {
          'position_usd':    float,
          'position_units':  float,
          'leverage':        int,
          'stop_distance':   float (price distance),
          'stop_price_long': float,
          'stop_price_short':float,
          'kelly_fraction':  float,
          'risk_usd':        float,
          'capped_by':       str (what constrained the size),
        }
    """
    kelly_frac = _get_kelly_fraction(account_balance, paper)

    # Base dollar risk
    dollar_risk = account_balance * _RISK_PCT

    # Stop distance (ATR-based)
    stop_distance = atr_7 * stop_multiplier
    if stop_distance < 1e-9:
        stop_distance = current_price * 0.015  # 1.5% floor

    # Base position
    position_units = dollar_risk / (stop_distance + 1e-9)
    position_usd = position_units * current_price

    # ── Multiplier chain ─────────────────────────────────────────────────
    chain_mult = 1.0

    # 1. Vol regime
    if vol_regime == 1:  # compressing
        chain_mult *= 1.10
    elif vol_regime == 3:  # expanding
        chain_mult *= 0.80

    # 2. ML score (centered at 65, ±20% range)
    ml_mult = 0.8 + (ml_score / 100) * 0.4  # 0.80 at ml=0, 1.20 at ml=100
    chain_mult *= ml_mult

    # 3. Fear & Greed
    if fg_current > 75:
        chain_mult *= 0.85  # euphoria — reduce
    elif fg_current < 25:
        chain_mult *= 0.90  # extreme fear — slightly cautious
    else:
        chain_mult *= 1.0

    # 4. Correlation penalty (from scanner/pair_intelligence)
    chain_mult *= correlation_penalty

    # Apply chain
    position_usd *= chain_mult

    # Apply Kelly — this is where it actually gets used
    position_usd *= kelly_frac

    # ── Caps ─────────────────────────────────────────────────────────────
    capped_by = "chain"

    # Single position cap: 12% of account, scales with balance
    _max_single = account_balance * _MAX_SINGLE_POSITION_PCT
    if position_usd > _max_single:
        position_usd = _max_single
        capped_by = "max_single_position"

    # Total deployment cap: 95% of account
    remaining_capacity = account_balance * _MAX_DEPLOYED_PCT - deployed_usd
    if position_usd > remaining_capacity:
        position_usd = max(0, remaining_capacity)
        capped_by = "deployment_cap"

    # Minimum notional
    if position_usd < _MIN_NOTIONAL:
        position_usd = _MIN_NOTIONAL
        capped_by = "minimum_notional"

    # Recompute units
    position_units = position_usd / (current_price + 1e-9)

    # Leverage — when ML model is untrained (default 50.0), use composite_score as proxy
    # so high-conviction signals can access higher leverage tiers without a trained model.
    _ml_for_leverage = (
        composite_score if (ml_score == 50.0 and composite_score > 50.0) else ml_score
    )
    leverage = _get_leverage(
        vol_regime, _ml_for_leverage, cascade_risk_score, edge_score
    )

    return {
        "position_usd": round(position_usd, 2),
        "position_units": round(position_units, 6),
        "leverage": leverage,
        "stop_distance": round(stop_distance, 4),
        "stop_price_long": round(current_price - stop_distance, 4),
        "stop_price_short": round(current_price + stop_distance, 4),
        "kelly_fraction": kelly_frac,
        "risk_usd": round(dollar_risk, 2),
        "chain_multiplier": round(chain_mult, 4),
        "capped_by": capped_by,
    }


# ── 6-Priority Exit Stack ────────────────────────────────────────────────────


class ExitDecision:
    """Result of the exit stack evaluation."""

    __slots__ = [
        "should_exit",
        "priority",
        "exit_type",
        "reason",
        "partial_pct",
        "trail_atr_mult",
    ]

    def __init__(
        self,
        should_exit: bool,
        priority: int = 0,
        exit_type: str = "none",
        reason: str = "",
        partial_pct: float = 1.0,
        trail_atr_mult: Optional[float] = None,
    ):
        self.should_exit = should_exit
        self.priority = priority
        self.exit_type = exit_type
        self.reason = reason
        self.partial_pct = partial_pct  # fraction to close (1.0 = full)
        self.trail_atr_mult = (
            trail_atr_mult  # carries new trail mult for trail_compressed events
        )

    def __repr__(self):
        return (
            f"ExitDecision(exit={self.should_exit}, priority={self.priority}, "
            f"type={self.exit_type}, partial={self.partial_pct:.0%})"
        )


# ── Regime-aware trailing configuration ───────────────────────────────────────
#
# Philosophy: the trailing stop should MATCH the market's character, not fight it.
#   TRENDING:  Give the trend room. Wide trail (4.5×), late activation (1.5×ATR profit).
#              A sustained 4.5-ATR reversal is a real trend change, not noise.
#   RANGING:   The move is finite. Narrow trail (2.5×), activate early (1.0×ATR profit).
#              Mean-reversion needs to lock in the bounce before it fades.
#   HIGH_VOL:  More noise means more false pullbacks. Widest trail (5.5×), latest
#              activation (2.0×ATR) so random wicks don't shake out the position.
#   LOW_VOL:   Calm markets revert faster. Moderate trail (3.5×).
#   UNKNOWN:   Conservative default (4.0×, 1.25×).
#
_REGIME_TRAIL_CONFIG: dict = {
    "TRENDING_UP": (4.5, 1.5),
    "TRENDING_DOWN": (4.5, 1.5),
    "RANGING": (2.5, 1.0),
    "HIGH_VOL": (5.5, 2.0),
    "LOW_VOL": (3.5, 1.0),
    "UNKNOWN": (4.0, 1.25),
}


def _resolve_trail_config(regime: str) -> tuple:
    """Return (trail_atr_mult, activation_atr_mult) for the given regime."""
    return _REGIME_TRAIL_CONFIG.get(
        str(regime).upper() if regime else "UNKNOWN", (4.0, 1.25)
    )


def check_exits(
    position: Dict,
    current_price: float,
    current_features: Optional[Dict] = None,
    model_store=None,
    account_balance: float = 5000.0,
    total_deployed_usd: float = 0.0,
    margin_utilization_pct: float = 0.0,
    drawdown_pct: float = 0.0,
    kill_switch_triggered: bool = False,
) -> ExitDecision:
    """
    Run the 6-priority exit stack against a live position.

    Position dict must contain:
        entry_price, direction, entry_ts, entry_composite_score,
        peak_price (updated live), atr_at_entry, stop_price, take_profit_price,
        scale_33_done (bool), scale_66_done (bool), trailing_active (bool),
        trailing_stop_price

    Returns:
        ExitDecision — highest-priority exit that triggered, or no-exit.
    """
    entry = float(position.get("entry_price", current_price))
    direction = str(position.get("direction", "LONG")).upper()
    atr = float(position.get("atr_at_entry", current_price * 0.015))
    stop_p = float(position.get("stop_price", 0))
    peak_p = float(position.get("peak_price", entry))
    entry_score = float(position.get("entry_composite_score", 65.0))
    regime = str(position.get("regime", "UNKNOWN")).upper()
    _atr_pct = atr / entry if entry > 0 else 0.02

    is_long = direction == "LONG"

    # Trail config resolved once — used by activation, compression, and update paths.
    _trail_mult, _activation_atr = _resolve_trail_config(regime)

    # ── Priority 6: Kill switch ───────────────────────────────────────────
    if kill_switch_triggered:
        return ExitDecision(
            True, 6, "kill_switch", "Kill switch triggered — close all", 1.0
        )

    # ── Priority 5: Risk forced exit ──────────────────────────────────────
    # Kill threshold = 75% of configured account size (not hardcoded $10K architecture)
    try:
        from config import ACCOUNT_SIZE as _ACCT

        _kill_floor = float(_ACCT) * 0.75
    except Exception:
        _kill_floor = 7500.0
    if account_balance < _kill_floor:
        return ExitDecision(
            True,
            5,
            "risk_forced",
            f"Balance ${account_balance:.0f} below kill threshold ${_kill_floor:.0f}",
            1.0,
        )

    if margin_utilization_pct > 85:
        return ExitDecision(
            True,
            5,
            "risk_forced",
            f"Margin utilization {margin_utilization_pct:.0f}% > 85%",
            1.0,
        )

    if drawdown_pct > 15:
        return ExitDecision(
            True,
            5,
            "risk_forced",
            f"Drawdown {drawdown_pct:.1f}% > 15% emergency threshold",
            1.0,
        )

    # ── Priority 4: Hard stop ─────────────────────────────────────────────
    if stop_p > 0:
        if is_long and current_price <= stop_p:
            return ExitDecision(
                True,
                4,
                "hard_stop",
                f"Hard stop hit: {current_price:.8g} <= {stop_p:.8g}",
                1.0,
            )
        elif not is_long and current_price >= stop_p:
            return ExitDecision(
                True,
                4,
                "hard_stop",
                f"Hard stop hit: {current_price:.8g} >= {stop_p:.8g}",
                1.0,
            )

    # ── Priority 3: Thesis exit ───────────────────────────────────────────────
    # Tier 1 entries: exit when the specific setup conditions are no longer met.
    # Tier 2 entries: fall back to score comparison (current composite < entry × regime_frac).
    #
    # Minimum hold before thesis invalidation can fire — varies by setup type:
    #
    #   wae_explosion / squeeze_breakout (momentum):
    #     Signal fires on ONE bar then goes false as MACD histograms revert.
    #     The trade needs time for the impulse move to develop.
    #     Base: 2 hours. ATR-proportional floor applied on top.
    #
    #   ranging_mr_long / ranging_mr_short (mean-reversion):
    #     Mean-reversion in ranging markets can take 2–4 hours to reach target.
    #     A composite dip at 60 minutes is normal noise, not thesis failure.
    #     Base: 90 minutes. ATR-proportional floor applied on top.
    #
    #   Default (Tier 2 score-based): 1 hour base.
    #
    # ATR-proportional floor (applied to ALL setups, both sides):
    #   More volatile instrument = more noise = more time before thesis can fire.
    #   Formula: atr_pct × scaling_factor, clamped to [floor, ceiling].
    #
    #   LONG scaling:  360,000 → at 1% ATR: 1h, 2%: 2h, 3%: 3h  (ceiling 6h)
    #   SHORT scaling: 720,000 → at 1% ATR: 2h, 2%: 4h, 3%: 6h  (ceiling 12h)
    #   (Shorts historically need twice as long to develop — empirically validated.)
    _entry_setup = position.get("entry_setup", "")
    _MOMENTUM_SETUPS = {
        "wae_explosion",
        "wae_explosion_short",
        "squeeze_breakout",
        "squeeze_breakout_short",
    }
    _MR_SETUPS = {"ranging_mr_long", "ranging_mr_short"}
    if _entry_setup in _MOMENTUM_SETUPS:
        _min_hold_secs = 7200  # 2h base for momentum (single-bar signal needs room)
    elif _entry_setup in _MR_SETUPS:
        _min_hold_secs = 5400  # 90min base for mean-reversion (up from 45min)
    else:
        _min_hold_secs = 3600  # 1h default

    # ATR-proportional floor — applied universally so volatile instruments get
    # proportionally more time before thesis can fire.
    if is_long:
        _dynamic_hold = int(_atr_pct * 360_000)
        _dynamic_hold = max(3600, min(_dynamic_hold, 21600))  # 1h–6h
    else:
        _dynamic_hold = int(_atr_pct * 720_000)
        _dynamic_hold = max(7200, min(_dynamic_hold, 43200))  # 2h–12h
    _min_hold_secs = max(_min_hold_secs, _dynamic_hold)

    _hold_elapsed = time.time() - float(position.get("entry_ts", 0))
    _thesis_eligible = _hold_elapsed >= _min_hold_secs

    if current_features is not None:
        entry_setup = _entry_setup  # already read above for hold-time calculation
        if entry_setup and _thesis_eligible:
            try:
                from signal_engine import check_setup_still_valid

                still_valid, reason = check_setup_still_valid(
                    entry_setup, current_features, direction
                )
                if still_valid is False:
                    return ExitDecision(True, 3, "thesis_invalidated", reason, 1.0)
            except Exception as e:
                logger.debug(f"[pos_mgr] setup validity check error: {e}")
        elif entry_score > 0 and _thesis_eligible:
            try:
                from signal_engine import thesis_still_valid

                # `regime` already read at top of function — no duplicate fetch.
                valid, current_score, reason = thesis_still_valid(
                    entry_score, current_features, direction, regime, model_store
                )
                if not valid:
                    return ExitDecision(True, 3, "thesis_degraded", reason, 1.0)
            except Exception as e:
                logger.debug(f"[pos_mgr] thesis check error: {e}")

    # ── Priority 2: Conviction-adaptive take-profit scale-out ─────────────
    #
    # FIX #1 — R denominator was atr×1.5, but actual stop is atr×stop_multiplier
    # (3.0 in v10_runner).  Using the hardcoded 1.5 made the code's "2R" fire at
    # real 1:1 R:R — the first 33% sold the moment the trade covered its own risk.
    # Now we use the actual stop distance stored in the position dict.
    #
    # FIX #2 — Flat 33%/33% scale with fixed 2R/3.5R targets treats every trade
    # identically regardless of how confident the bot was or what the market is
    # doing.  High conviction in a trending regime should run further before scaling.
    # Low conviction or a ranging entry should lock in profits sooner.
    #
    # Combined factor blends entry_composite_score conviction (60%) with regime
    # extension potential (40%):
    #   _factor = 0.0 → lowest conviction + ranging  → first scale 30% @ 2.0R, second @ 4.5R
    #   _factor = 1.0 → highest conviction + trending → first scale 20% @ 4.0R, second @ 8.0R
    #
    # Second cut is always 25%.  Remainder (~50-55%) trails to the stop.
    _actual_risk = abs(entry - stop_p) if stop_p > 0 else atr * 3.0
    if is_long:
        r_gained = (current_price - entry) / (_actual_risk + 1e-9)
    else:
        r_gained = (entry - current_price) / (_actual_risk + 1e-9)

    # Conviction factor: 0.0 at minimum threshold (58), 1.0 at high confidence (88+)
    _conv = min(1.0, max(0.0, (entry_score - 58.0) / 30.0))
    # Regime extension: trending gives more runway; ranging takes profits sooner
    _regime_ext = {
        "TRENDING_UP": 1.0,
        "TRENDING_DOWN": 1.0,
        "RANGING": 0.0,
        "HIGH_VOL": 0.4,
        "LOW_VOL": 0.6,
        "UNKNOWN": 0.5,
    }.get(regime, 0.5)
    _factor = _conv * 0.6 + _regime_ext * 0.4

    _first_r = 2.0 + 2.0 * _factor  # 2.0R (flat) → 4.0R (high-conviction trending)
    _second_r = 4.5 + 3.5 * _factor  # 4.5R        → 8.0R
    _first_pct = 0.30 - 0.10 * _factor  # 30% slice  → 20%

    scale_33_done = bool(position.get("scale_33_done", False))
    scale_66_done = bool(position.get("scale_66_done", False))

    if not scale_33_done and r_gained >= _first_r:
        return ExitDecision(
            True,
            2,
            "scale_out_33",
            f"{_first_r:.1f}R reached (r={r_gained:.2f}) — close {_first_pct:.0%} "
            f"[conv={_conv:.2f} regime={regime}]",
            round(_first_pct, 3),
        )

    if scale_33_done and not scale_66_done and r_gained >= _second_r:
        return ExitDecision(
            True,
            2,
            "scale_out_66",
            f"{_second_r:.1f}R reached (r={r_gained:.2f}) — close 25% "
            f"[conv={_conv:.2f} regime={regime}]",
            0.25,
        )

    # ── Priority 1: Regime-aware trailing stop ────────────────────────────
    #
    # FIX #3 — Activation and trail width are now regime-driven rather than
    # hardcoded.  The market's own character governs how much room the trade gets:
    #   TRENDING:   wide trail (4.5×ATR), late activation (1.5×ATR profit)
    #   RANGING:    narrow trail (2.5×ATR), early activation (1.0×ATR profit)
    #   HIGH_VOL:   widest trail (5.5×ATR), latest activation (2.0×ATR profit)
    #   LOW_VOL:    moderate trail (3.5×ATR)
    #
    # FIX #5 — Signal-health trail compression.  When trailing is active AND
    # current_features are available, the bot computes how far its own composite
    # score has degraded toward the thesis exit floor.  As signal health fades
    # (but before a full thesis exit fires), the trailing distance compresses
    # proportionally — the bot tightens its own leash when its conviction is
    # fading, protecting accumulated profits without hard-coding an exit rule.
    #
    # signal_health = (current_score − thesis_floor) / (entry_score − thesis_floor)
    #   1.0 → signal as strong as entry → nominal trail width
    #   0.65 → compression begins → trail shrinks toward 50% of nominal
    #   0.0  → at thesis floor → thesis exit fires (normal path)
    #
    trailing_active = bool(position.get("trailing_active", False))
    trailing_stop = float(position.get("trailing_stop_price", 0))

    if not trailing_active:
        # Regime-aware activation threshold
        if is_long and current_price >= entry + atr * _activation_atr:
            new_trail = current_price - atr * _trail_mult
            return ExitDecision(
                False,
                0,
                "trailing_activated",
                f"Trail activated at {new_trail:.4f} "
                f"[{regime}: {_trail_mult}×ATR, activation={_activation_atr}×ATR]",
                0.0,
                trail_atr_mult=_trail_mult,
            )
        elif not is_long and current_price <= entry - atr * _activation_atr:
            new_trail = current_price + atr * _trail_mult
            return ExitDecision(
                False,
                0,
                "trailing_activated",
                f"Trail activated at {new_trail:.4f} "
                f"[{regime}: {_trail_mult}×ATR, activation={_activation_atr}×ATR]",
                0.0,
                trail_atr_mult=_trail_mult,
            )

    # Signal-health trail compression (only when trailing is active and features present)
    if (
        trailing_active
        and trailing_stop > 0
        and current_features is not None
        and entry_score > 0
    ):
        try:
            from signal_engine import thesis_still_valid

            _regime_key = regime.split("_")[0] if "_" in regime else regime
            _floor_frac = {
                "TRENDING": 0.30,
                "RANGING": 0.15,
                "HIGH_VOL": 0.35,
                "UNKNOWN": 0.25,
            }.get(_regime_key, 0.25)
            _thesis_floor = entry_score * _floor_frac
            _, _cur_score, _ = thesis_still_valid(
                entry_score, current_features, direction, regime, model_store
            )
            _health = max(
                0.0,
                min(
                    1.0,
                    (_cur_score - _thesis_floor)
                    / max(entry_score - _thesis_floor, 1.0),
                ),
            )
            # Compress trail when signal health drops below 65%
            if _health < 0.65:
                _nominal_mult = float(position.get("trail_atr_mult", _trail_mult))
                _compression = 0.50 + 0.50 * (_health / 0.65)
                _compressed_mult = max(
                    _nominal_mult * 0.50, _nominal_mult * _compression
                )
                _peak_now = float(position.get("peak_price", current_price))
                if is_long:
                    _new_tight = _peak_now - atr * _compressed_mult
                    if _new_tight > current_price:
                        # Compression drives stop above price — exit immediately
                        return ExitDecision(
                            True,
                            1,
                            "trailing_stop",
                            f"Signal-health compression ({_health:.0%}) drove trail above price",
                            1.0,
                        )
                    if _new_tight > trailing_stop:
                        return ExitDecision(
                            False,
                            0,
                            "trail_compressed",
                            f"Signal health {_health:.0%} — trail {_nominal_mult:.1f}→{_compressed_mult:.1f}×ATR",
                            0.0,
                            trail_atr_mult=_compressed_mult,
                        )
                else:
                    _new_tight = _peak_now + atr * _compressed_mult
                    if _new_tight < current_price:
                        return ExitDecision(
                            True,
                            1,
                            "trailing_stop",
                            f"Signal-health compression ({_health:.0%}) drove trail below price",
                            1.0,
                        )
                    if _new_tight < trailing_stop:
                        return ExitDecision(
                            False,
                            0,
                            "trail_compressed",
                            f"Signal health {_health:.0%} — trail {_nominal_mult:.1f}→{_compressed_mult:.1f}×ATR",
                            0.0,
                            trail_atr_mult=_compressed_mult,
                        )
        except Exception as e:
            logger.debug(f"[pos_mgr] signal-health compression error: {e}")

    if trailing_active and trailing_stop > 0:
        if is_long and current_price <= trailing_stop:
            return ExitDecision(
                True,
                1,
                "trailing_stop",
                f"Trailing stop hit: {current_price:.4f} <= {trailing_stop:.4f}",
                1.0,
            )
        elif not is_long and current_price >= trailing_stop:
            return ExitDecision(
                True,
                1,
                "trailing_stop",
                f"Trailing stop hit: {current_price:.4f} >= {trailing_stop:.4f}",
                1.0,
            )

    return ExitDecision(False, 0, "none", "No exit signal")


def update_trailing_stop(position: Dict, current_price: float) -> Dict:
    """
    Update trailing stop price based on new peak.
    Call every price tick for open positions with trailing_active=True.
    Modifies position dict in-place and returns it.

    Uses trail_atr_mult stored in the position dict (set by activate_trailing or
    trail_compressed events).  Falls back to 4.0 if not present.
    """
    if not position.get("trailing_active", False):
        return position

    direction = str(position.get("direction", "LONG")).upper()
    atr = float(position.get("atr_at_entry", current_price * 0.015))
    current_trail = float(position.get("trailing_stop_price", 0))
    trail_mult = float(position.get("trail_atr_mult", 4.0))

    if direction == "LONG":
        peak = float(position.get("peak_price", current_price))
        new_peak = max(peak, current_price)
        new_trail = new_peak - atr * trail_mult

        if new_trail > current_trail:
            position["trailing_stop_price"] = round(new_trail, 4)
            position["peak_price"] = round(new_peak, 4)

    else:  # SHORT
        trough = float(
            position.get("peak_price", current_price)
        )  # reuse peak as trough for short
        new_trough = min(trough, current_price)
        new_trail = new_trough + atr * trail_mult

        if new_trail < current_trail or current_trail == 0:
            position["trailing_stop_price"] = round(new_trail, 4)
            position["peak_price"] = round(new_trough, 4)

    return position


def activate_trailing(position: Dict, current_price: float) -> Dict:
    """
    Activate trailing stop when the regime-aware activation threshold is reached.
    Trail width is determined by the position's regime (set at entry time).
    Stores trail_atr_mult in the position dict so update_trailing_stop and
    trail_compressed handlers use the same multiplier.
    Returns updated position dict.
    """
    direction = str(position.get("direction", "LONG")).upper()
    atr = float(position.get("atr_at_entry", current_price * 0.015))
    regime = str(position.get("regime", "UNKNOWN")).upper()

    trail_mult, _activation_atr = _resolve_trail_config(regime)

    if direction == "LONG":
        trail_price = current_price - atr * trail_mult
    else:
        trail_price = current_price + atr * trail_mult

    position["trailing_active"] = True
    position["trailing_stop_price"] = round(trail_price, 4)
    position["trail_atr_mult"] = trail_mult
    position["peak_price"] = round(current_price, 4)
    position["trailing_activated_at"] = time.time()

    return position
