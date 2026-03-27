"""
strategies/futures/mes_engine.py — MES Futures Signal Engine (Sprint 4)

Signal hierarchy:
  1. Opening Range Breakout with PULLBACK ENTRY (9:30–10:30am ET only)
     - Mark first 5-min candle H/L as opening range
     - Detect breakout above/below range
     - Wait for FIRST PULLBACK back to breakout level (do NOT enter on initial breakout)
     - Enter when price bounces off the level on reduced volume vs. breakout candle
     - Stop: below/above the breakout level (tight, objective)
     - Target: measured move = range size projected in breakout direction
  2. Close Auction Setup (3:00–3:30pm ET only)
     - Trade with the last-hour trend (institutional rebalancing)
     - 30-min HTF must align; ADX > 20

Hard rules — code-enforced, no debate or config can override:
  - Zero new entries 11:30am–2:30pm ET (midday dead zone)
  - Maximum 2 trades per session
  - Daily goal: +6 MES points (+$30) — stop when hit, no exceptions
  - Daily max loss: -5 MES points (-$25) — stop when hit, no exceptions
  - 1 MES contract only
  - 30-min HTF bias must align with trade direction

VIX regime:
  - VIX > 35 (HIGH): skip all new entries — too noisy for ORB patterns
  - VIX 25–35 (ELEVATED): 0.8× confidence discount
  - VIX < 20 (NORMAL/LOW): standard

Pre-market accumulation signal (proxy — no L2 data):
  - Consistent dip buying pre-market (lower wicks > 2× upper wicks in pre-market candles)
  - This is a directional bias boost, not an independent trade signal
"""
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Optional

import pandas as pd
import pytz

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import MARKET_TIMEZONE, FUTURES_DAILY_GOAL_PTS, FUTURES_DAILY_MAX_LOSS_PTS

# ─── Hard rules (these CANNOT be changed via config) ──────────────────────────
_MAX_TRADES_SESSION: int = 2
_MAX_CONTRACTS: int = 1
_DAILY_GOAL_PTS: float = 6.0      # +$30/day target
_DAILY_STOP_PTS: float = 5.0      # -$25/day max loss
_ORB_WINDOW_START: dtime = dtime(9, 30)
_ORB_WINDOW_END: dtime = dtime(10, 30)
_MIDDAY_DEAD_START: dtime = dtime(11, 30)
_MIDDAY_DEAD_END: dtime = dtime(14, 30)
_CLOSE_AUCTION_START: dtime = dtime(15, 0)
_CLOSE_AUCTION_END: dtime = dtime(15, 30)
_BREAKOUT_THRESHOLD: float = 0.001  # 0.1% above/below OR = confirmed breakout
_PULLBACK_ZONE: float = 0.002       # within 0.2% of breakout level = in pullback zone
_MIN_ADX: float = 18.0
_HIGH_VIX: float = 35.0     # was 25 — elevated market VIX; ORB still works up to ~35
_ELEVATED_VIX: float = 25.0  # was 20


# ─── Signal dataclass ─────────────────────────────────────────────────────────

@dataclass
class MESSignal:
    action: str            # 'LONG' | 'SHORT' | 'HOLD'
    signal_type: str       # 'orb_pullback' | 'close_auction' | 'hold'
    confidence: float      # 0.0 – 1.0
    reason: str
    entry_price: float
    stop_pts: float        # points below/above entry
    target_pts: float      # points above/below entry
    contracts: int = 1
    htf_bias: str = 'NEUTRAL'
    vix_regime: str = 'NORMAL'
    fired_signals: list = field(default_factory=list)


# ─── Engine ───────────────────────────────────────────────────────────────────

