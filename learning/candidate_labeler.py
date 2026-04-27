"""
learning/candidate_labeler.py — Automated forward-outcome labeler for scan_candidates.

Scheduled to run every 15 minutes by v10_runner.
Finds unlabeled candidates that are >= 4 hours old, fetches forward candles,
computes 15m / 1h / 4h forward returns and MFE / MAE, then writes to candidate_outcomes.

Design constraints:
- Never blocks live scanning or entry (runs in a background thread).
- Uses existing get_candles() plumbing — no new API churn.
- All writes are SQLite only.
- Bounded: processes at most MAX_BATCH rows per run.
- Silent on individual failures (logs warnings, never raises).

15-minute labeling:
  Fetches an additional 50 × 15m candle series per labeled row.
  Since labeling occurs >= 4h after the candidate, all 15m forward bars
  have resolved.  Uses the same "backward approximation" pattern as the
  1h labeler: ref_idx_15m = len(bars) − 17 ≈ 4h before current last bar.
  Cost: ~1 extra lightweight REST call per labeled row (bounded at MAX_BATCH).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum rows to label per scheduler invocation
_MAX_BATCH = 50

# Minimum look-forward age before we attempt labeling (seconds)
_MIN_AGE_HOURS = 4.0

# 15m candle series length; 50 bars × 15min = 12.5h of data.
# With MIN_AGE=4h, the reference bar sits at index ≈ len−17
# (16 × 15min = 4h), and the 15m forward bar is at index ≈ len−16.
_15M_SERIES_LEN = 50


def _parse_ref_ts(ref_ts_iso: str):
    """Parse candidate timestamp into a timezone-aware UTC datetime."""
    if not ref_ts_iso:
        return None
    try:
        ts = datetime.fromisoformat(str(ref_ts_iso))
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _resolve_reference_index(df, ref_ts_iso: str, fallback_idx: int) -> int:
    """
    Resolve the reference bar index from the candidate timestamp.

    Falls back to the historical tail-based heuristic when the timestamp cannot
    be mapped onto the candle index.
    """
    try:
        n = len(df)
        if n <= 0:
            return 0
        fallback_idx = max(0, min(fallback_idx, n - 1))
        ts = _parse_ref_ts(ref_ts_iso)
        if ts is None or not hasattr(df, "index"):
            return fallback_idx
        idx = df.index
        if not hasattr(idx, "searchsorted"):
            return fallback_idx
        pos = int(idx.searchsorted(ts, side="right")) - 1
        if pos < 0:
            return fallback_idx
        return min(pos, n - 1)
    except Exception:
        return fallback_idx


def _fetch_forward_candles(
    symbol: str,
    ref_ts_iso: str,
    get_candles,
    interval: str = "1h",
    limit: int = 200,
) -> Optional[object]:
    """
    Fetch candles for `symbol` and return the DataFrame.
    Returns None if unavailable or too short.
    """
    try:
        df = get_candles(symbol, interval, limit)
        if df is None or len(df) < 5:
            return None
        return df
    except Exception as e:
        logger.debug(f"[labeler] candle fetch error {symbol} {interval}: {e}")
        return None


def _compute_15m_metrics(
    df_15m,
    ref_close: float,
    direction: str,
    ref_ts_iso: str = "",
) -> tuple:
    """
    Compute 15-minute forward price and return from a 15m candle DataFrame.

    Uses the same backward-approximation pattern as _compute_outcome:
    the reference bar is approximately 4h before the last bar
    (16 × 15m bars), and price_15m is the bar immediately after it.

    Returns (price_15m, ret_15m_pct). Both 0.0 on any failure.
    """
    try:
        closes = list(df_15m["close"].values)
        if len(closes) < 5:
            return 0.0, 0.0
        ref_idx_15m = _resolve_reference_index(
            df_15m,
            ref_ts_iso,
            max(0, len(closes) - 17),
        )
        price_15m = float(closes[min(ref_idx_15m + 1, len(closes) - 1)])
        if ref_close <= 0:
            return price_15m, 0.0
        is_long = str(direction).upper() == "LONG"
        if is_long:
            ret_15m_pct = (price_15m - ref_close) / ref_close * 100.0
        else:
            ret_15m_pct = (ref_close - price_15m) / ref_close * 100.0
        return price_15m, round(ret_15m_pct, 4)
    except Exception:
        return 0.0, 0.0


def _compute_path_timing(
    df_15m,
    ref_close: float,
    direction: str,
    stop_pct: float,
    ref_ts_iso: str = "",
) -> dict:
    """
    Compute how many minutes it took for the trade to reach 0.5R, 1R, and 2R.

    Scans 15m bars AFTER the reference bar.  Uses the favorable extreme per bar:
    - LONG: bar high for MFE (favorable = up)
    - SHORT: bar low for MFE (favorable = down)

    R multiples are derived from stop_pct (1R = stop distance as % of entry).

    Returns dict with keys: time_to_05r_min, time_to_1r_min, time_to_2r_min.
    All values are None if the threshold was not reached within available bars.
    """
    result: dict = {
        "time_to_05r_min": None,
        "time_to_1r_min": None,
        "time_to_2r_min": None,
    }
    try:
        if df_15m is None or len(df_15m) < 5:
            return result
        if ref_close <= 0:
            return result

        _stop_pct = float(stop_pct) if stop_pct and stop_pct > 0 else 3.0
        target_05r = _stop_pct * 0.5
        target_1r = _stop_pct * 1.0
        target_2r = _stop_pct * 2.0

        highs = list(df_15m["high"].values)
        lows = list(df_15m["low"].values)
        is_long = str(direction).upper() == "LONG"

        ref_idx_15m = _resolve_reference_index(
            df_15m,
            ref_ts_iso,
            max(0, len(highs) - 17),
        )

        for bar_offset, idx in enumerate(range(ref_idx_15m + 1, len(highs)), start=1):
            if is_long:
                extreme = float(highs[idx])
                move_pct = (extreme - ref_close) / ref_close * 100.0
            else:
                extreme = float(lows[idx])
                move_pct = (ref_close - extreme) / ref_close * 100.0

            minutes = bar_offset * 15
            if result["time_to_05r_min"] is None and move_pct >= target_05r:
                result["time_to_05r_min"] = minutes
            if result["time_to_1r_min"] is None and move_pct >= target_1r:
                result["time_to_1r_min"] = minutes
            if result["time_to_2r_min"] is None and move_pct >= target_2r:
                result["time_to_2r_min"] = minutes

            if all(v is not None for v in result.values()):
                break  # all thresholds hit — stop scanning

    except Exception as e:
        logger.debug(f"[labeler] path timing error: {e}")
    return result


def _compute_outcome(
    df,
    ref_price: float,
    direction: str,
    stop_pct: float,
    atr_15m: float,
    df_15m=None,
    ref_ts_iso: str = "",
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

    If df_15m is provided, also computes price_15m and ret_15m_pct.
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
    ref_idx = _resolve_reference_index(df, ref_ts_iso, max(0, len(closes) - 5))
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

    # 15m forward metrics — cheap extra fetch, high analytical value
    price_15m = 0.0
    ret_15m_pct = 0.0
    if df_15m is not None:
        price_15m, ret_15m_pct = _compute_15m_metrics(
            df_15m,
            ref_close,
            direction,
            ref_ts_iso=ref_ts_iso,
        )

    # Path timing: how many minutes to reach 0.5R / 1R / 2R
    timing = _compute_path_timing(
        df_15m,
        ref_close,
        direction,
        _stop_pct,
        ref_ts_iso=ref_ts_iso,
    )
    path_timing_evaluated = int(df_15m is not None and len(df_15m) >= 5)

    # peak_r_4h: MFE expressed in R units (mfe_4h_pct / stop_pct)
    peak_r_4h = round(mfe_4h_pct / _stop_pct, 4) if _stop_pct > 0 else None

    return {
        "label_status": "complete",
        "entry_ref_price": ref_close,
        "price_15m": price_15m,
        "price_1h": price_1h,
        "price_4h": price_4h,
        "ret_15m_pct": ret_15m_pct,
        "ret_1h_pct": round(ret_1h_pct, 4),
        "ret_4h_pct": round(ret_4h_pct, 4),
        "mfe_4h_pct": round(mfe_4h_pct, 4),
        "mae_4h_pct": round(mae_4h_pct, 4),
        "hit_1r": hit_1r,
        "hit_2r": hit_2r,
        "hit_stop": hit_stop,
        "best_exit_pct": round(best_exit_pct, 4),
        "worst_drawdown_pct": round(worst_drawdown_pct, 4),
        "time_to_05r_min": timing["time_to_05r_min"],
        "time_to_1r_min": timing["time_to_1r_min"],
        "time_to_2r_min": timing["time_to_2r_min"],
        "peak_r_4h": peak_r_4h,
        "path_timing_evaluated": path_timing_evaluated,
    }


def run_labeling_pass(get_candles=None) -> dict:
    """
    Main entry point — called by v10_runner every 15 minutes.

    Finds unlabeled candidates >= 4h old, fetches forward candles (1h + 15m),
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
            df_1h = _fetch_forward_candles(symbol, ref_ts, get_candles, "1h", 200)
            if df_1h is None:
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
                    price_15m=0.0,
                    ret_15m_pct=0.0,
                )
                skipped += 1
                continue

            # Fetch 15m candles for short-term forward return (best-effort)
            df_15m = _fetch_forward_candles(
                symbol, ref_ts, get_candles, "15m", _15M_SERIES_LEN
            )

            outcome = _compute_outcome(
                df_1h,
                ref_price,
                direction,
                stop_pct,
                atr_15m,
                df_15m,
                ref_ts_iso=ref_ts,
            )

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
                    price_15m=0.0,
                    ret_15m_pct=0.0,
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
                price_15m=outcome.get("price_15m", 0.0),
                ret_15m_pct=outcome.get("ret_15m_pct", 0.0),
                path_timing_evaluated=outcome.get("path_timing_evaluated", 0),
                time_to_05r_min=outcome.get("time_to_05r_min"),
                time_to_1r_min=outcome.get("time_to_1r_min"),
                time_to_2r_min=outcome.get("time_to_2r_min"),
                peak_r_4h=outcome.get("peak_r_4h"),
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
    logger.info(
        f"[labeler] labeled {labeled}/{len(candidates)} candidates "
        f"(skipped={skipped} errors={errors})"
    )
    try:
        from notifications.notification_engine import NotificationEngine

        NotificationEngine().emit(
            source="candidate_labeler",
            level="INFO",
            message=(
                f"Labeling pass: processed={len(candidates)} "
                f"labeled={labeled} skipped={skipped} errors={errors}"
            ),
        )
    except Exception:
        pass
    return summary
