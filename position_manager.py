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
  1. Trailing stop — activates after 1x ATR in favor, trails at 1.5x ATR from peak
  2. Take profit scale-out — 2R → 33%; 3.5R → 33%; remainder trails
  3. Thesis score — current_signal_score < entry_signal_score × 0.45 → close all
  4. Hard stop — stop-market on exchange, never widened
  5. Risk forced exit — margin breach / drawdown / correlation
  6. Kill switch — balance < $7,500 / API errors / latency
"""

import logging
import time
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_RISK_PCT = 0.02              # 2% account risk per trade
_MAX_SINGLE_POSITION_PCT = 0.30   # 30% of account max per position
_MAX_DEPLOYED_PCT = 0.80          # 80% max total deployment
_MIN_NOTIONAL = 100.0             # $100 minimum

# Kelly ramp thresholds
_KELLY_RAMP = [
    (0,   0.33),   # < 50 trades: 1/3 Kelly
    (50,  0.40),   # 50+ trades: 40%
    (100, 0.50),   # 100+ trades with Sharpe > 1.0: half Kelly
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

        rows = db.conn.execute("""
            SELECT pnl_usd, won FROM trades
            WHERE paper=? AND action='SELL'
            ORDER BY ts DESC LIMIT 200
        """, (1 if paper else 0,)).fetchall()

        if len(rows) < 20:
            return 0.33

        pnls  = [float(r[0]) for r in rows]
        wins  = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p <= 0]

        if not wins or not losses:
            return 0.33

        win_rate  = len(wins) / len(rows)
        loss_rate = 1 - win_rate
        avg_win   = np.mean(wins)
        avg_loss  = np.mean(losses)

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
        logger.debug(f'[pos_mgr] kelly error: {e}')
        return 0.33


def _get_leverage(vol_regime: int, ml_score: float,
                   cascade_risk: float, edge_score: float) -> int:
    """
    Leverage schedule per spec.
    vol_regime: 1=compressing, 2=normal, 3=expanding
    """
    # MAX 10x: strict thresholds
    if (ml_score > 85 and cascade_risk < 20 and
            vol_regime == 1 and edge_score > 0.70):
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
    stop_multiplier: float = 1.5,
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
        stop_distance = current_price * 0.015   # 1.5% floor

    # Base position
    position_units = dollar_risk / (stop_distance + 1e-9)
    position_usd   = position_units * current_price

    # ── Multiplier chain ─────────────────────────────────────────────────
    chain_mult = 1.0

    # 1. Vol regime
    if vol_regime == 1:    # compressing
        chain_mult *= 1.10
    elif vol_regime == 3:  # expanding
        chain_mult *= 0.80

    # 2. ML score (centered at 65, ±20% range)
    ml_mult = 0.8 + (ml_score / 100) * 0.4   # 0.80 at ml=0, 1.20 at ml=100
    chain_mult *= ml_mult

    # 3. Fear & Greed
    if fg_current > 75:
        chain_mult *= 0.85   # euphoria — reduce
    elif fg_current < 25:
        chain_mult *= 0.90   # extreme fear — slightly cautious
    else:
        chain_mult *= 1.0

    # 4. Correlation penalty (from scanner/pair_intelligence)
    chain_mult *= correlation_penalty

    # Apply chain
    position_usd *= chain_mult

    # ── Caps ─────────────────────────────────────────────────────────────
    capped_by = 'chain'

    # Single position max: 30% of account
    max_single = account_balance * _MAX_SINGLE_POSITION_PCT
    if position_usd > max_single:
        position_usd = max_single
        capped_by = 'max_single_position'

    # Total deployment cap: 80% of account
    remaining_capacity = account_balance * _MAX_DEPLOYED_PCT - deployed_usd
    if position_usd > remaining_capacity:
        position_usd = max(0, remaining_capacity)
        capped_by = 'deployment_cap'

    # Minimum notional
    if position_usd < _MIN_NOTIONAL:
        position_usd = _MIN_NOTIONAL
        capped_by = 'minimum_notional'

    # Recompute units
    position_units = position_usd / (current_price + 1e-9)

    # Leverage
    leverage = _get_leverage(vol_regime, ml_score, cascade_risk_score, edge_score)

    return {
        'position_usd':     round(position_usd, 2),
        'position_units':   round(position_units, 6),
        'leverage':         leverage,
        'stop_distance':    round(stop_distance, 4),
        'stop_price_long':  round(current_price - stop_distance, 4),
        'stop_price_short': round(current_price + stop_distance, 4),
        'kelly_fraction':   kelly_frac,
        'risk_usd':         round(dollar_risk, 2),
        'chain_multiplier': round(chain_mult, 4),
        'capped_by':        capped_by,
    }


# ── 6-Priority Exit Stack ────────────────────────────────────────────────────

class ExitDecision:
    """Result of the exit stack evaluation."""
    __slots__ = ['should_exit', 'priority', 'exit_type', 'reason', 'partial_pct']

    def __init__(self, should_exit: bool, priority: int = 0,
                 exit_type: str = 'none', reason: str = '', partial_pct: float = 1.0):
        self.should_exit  = should_exit
        self.priority     = priority
        self.exit_type    = exit_type
        self.reason       = reason
        self.partial_pct  = partial_pct   # fraction to close (1.0 = full)

    def __repr__(self):
        return (f'ExitDecision(exit={self.should_exit}, priority={self.priority}, '
                f'type={self.exit_type}, partial={self.partial_pct:.0%})')


def check_exits(
    position: Dict,
    current_price: float,
    current_features: Optional[Dict] = None,
    model_store=None,
    account_balance: float = 10000.0,
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
    entry     = float(position.get('entry_price', current_price))
    direction = str(position.get('direction', 'LONG')).upper()
    atr       = float(position.get('atr_at_entry', current_price * 0.015))
    stop_p    = float(position.get('stop_price', 0))
    peak_p    = float(position.get('peak_price', entry))
    entry_score = float(position.get('entry_composite_score', 65.0))

    is_long = direction == 'LONG'

    # ── Priority 6: Kill switch ───────────────────────────────────────────
    if kill_switch_triggered:
        return ExitDecision(True, 6, 'kill_switch',
                            'Kill switch triggered — close all', 1.0)

    # ── Priority 5: Risk forced exit ──────────────────────────────────────
    if account_balance < 7500:
        return ExitDecision(True, 5, 'risk_forced',
                            f'Balance ${account_balance:.0f} below kill threshold $7,500', 1.0)

    if margin_utilization_pct > 85:
        return ExitDecision(True, 5, 'risk_forced',
                            f'Margin utilization {margin_utilization_pct:.0f}% > 85%', 1.0)

    if drawdown_pct > 15:
        return ExitDecision(True, 5, 'risk_forced',
                            f'Drawdown {drawdown_pct:.1f}% > 15% emergency threshold', 1.0)

    # ── Priority 4: Hard stop ─────────────────────────────────────────────
    if stop_p > 0:
        if is_long and current_price <= stop_p:
            return ExitDecision(True, 4, 'hard_stop',
                                f'Hard stop hit: {current_price:.4f} <= {stop_p:.4f}', 1.0)
        elif not is_long and current_price >= stop_p:
            return ExitDecision(True, 4, 'hard_stop',
                                f'Hard stop hit: {current_price:.4f} >= {stop_p:.4f}', 1.0)

    # ── Priority 3: Thesis score ──────────────────────────────────────────
    if current_features is not None and entry_score > 0:
        try:
            from signal_engine import thesis_still_valid
            regime = position.get('regime', 'UNKNOWN')
            valid, current_score, reason = thesis_still_valid(
                entry_score, current_features, direction, regime, model_store
            )
            if not valid:
                return ExitDecision(True, 3, 'thesis_degraded', reason, 1.0)
        except Exception as e:
            logger.debug(f'[pos_mgr] thesis check error: {e}')

    # ── Priority 2: Take profit scale-out ────────────────────────────────
    if is_long:
        r_gained = (current_price - entry) / (atr * 1.5 + 1e-9)
    else:
        r_gained = (entry - current_price) / (atr * 1.5 + 1e-9)

    scale_33_done = bool(position.get('scale_33_done', False))
    scale_66_done = bool(position.get('scale_66_done', False))

    if not scale_33_done and r_gained >= 2.0:
        return ExitDecision(True, 2, 'scale_out_33',
                            f'2R reached (r={r_gained:.2f}) — close 33%', 0.33)

    if scale_33_done and not scale_66_done and r_gained >= 3.5:
        return ExitDecision(True, 2, 'scale_out_66',
                            f'3.5R reached (r={r_gained:.2f}) — close next 33%', 0.33)

    # ── Priority 1: Trailing stop ─────────────────────────────────────────
    trailing_active = bool(position.get('trailing_active', False))
    trailing_stop   = float(position.get('trailing_stop_price', 0))

    # Activate trailing after 1x ATR profit
    if not trailing_active:
        if is_long and current_price >= entry + atr:
            new_trail = current_price - atr * 1.5
            return ExitDecision(False, 0, 'trailing_activated',
                                f'Trailing stop activated at {new_trail:.4f}', 0.0)
        elif not is_long and current_price <= entry - atr:
            new_trail = current_price + atr * 1.5
            return ExitDecision(False, 0, 'trailing_activated',
                                f'Trailing stop activated at {new_trail:.4f}', 0.0)

    if trailing_active and trailing_stop > 0:
        if is_long and current_price <= trailing_stop:
            return ExitDecision(True, 1, 'trailing_stop',
                                f'Trailing stop hit: {current_price:.4f} <= {trailing_stop:.4f}', 1.0)
        elif not is_long and current_price >= trailing_stop:
            return ExitDecision(True, 1, 'trailing_stop',
                                f'Trailing stop hit: {current_price:.4f} >= {trailing_stop:.4f}', 1.0)

    return ExitDecision(False, 0, 'none', 'No exit signal')


def update_trailing_stop(position: Dict, current_price: float) -> Dict:
    """
    Update trailing stop price based on new peak.
    Call every price tick for open positions with trailing_active=True.
    Modifies position dict in-place and returns it.
    """
    if not position.get('trailing_active', False):
        return position

    direction = str(position.get('direction', 'LONG')).upper()
    atr       = float(position.get('atr_at_entry', current_price * 0.015))
    current_trail = float(position.get('trailing_stop_price', 0))

    if direction == 'LONG':
        peak = float(position.get('peak_price', current_price))
        new_peak = max(peak, current_price)
        new_trail = new_peak - atr * 1.5

        if new_trail > current_trail:
            position['trailing_stop_price'] = round(new_trail, 4)
            position['peak_price'] = round(new_peak, 4)

    else:   # SHORT
        trough = float(position.get('peak_price', current_price))   # reuse peak as trough for short
        new_trough = min(trough, current_price)
        new_trail = new_trough + atr * 1.5

        if new_trail < current_trail or current_trail == 0:
            position['trailing_stop_price'] = round(new_trail, 4)
            position['peak_price'] = round(new_trough, 4)

    return position


def activate_trailing(position: Dict, current_price: float) -> Dict:
    """
    Activate trailing stop when 1x ATR profit is reached.
    Returns updated position dict.
    """
    direction = str(position.get('direction', 'LONG')).upper()
    entry     = float(position.get('entry_price', current_price))
    atr       = float(position.get('atr_at_entry', current_price * 0.015))

    if direction == 'LONG':
        trail_price = current_price - atr * 1.5
    else:
        trail_price = current_price + atr * 1.5

    position['trailing_active']      = True
    position['trailing_stop_price']  = round(trail_price, 4)
    position['peak_price']           = round(current_price, 4)
    position['trailing_activated_at'] = time.time()

    return position