class MESEngine:
    """
    Stateful MES signal engine.
    Reset daily at premarket (reset_daily()).
    Opening range set at 9:35 ET (set_opening_range()).
    Evaluate each scan tick via evaluate().
    """

    def __init__(self):
        self._tz = pytz.timezone(MARKET_TIMEZONE)
        self._opening_range: dict = {}          # {'high': float, 'low': float, 'set': bool}
        self._breakout_state: dict = {}         # {'direction': str, 'level': float,
        #                                           'breakout_bar_volume': float,
        #                                           'pullback_in_progress': bool,
        #                                           'pullback_bar_count': int,
        #                                           'pullback_volumes': list[float],
        #                                           'last_bar_ts': Any}
        self._daily_pnl_pts: float = 0.0
        self._trades_today: int = 0
        self._goal_hit: bool = False
        self._htf_bias: dict = {'bias': 'NEUTRAL', 'strength': 0.0}
        self._vix: Optional[float] = None
        self._premarket_bias: str = 'NEUTRAL'   # from pre-market dip-buying proxy

    # ─── Public interface ─────────────────────────────────────────────────────

    def evaluate(self, price: float, df_5m: pd.DataFrame) -> MESSignal:
        """
        Main evaluation — call every FUTURES_SCAN_INTERVAL_SECONDS.
        df_5m: recent 5-minute bars with indicators (add_all_indicators applied).
        Returns a MESSignal with action LONG/SHORT/HOLD.
        """
        hold = lambda reason, stype='hold': MESSignal(
            action='HOLD', signal_type=stype, confidence=0.0,
            reason=reason, entry_price=price,
            stop_pts=0.0, target_pts=0.0, contracts=0,
            htf_bias=self._htf_bias.get('bias', 'NEUTRAL'),
            vix_regime=self._vix_regime(),
        )

        # ── Hard rule checks ─────────────────────────────────────────────────
        if self._goal_hit:
            return hold(f"Daily goal hit (+{_DAILY_GOAL_PTS} pts) — standing down")

        if self._daily_pnl_pts <= -_DAILY_STOP_PTS:
            return hold(f"Daily loss limit hit ({self._daily_pnl_pts:.1f} pts) — standing down")

        if self._trades_today >= _MAX_TRADES_SESSION:
            return hold(f"Max {_MAX_TRADES_SESSION} trades reached today")

        # ── VIX hard gate ─────────────────────────────────────────────────────
        vix = self._get_vix()
        if vix and vix > _HIGH_VIX:
            return hold(f"VIX={vix:.1f} > {_HIGH_VIX} — too noisy for ORB patterns")

        # ── Time-of-day routing ───────────────────────────────────────────────
        now_et = datetime.now(self._tz).time()

        if _MIDDAY_DEAD_START <= now_et < _MIDDAY_DEAD_END:
            return hold(f"Midday dead zone {_MIDDAY_DEAD_START}–{_MIDDAY_DEAD_END} ET — no new entries")

        if _CLOSE_AUCTION_START <= now_et < _CLOSE_AUCTION_END:
            return self._close_auction_signal(price, df_5m, vix)

        if _ORB_WINDOW_START <= now_et < _ORB_WINDOW_END:
            return self._orb_pullback_signal(price, df_5m, vix)

        # Outside all active windows
        return hold(f"Outside trading windows — time={now_et.strftime('%H:%M')} ET")

    def set_opening_range(self, high: float, low: float) -> None:
        """Called by scheduler at 9:35 ET with the first 5-min candle H/L."""
        self._opening_range = {'high': high, 'low': low, 'set': True}
        self._breakout_state = {}   # reset any stale breakout state
        print(f"[mes_engine] Opening range set: [{low:.2f} – {high:.2f}] "
              f"(range={high-low:.2f} pts)")

    def set_htf_bias(self, bias: str, strength: float) -> None:
        """Called by premarket routine with 30-min HTF bias."""
        self._htf_bias = {'bias': bias, 'strength': strength}

    def record_trade_result(self, pnl_pts: float) -> None:
        """Update daily state after each trade closes."""
        self._daily_pnl_pts += pnl_pts
        self._trades_today += 1
        if self._daily_pnl_pts >= _DAILY_GOAL_PTS:
            self._goal_hit = True
            print(f"[mes_engine] 🎯 Daily goal reached ({self._daily_pnl_pts:.1f} pts) — standing down")
        elif self._daily_pnl_pts <= -_DAILY_STOP_PTS:
            print(f"[mes_engine] 🛑 Daily stop hit ({self._daily_pnl_pts:.1f} pts) — standing down")

    def reset_daily(self) -> None:
        """Called at premarket each day to reset all daily state."""
        self._daily_pnl_pts = 0.0
        self._trades_today = 0
        self._goal_hit = False
        self._opening_range = {}
        self._breakout_state = {}
        self._vix = None
        self._premarket_bias = 'NEUTRAL'
        print("[mes_engine] Daily state reset")

    def update_premarket_bias(self, df_premarket: pd.DataFrame) -> str:
        """
        Proxy for ghost order / accumulation detection using candle structure.
        Consistent dip buying = lower wick > 2× upper wick across ≥3 pre-market bars.
        Returns 'BULLISH' | 'BEARISH' | 'NEUTRAL'.
        """
        if df_premarket is None or len(df_premarket) < 3:
            return 'NEUTRAL'
        try:
            df = df_premarket.tail(10).copy()
            lower_wicks = df['close'].combine(df['open'], max) - df['low']
            upper_wicks = df['high'] - df['close'].combine(df['open'], min)
            bull_bars = (lower_wicks > upper_wicks * 2).sum()
            bear_bars = (upper_wicks > lower_wicks * 2).sum()
            if bull_bars >= 3 and bull_bars > bear_bars:
                bias = 'BULLISH'
            elif bear_bars >= 3 and bear_bars > bull_bars:
                bias = 'BEARISH'
            else:
                bias = 'NEUTRAL'
            self._premarket_bias = bias
            print(f"[mes_engine] Pre-market accumulation signal: {bias} "
                  f"(bull_bars={bull_bars}, bear_bars={bear_bars})")
            return bias
        except Exception:
            return 'NEUTRAL'

    @property
    def daily_pnl_pts(self) -> float:
        return self._daily_pnl_pts

    @property
    def trades_today(self) -> int:
        return self._trades_today

    @property
    def trades_remaining(self) -> int:
        return max(0, _MAX_TRADES_SESSION - self._trades_today)

    @property
    def goal_pts(self) -> float:
        return _DAILY_GOAL_PTS

    @property
    def stop_pts(self) -> float:
        return _DAILY_STOP_PTS

    # ─── Signal paths ─────────────────────────────────────────────────────────

    def _orb_pullback_signal(self, price: float, df: pd.DataFrame, vix: Optional[float]) -> MESSignal:
        """Opening Range Breakout with pullback entry."""
        hold = lambda reason: MESSignal(
            action='HOLD', signal_type='hold', confidence=0.0,
            reason=reason, entry_price=price, stop_pts=0.0, target_pts=0.0,
            htf_bias=self._htf_bias.get('bias', 'NEUTRAL'),
            vix_regime=self._vix_regime(),
        )

        if not self._opening_range.get('set'):
            return hold("Opening range not set yet — waiting for 9:35 ET")

        or_high = self._opening_range['high']
        or_low = self._opening_range['low']
        or_range = or_high - or_low

        if or_range < 1.0:
            return hold(f"Opening range too narrow ({or_range:.2f} pts) — unreliable for ORB")

        last = df.iloc[-1]
        adx = float(last.get('adx', 25) or 25)
        volume = float(last.get('volume', 0) or 0)

        if adx < _MIN_ADX:
            return hold(f"Choppy market (ADX={adx:.1f} < {_MIN_ADX}) — ORB unreliable")

        htf_bias = self._htf_bias.get('bias', 'NEUTRAL')

        # ── Detect breakout direction ─────────────────────────────────────────
        if not self._breakout_state:
            if price > or_high * (1 + _BREAKOUT_THRESHOLD):
                if htf_bias == 'BEARISH':
                    return hold(f"Long breakout vs BEARISH HTF bias — skip")
                self._breakout_state = {
                    'direction': 'LONG',
                    'level': or_high,
                    'breakout_bar_volume': volume,
                    'pullback_in_progress': False,
                    'pullback_bar_count': 0,
                    'pullback_volumes': [],
                    'last_bar_ts': df.index[-1],
                }
                return hold(f"LONG breakout detected at {or_high:.2f} — waiting for pullback")

            elif price < or_low * (1 - _BREAKOUT_THRESHOLD):
                if htf_bias == 'BULLISH':
                    return hold(f"Short breakout vs BULLISH HTF bias — skip")
                self._breakout_state = {
                    'direction': 'SHORT',
                    'level': or_low,
                    'breakout_bar_volume': volume,
                    'pullback_in_progress': False,
                    'pullback_bar_count': 0,
                    'pullback_volumes': [],
                    'last_bar_ts': df.index[-1],
                }
                return hold(f"SHORT breakout detected at {or_low:.2f} — waiting for pullback")

            return hold(f"No breakout: price={price:.2f} inside OR [{or_low:.2f}–{or_high:.2f}]")

        # ── Manage existing breakout state ────────────────────────────────────
        bs = self._breakout_state
        direction = bs['direction']
        level = bs['level']
        breakout_vol = bs.get('breakout_bar_volume', volume)

        # ── Landry pullback bar count: only increment on a NEW 5-min bar ────────
        current_bar_ts = df.index[-1]
        last_bar_ts = bs.get('last_bar_ts')
        is_new_bar = (last_bar_ts is None or current_bar_ts != last_bar_ts)

        # If price has run far away and then pulled back, track pullback
        if direction == 'LONG':
            in_pullback_zone = price <= level * (1 + _PULLBACK_ZONE)
            if in_pullback_zone:
                bs['pullback_in_progress'] = True
                if is_new_bar:
                    bs['pullback_bar_count'] = bs.get('pullback_bar_count', 0) + 1
                    bs['pullback_volumes'] = bs.get('pullback_volumes', []) + [volume]
                    bs['last_bar_ts'] = current_bar_ts

            bar_count = bs.get('pullback_bar_count', 0)
            pullback_vols = bs.get('pullback_volumes', [])
            avg_pullback_vol = (sum(pullback_vols) / len(pullback_vols)) if pullback_vols else volume

            # Entry: pullback happened, bar count 3–7, bouncing on expansion vs pullback avg
            # but still reduced vs breakout (Landry: demand re-entering, not panic)
            if (bs['pullback_in_progress']
                    and price > level
                    and price <= level * (1 + _PULLBACK_ZONE * 2)
                    and 3 <= bar_count <= 7
                    and volume > avg_pullback_vol * 1.2   # volume expanding vs pullback
                    and volume < breakout_vol * 0.7):      # still subdued vs breakout
                return self._build_long_signal(
                    price, or_range, adx, vix, level,
                    f"ORB pullback long at {price:.2f} | level={level:.2f} | "
                    f"OR_range={or_range:.1f} | ADX={adx:.1f} | HTF={htf_bias} | "
                    f"pullback_bars={bar_count}",
                )
            if bar_count > 7:
                self._breakout_state = {}
                return hold(f"Pullback exceeded 7 bars ({bar_count}) — setup invalidated (Landry rule)")

        else:  # SHORT
            in_pullback_zone = price >= level * (1 - _PULLBACK_ZONE)
            if in_pullback_zone:
                bs['pullback_in_progress'] = True
                if is_new_bar:
                    bs['pullback_bar_count'] = bs.get('pullback_bar_count', 0) + 1
                    bs['pullback_volumes'] = bs.get('pullback_volumes', []) + [volume]
                    bs['last_bar_ts'] = current_bar_ts

            bar_count = bs.get('pullback_bar_count', 0)
            pullback_vols = bs.get('pullback_volumes', [])
            avg_pullback_vol = (sum(pullback_vols) / len(pullback_vols)) if pullback_vols else volume

            if (bs['pullback_in_progress']
                    and price < level
                    and price >= level * (1 - _PULLBACK_ZONE * 2)
                    and 3 <= bar_count <= 7
                    and volume > avg_pullback_vol * 1.2
                    and volume < breakout_vol * 0.7):
                return self._build_short_signal(
                    price, or_range, adx, vix, level,
                    f"ORB pullback short at {price:.2f} | level={level:.2f} | "
                    f"OR_range={or_range:.1f} | ADX={adx:.1f} | HTF={htf_bias} | "
                    f"pullback_bars={bar_count}",
                )
            if bar_count > 7:
                self._breakout_state = {}
                return hold(f"Pullback exceeded 7 bars ({bar_count}) — setup invalidated (Landry rule)")

        if is_new_bar:
            bs['last_bar_ts'] = current_bar_ts

        bar_count = bs.get('pullback_bar_count', 0)
        return hold(
            f"Breakout {direction} confirmed — waiting for pullback to {level:.2f} "
            f"(pullback_in_progress={bs.get('pullback_in_progress', False)}, bars={bar_count}/7)"
        )

    def _close_auction_signal(self, price: float, df: pd.DataFrame, vix: Optional[float]) -> MESSignal:
        """Close auction setup — trade with last-hour trend."""
        hold = lambda reason: MESSignal(
            action='HOLD', signal_type='hold', confidence=0.0,
            reason=reason, entry_price=price, stop_pts=0.0, target_pts=0.0,
            htf_bias=self._htf_bias.get('bias', 'NEUTRAL'),
            vix_regime=self._vix_regime(),
        )

        if len(df) < 12:
            return hold("Insufficient data for close auction")

        last = df.iloc[-1]
        adx = float(last.get('adx', 25) or 25)

        if adx < 20:
            return hold(f"ADX={adx:.1f} < 20 — no trend to ride in close auction")

        # Last-hour trend: compare current price to price 12 bars ago (1 hour on 5-min bars)
        price_1h_ago = float(df.iloc[-12]['close'])
        trend_pct = (price - price_1h_ago) / price_1h_ago * 100

        htf_bias = self._htf_bias.get('bias', 'NEUTRAL')

        if trend_pct > 0.3 and htf_bias != 'BEARISH':
            vwap = float(last.get('vwap', price) or price)
            if price < vwap:
                return hold(f"Close auction: bullish trend but price below VWAP — skipping")
            return self._build_long_signal(
                price, 4.0, adx, vix, price,
                f"Close auction long | trend={trend_pct:+.2f}% (1h) | ADX={adx:.1f} | HTF={htf_bias}",
                signal_type='close_auction',
            )
        elif trend_pct < -0.3 and htf_bias != 'BULLISH':
            vwap = float(last.get('vwap', price) or price)
            if price > vwap:
                return hold(f"Close auction: bearish trend but price above VWAP — skipping")
            return self._build_short_signal(
                price, 4.0, adx, vix, price,
                f"Close auction short | trend={trend_pct:+.2f}% (1h) | ADX={adx:.1f} | HTF={htf_bias}",
                signal_type='close_auction',
            )

        return hold(f"Close auction: no clear trend (1h change={trend_pct:+.2f}%)")

    # ─── Signal builders ─────────────────────────────────────────────────────

    def _build_long_signal(
        self, price: float, or_range: float, adx: float,
        vix: Optional[float], level: float, reason: str,
        signal_type: str = 'orb_pullback',
    ) -> MESSignal:
        stop_pts = max(or_range * 0.5, 3.0)   # Stop = 50% of OR or 3 pts min
        target_pts = or_range                  # Target = full OR range (1:1 R:R minimum)
        target_pts = max(target_pts, stop_pts * 1.5)  # Enforce 1.5:1 R:R minimum

        confidence = self._calc_confidence(adx, vix, 'LONG')

        # Clear breakout state — we've entered
        self._breakout_state = {}

        return MESSignal(
            action='LONG',
            signal_type=signal_type,
            confidence=confidence,
            reason=reason,
            entry_price=price,
            stop_pts=stop_pts,
            target_pts=target_pts,
            contracts=_MAX_CONTRACTS,
            htf_bias=self._htf_bias.get('bias', 'NEUTRAL'),
            vix_regime=self._vix_regime(),
            fired_signals=self._get_fired_signals('LONG', adx, vix),
        )

    def _build_short_signal(
        self, price: float, or_range: float, adx: float,
        vix: Optional[float], level: float, reason: str,
        signal_type: str = 'orb_pullback',
    ) -> MESSignal:
        stop_pts = max(or_range * 0.5, 3.0)
        target_pts = or_range
        target_pts = max(target_pts, stop_pts * 1.5)

        confidence = self._calc_confidence(adx, vix, 'SHORT')

        self._breakout_state = {}

        return MESSignal(
            action='SHORT',
            signal_type=signal_type,
            confidence=confidence,
            reason=reason,
            entry_price=price,
            stop_pts=stop_pts,
            target_pts=target_pts,
            contracts=_MAX_CONTRACTS,
            htf_bias=self._htf_bias.get('bias', 'NEUTRAL'),
            vix_regime=self._vix_regime(),
            fired_signals=self._get_fired_signals('SHORT', adx, vix),
        )

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _calc_confidence(self, adx: float, vix: Optional[float], direction: str) -> float:
        base = 0.55
        # ADX strength bonus
        base += min((adx - _MIN_ADX) / 40, 0.20)
        # HTF alignment bonus
        htf = self._htf_bias.get('bias', 'NEUTRAL')
        if (direction == 'LONG' and htf == 'BULLISH') or (direction == 'SHORT' and htf == 'BEARISH'):
            base += 0.10
        # Pre-market accumulation alignment
        if (direction == 'LONG' and self._premarket_bias == 'BULLISH') or \
           (direction == 'SHORT' and self._premarket_bias == 'BEARISH'):
            base += 0.05
        # VIX discount
        if vix:
            if vix > _ELEVATED_VIX:
                base *= 0.85
        return min(round(base, 3), 0.90)

    def _get_fired_signals(self, direction: str, adx: float, vix: Optional[float]) -> list:
        sigs = []
        if adx >= _MIN_ADX:
            sigs.append(f"adx={adx:.1f}")
        htf = self._htf_bias.get('bias', 'NEUTRAL')
        if htf != 'NEUTRAL':
            sigs.append(f"htf_{htf.lower()}")
        if self._premarket_bias != 'NEUTRAL':
            sigs.append(f"premarket_{self._premarket_bias.lower()}")
        if vix:
            sigs.append(f"vix={vix:.1f}")
        return sigs

    def _vix_regime(self) -> str:
        v = self._vix
        if v is None:
            return 'UNKNOWN'
        if v > _HIGH_VIX:
            return 'HIGH'
        if v > _ELEVATED_VIX:
            return 'ELEVATED'
        return 'NORMAL'

    def _get_vix(self) -> Optional[float]:
        """Fetch VIX via yfinance with 30-min cache."""
        if self._vix is not None:
            return self._vix
        try:
            import yfinance as yf
            hist = yf.Ticker('^VIX').history(period='1d', interval='5m')
            if hist is not None and not hist.empty:
                self._vix = float(hist['Close'].iloc[-1])
                return self._vix
        except Exception:
            pass
        return None


# ─── Module-level singleton ───────────────────────────────────────────────────

_engine: Optional[MESEngine] = None


def get_engine() -> MESEngine:
    global _engine
    if _engine is None:
        _engine = MESEngine()
    return _engine


def evaluate(price: float, df_5m: pd.DataFrame) -> MESSignal:
    """Module-level evaluate — uses singleton engine."""
    return get_engine().evaluate(price, df_5m)
