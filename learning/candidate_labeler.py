"""
learning/candidate_labeler.py — Automated forward-outcome labeler for scan_candidates.

Scheduled to run every 15 minutes by v10_runner.
Finds unlabeled candidates that are >= 4 hours old, fetches forward candles,
computes 1h / 4h forward returns and MFE / MAE, then writes to candidate_outcomes.

Design constraints:
- Never blocks live scanning or entry (runs in a background thread).
- Uses existing get_candles() plumbing — no new API churn.
- All writes are SQLite only.
- Bounded: processes at most MAX_BATCH rows per run.
- Silent on individual failures (logs warnings, never raises).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum rows to label per scheduler invocation
_MAX_BATCH = 50

# Minimum look-forward age before we attempt labeling (seconds)
_MIN_AGE_HOURS = 4.0


def _fetch_forward_candles(
    symbol: str,
    ref_ts_iso: str,
    get_candles,
) -> Optional[object]:
    """
    Fetch 1h candles for `symbol` and return the DataFrame.
    Returns None if unavailable.
    """
    try:
        df = get_candles(symbol, "1h", 200)
        if df is None or len(df) < 5:
            return None
        return df
    except Exception as e:
        logger.debug(f"[labeler] candle fetch error {symbol}: {e}")
        return None


def _compute_outcome(
    df,
    ref_price: float,
    direction: str,
    stop_pct: float,
    atr_15m: float,
) -> dict:
    """
    Compute forward-outcome metrics from a candles DataFrame.

    Uses the last bar as the reference (approximately now = ref_price time).
    Look-forward is based on available subsequent bars in the DataFrame.

    For a 200-bar 1h DataFrame the last bar is ~now.  We synthesise forward
    windows by scanning the tail:
      - price_1h  = close 1 bar after last (not available yet → use last)
      - price_4h  = close 4 bars after last
    Since we're fetching after min_age=4h, the 4h window has already resolved
    and the last 4 bars of the DataFrame are our forward window.
    """
    closes = list(df["close"].values)
    highs = list(df["high"].values)
    lows = list(df["low"].values)

    if len(closes) < 5:
        return {"label_status": "data_unavailable"}

    # Reference point: last bar when the candidate was scanned.
    # Since we are re-fetching 4h+ later, the "forward" bars are now in the tail.
    # We approximate: look back 4 bars from the current last bar as the entry bar,
    # then use the following bars as the forward window.
    ref_idx = max(0, len(closes) - 5)
    ref_close = float(closes[ref_idx])

    # If actual ref_price is very different from reconstructed ref, use the stored one
    # (price sanity: only trust reconstructed if within 2%)
    if ref_price > 0 and abs(ref_close - ref_price) / ref_price > 0.02:
        ref_close = ref_price

    is_long = str(direction).upper() == "LONG"

    # Forward prices: 1h = ref_idx+1, 4h = ref_idx+4 (or last available)
    price_1h = float(closes[min(ref_idx + 1, len(closes) - 1)])
    price_4h = float(closes[min(ref_idx + 4, len(closes) - 1)])

    # Forward return
    if is_long:
        ret_1h_pct = (price_1h - ref_close) / ref_close * 100.0
        ret_4h_pct = (price_4h - ref_close) / ref_close * 100.0
    else:
        ret_1h_pct = (ref_close - price_1h) / ref_close * 100.0
        ret_4h_pct = (ref_close - price_4h) / ref_close * 100.0

    # MFE / MAE over the 4h forward window
    fwd_closes = closes[ref_idx + 1 : ref_idx + 5]
    fwd_highs = highs[ref_idx + 1 : ref_idx + 5]
    fwd_lows = lows[ref_idx + 1 : ref_idx + 5]

    if not fwd_closes:
        return {"label_status": "data_unavailable"}

    if is_long:
        peak = max(fwd_highs) if fwd_highs else price_4h
        trough = min(fwd_lows) if fwd_lows else price_4h
        mfe_4h_pct = (peak - ref_close) / ref_close * 100.0
        mae_4h_pct = (trough - ref_close) / ref_close * 100.0
    else:
        peak = min(fwd_lows) if fwd_lows else price_4h
        trough = max(fwd_highs) if fwd_highs else price_4h
        mfe_4h_pct = (ref_close - peak) / ref_close * 100.0
        mae_4h_pct = (ref_close - trough) / ref_close * 100.0

    # Derive implied stop and 1R / 2R targets from stored stop_pct (% distance)
    # stop_pct is stored as a percentage (e.g. 3.0 = 3% stop distance)
    _stop_pct = float(stop_pct) if stop_pct and stop_pct > 0 else 3.0
    _1r = _stop_pct  # 1R = 1× stop distance
    _2r = _stop_pct * 2.0  # 2R = 2× stop distance

    if is_long:
        hit_1r = int(mfe_4h_pct >= _1r)
        hit_2r = int(mfe_4h_pct >= _2r)
        hit_stop = int(mae_4h_pct <= -_stop_pct)
    else:
        hit_1r = int(mfe_4h_pct >= _1r)
        hit_2r = int(mfe_4h_pct >= _2r)
        hit_stop = int(mae_4h_pct <= -_stop_pct)

    best_exit_pct = mfe_4h_pct
    worst_drawdown_pct = mae_4h_pct

    return {
        "label_status": "complete",
        "entry_ref_price": ref_close,
        "price_1h": price_1h,
        "price_4h": price_4h,
        "ret_1h_pct": round(ret_1h_pct, 4),
        "ret_4h_pct": round(ret_4h_pct, 4),
        "mfe_4h_pct": round(mfe_4h_pct, 4),
        "mae_4h_pct": round(mae_4h_pct, 4),
        "hit_1r": hit_1r,
        "hit_2r": hit_2r,
        "hit_stop": hit_stop,
        "best_exit_pct": round(best_exit_pct, 4),
        "worst_drawdown_pct": round(worst_drawdown_pct, 4),
    }


def run_labeling_pass(get_candles=None) -> dict:
    """
    Main entry point — called by v10_runner every 15 minutes.

    Finds unlabeled candidates >= 4h old, fetches forward candles,
    writes outcomes. Returns a summary dict.

    Args:
        get_candles: callable(symbol, interval, limit) → DataFrame.
                     If None, attempts to import from data.historical_data.

    Returns:
        {"processed": int, "labeled": int, "skipped": int, "errors": int}
    """
    if get_candles is None:
        try:
            from data.historical_data import get_candles as _gc

            get_candles = _gc
        except Exception as e:
            logger.debug(f"[labeler] cannot import get_candles: {e}")
            return {"processed": 0, "labeled": 0, "skipped": 0, "errors": 0}

    try:
        from logging_db.trade_logger import (
            get_unlabeled_candidates,
            log_candidate_outcome,
        )
    except Exception as e:
        logger.debug(f"[labeler] cannot import trade_logger: {e}")
        return {"processed": 0, "labeled": 0, "skipped": 0, "errors": 0}

    candidates = get_unlabeled_candidates(
        min_age_hours=_MIN_AGE_HOURS, limit=_MAX_BATCH
    )
    if not candidates:
        return {"processed": 0, "labeled": 0, "skipped": 0, "errors": 0}

    labeled = 0
    skipped = 0
    errors = 0

    for cand in candidates:
        candidate_id = cand.get("id")
        symbol = cand.get("symbol", "")
        direction = cand.get("direction", "LONG")
        ref_price = float(cand.get("price") or 0)
        stop_pct = float(cand.get("stop_pct") or 3.0)
        atr_15m = float(cand.get("atr_15m") or 0)
        ref_ts = cand.get("ts", "")

        if not symbol or not candidate_id:
            skipped += 1
            continue

        try:
            df = _fetch_forward_candles(symbol, ref_ts, get_candles)
            if df is None:
                # Write a partial label so we don't keep retrying stale symbols
                log_candidate_outcome(
                    candidate_id=candidate_id,
                    label_status="data_unavailable",
                    entry_ref_price=ref_price,
                    price_1h=0.0,
                    price_4h=0.0,
                    ret_1h_pct=0.0,
                    ret_4h_pct=0.0,
                    mfe_4h_pct=0.0,
                    mae_4h_pct=0.0,
                    hit_1r=0,
                    hit_2r=0,
                    hit_stop=0,
                    best_exit_pct=0.0,
                    worst_drawdown_pct=0.0,
                )
                skipped += 1
                continue

            outcome = _compute_outcome(df, ref_price, direction, stop_pct, atr_15m)

            if outcome.get("label_status") == "data_unavailable":
                log_candidate_outcome(
                    candidate_id=candidate_id,
                    label_status="data_unavailable",
                    entry_ref_price=ref_price,
                    price_1h=0.0,
                    price_4h=0.0,
                    ret_1h_pct=0.0,
                    ret_4h_pct=0.0,
                    mfe_4h_pct=0.0,
                    mae_4h_pct=0.0,
                    hit_1r=0,
                    hit_2r=0,
                    hit_stop=0,
                    best_exit_pct=0.0,
                    worst_drawdown_pct=0.0,
                )
                skipped += 1
                continue

            log_candidate_outcome(
                candidate_id=candidate_id,
                label_status=outcome["label_status"],
                entry_ref_price=outcome["entry_ref_price"],
                price_1h=outcome["price_1h"],
                price_4h=outcome["price_4h"],
                ret_1h_pct=outcome["ret_1h_pct"],
                ret_4h_pct=outcome["ret_4h_pct"],
                mfe_4h_pct=outcome["mfe_4h_pct"],
                mae_4h_pct=outcome["mae_4h_pct"],
                hit_1r=outcome["hit_1r"],
                hit_2r=outcome["hit_2r"],
                hit_stop=outcome["hit_stop"],
                best_exit_pct=outcome["best_exit_pct"],
                worst_drawdown_pct=outcome["worst_drawdown_pct"],
            )
            labeled += 1

        except Exception as e:
            logger.warning(
                f"[labeler] error labeling candidate {candidate_id} ({symbol}): {e}"
            )
            errors += 1

    summary = {
        "processed": len(candidates),
        "labeled": labeled,
        "skipped": skipped,
        "errors": errors,
    }
    if labeled > 0:
        logger.info(
            f"[labeler] labeled {labeled}/{len(candidates)} candidates "
            f"(skipped={skipped} errors={errors})"
        )
    return summary
