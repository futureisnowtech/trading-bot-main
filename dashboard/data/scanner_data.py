"""
dashboard/data/scanner_data.py — Scanner status, funnel truth, log summary.
Named scanner_data to avoid shadowing the project-root scanner.py.

Primary source of truth:
  - scan_funnels
  - scan_candidates
  - lane_runtime_state

Log parsing remains as a fallback only when the DB truth surfaces are absent.
"""

import re
import os
import sys
from datetime import datetime, timedelta

_DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_DIR = os.path.dirname(_DASH_DIR)
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

import db as _db

_q = _db._q
_q1 = _db._q1
_tail_log = getattr(_db, "_tail_log", lambda n=200: [])
LOG_PATH = getattr(_db, "LOG_PATH", "")
_clamp_metrics_cutoff = getattr(_db, "clamp_metrics_cutoff", lambda s: s)


def get_last_scan_age():
    """
    Seconds since last scan activity.

    Primary: lane_runtime_state.last_heartbeat_at (updated every minute by
    v10_runner regardless of candidate count — always current when bot is alive).

    Secondary: bot.log [v10] scan: lines (only written when candidates > 0,
    so stale when every scan returns 0 candidates — used only as a tiebreaker).

    Returns min(heartbeat_age, log_age) so the freshest signal always wins.
    """
    # Always get the heartbeat age first — it's the most reliable liveness signal
    heartbeat_age = 9999
    try:
        row = _q1("""
            SELECT last_heartbeat_at FROM lane_runtime_state
            WHERE lane_id = 'crypto' ORDER BY id DESC LIMIT 1
        """)
        if row and row.get("last_heartbeat_at"):
            from formatters import _ts_age_s

            age = _ts_age_s(row["last_heartbeat_at"])
            if age < 9999:
                heartbeat_age = age
    except Exception:
        pass

    # Also check the log for [v10] scan: lines as a secondary signal
    log_age = 9999
    try:
        with open(LOG_PATH, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            buf = b""
            pos = file_size
            chunk = 8192
            while pos > 0:
                read_size = min(chunk, pos)
                pos -= read_size
                f.seek(pos)
                buf = f.read(read_size) + buf
                for raw in reversed(buf.split(b"\n")):
                    line = raw.decode("utf-8", errors="replace")
                    if "[v10] scan:" in line:
                        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                        if m:
                            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                            log_age = int((datetime.now() - dt).total_seconds())
                        break
                if log_age < 9999:
                    break
                buf = buf.split(b"\n")[0]
    except Exception:
        pass

    return min(heartbeat_age, log_age)


def get_scan_status():
    db_result = _get_scan_status_from_db()
    if db_result is not None:
        return db_result

    return _get_scan_status_from_logs()


def _get_scan_status_from_db():
    """Preferred truth path using scan_funnels + scan_candidates."""
    try:
        lane_row = _q1(
            """
            SELECT last_heartbeat_at, capital_deployed_usd, buying_power_usd
            FROM lane_runtime_state
            WHERE lane_id='crypto'
            ORDER BY id DESC LIMIT 1
            """
        )
        f = _q1(
            """
            SELECT *
            FROM scan_funnels
            ORDER BY id DESC LIMIT 1
            """
        )
        if not f:
            return None

        scan_id = f.get("scan_id", "")
        candidates = []
        if scan_id:
            candidates = _q(
                """
                SELECT symbol, direction, COALESCE(volume_24h_usd, 0) AS volume_24h_usd,
                       COALESCE(edge_score, 0) AS edge_score,
                       COALESCE(scanner_expected_profit, 0) AS expected_profit,
                       COALESCE(recommended_lane, '') AS recommended_lane,
                       COALESCE(tradeability_status, '') AS tradeability_status,
                       COALESCE(primary_setup, '') AS primary_setup
                FROM scan_candidates
                WHERE scan_id=?
                ORDER BY id DESC
                LIMIT 8
                """,
                (scan_id,),
            )

        hb_age = get_last_scan_age()
        count = int(f.get("scanner_candidates_total") or 0)
        steps = []
        stage_rows = [
            ("Scored", int(f.get("scored_total") or 0)),
            ("Econ passed", int(f.get("econ_passed_total") or 0)),
            ("Final entryable", int(f.get("final_entryable_total") or 0)),
            ("Entered", int(f.get("entered") or 0)),
        ]
        prev_in = count
        for idx, (label, out_val) in enumerate(stage_rows, start=1):
            dropped = max(prev_in - out_val, 0)
            steps.append(
                {
                    "step": idx,
                    "in": prev_in,
                    "out": out_val,
                    "dropped": dropped,
                    "label": label,
                }
            )
            prev_in = out_val

        return {
            "age_s": hb_age,
            "count": count,
            "candidates": candidates,
            "steps": steps,
            "duration_s": 0.0,
            "balance": float(lane_row.get("buying_power_usd") or 0.0),
            "deployed": _current_crypto_deployed(
                float(lane_row.get("capital_deployed_usd") or 0.0)
            ),
        }
    except Exception:
        return None


def _current_crypto_deployed(fallback: float = 0.0) -> float:
    try:
        from data.positions import get_crypto_deployed_snapshot

        snap = get_crypto_deployed_snapshot()
        deployed = float(snap.get("deployed_usd") or 0.0)
        if deployed > 0:
            return deployed
    except Exception:
        pass
    return fallback


def _get_scan_status_from_logs():
    lines = _tail_log(800)
    result = {
        "age_s": 9999,
        "count": 0,
        "candidates": [],
        "steps": [],
        "duration_s": 0.0,
        "balance": 0.0,
        "deployed": 0.0,
    }
    complete_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if "[scanner] Complete:" in lines[i]:
            complete_idx = i
            break
    if complete_idx is None:
        return result
    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", lines[complete_idx])
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            result["age_s"] = int((datetime.now() - dt).total_seconds())
        except Exception:
            pass
    cm = re.search(
        r"Complete:\s*(\d+)\s*candidates\s*in\s*([\d.]+)s", lines[complete_idx]
    )
    if cm:
        result["count"] = int(cm.group(1))
        result["duration_s"] = float(cm.group(2))
    cand_re = re.compile(
        r"→\s+(\S+)\s+(LONG|SHORT)\s+spike=([\d.]+)\s+adx=([\d.]+)\s+ev=\$([\d.]+)\s+funding=([-\d.]+)%"
    )
    for line in lines[complete_idx + 1 : complete_idx + 20]:
        c = cand_re.search(line)
        if c:
            result["candidates"].append(
                {
                    "symbol": c.group(1),
                    "direction": c.group(2),
                    "vol_spike": float(c.group(3)),
                    "adx": float(c.group(4)),
                    "ev_usd": float(c.group(5)),
                    "funding_pct": float(c.group(6)),
                }
            )
    step_re = re.compile(r"\[scanner\] Step (\d+)[^:]*:\s*(\d+)\s*→\s*(\d+)")
    steps = {}
    for i in range(complete_idx, max(0, complete_idx - 30), -1):
        s = step_re.search(lines[i])
        if s:
            steps[int(s.group(1))] = {
                "step": int(s.group(1)),
                "in": int(s.group(2)),
                "out": int(s.group(3)),
                "dropped": int(s.group(2)) - int(s.group(3)),
                "label": lines[i].split("[scanner]")[-1].strip(),
            }
    result["steps"] = [steps[k] for k in sorted(steps.keys())]
    scan_re = re.compile(r"\[v10\] scan:.*balance=\$([\d.]+)\s+deployed=\$([\d.]+)")
    for line in lines[complete_idx : complete_idx + 5]:
        sm = scan_re.search(line)
        if sm:
            result["balance"] = float(sm.group(1))
            result["deployed"] = float(sm.group(2))
            break
    return result


def get_smart_log_summary(n=200) -> dict:
    """Parse bot.log into categorized event buckets."""
    lines = _tail_log(n)
    buckets = {
        "ENTERED": [],
        "CLOSE": [],
        "VETO": [],
        "SCAN": [],
        "ERROR": [],
        "ML": [],
        "HEALTH": [],
    }
    for line in reversed(lines):
        line = line.strip()
        if not any(
            x in line
            for x in (
                "[v10]",
                "[scanner]",
                "[perps]",
                "[risk]",
                "[wft]",
                "[learning]",
                "health",
            )
        ):
            continue
        if any(x in line for x in ("ib_insync", "IBKRBroker")):
            continue
        ts_m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        ts = ts_m.group(1)[11:19] if ts_m else ""
        msg = re.sub(
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+\s+\S+\s+\S+\s+", "", line
        )[:140]
        if "ENTERED" in line:
            k = "ENTERED"
        elif "PAPER CLOSE" in line or "CLOSE" in line.upper():
            k = "CLOSE"
        elif "ECONOMICS VETO" in line:
            k = "VETO"
        elif "Complete:" in line:
            k = "SCAN"
        elif "ERROR" in line.upper():
            k = "ERROR"
        elif "retrain" in line.lower() or "[wft]" in line:
            k = "ML"
        elif "health" in line.lower():
            k = "HEALTH"
        else:
            continue
        if len(buckets[k]) < 5:
            buckets[k].append({"ts": ts, "msg": msg})

    cutoff_1h = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    # Use normalized datetime comparison — raw ts >= ? fails for ISO timestamps
    # with 'T' separator because 'T' > ' ', making all same-day rows appear recent.
    r = _q1(
        "SELECT COUNT(*) AS n FROM system_events WHERE level='ERROR' "
        "AND datetime(replace(substr(ts,1,19),'T',' ')) >= ?",
        (cutoff_1h,),
    )
    error_count_1h = r.get("n") or 0
    rv = _q1(
        "SELECT COUNT(*) AS n FROM system_events WHERE message LIKE '%VETO%' "
        "AND datetime(replace(substr(ts,1,19),'T',' ')) >= ?",
        (cutoff_1h,),
    )
    veto_count_1h = rv.get("n") or 0
    re2 = _q1(
        "SELECT COUNT(*) AS n FROM trades "
        "WHERE datetime(replace(substr(ts,1,19),'T',' ')) >= ? "
        "AND paper=0 AND action IN ('BUY','SELL') AND pnl_usd=0",
        (cutoff_1h,),
    )
    entry_count_1h = re2.get("n") or 0
    return {
        "buckets": buckets,
        "error_count_1h": error_count_1h,
        "veto_count_1h": veto_count_1h,
        "entry_count_1h": entry_count_1h,
    }


_ECON_REASONS = {
    "ev_below_floor": "fees would eat the profit",
    "spread_too_wide": "bid-ask spread too wide",
    "volume_too_low": "volume too low",
    "rr_below_min": "risk/reward too low",
    "depth_too_thin": "order book too thin",
}

_BLOCK_REASONS = {
    "spot_position_already_open": "already holding this coin",
    "spot_deployment_cap_exceeded": "spot budget fully deployed",
    "perp_position_limit_reached": "already at max 3 perp trades",
    "perp_opposite_side_block": "opposite position is open",
    "perp_deployment_cap_exceeded": "perp budget fully deployed",
    "perp_not_autonomous_eligible": "manual-only symbol",
    "research_only_block": "research-only symbol, not tradeable live",
    "spot_min_order_not_met": "spot account has insufficient USD",
    "spot_balance_unavailable": "can't read spot account balance",
}

_BELOW_THRESHOLD_REASONS = {
    "below_regime_floor": "score below entry floor for this regime",
    "5m_derivative_not_positive": "5m momentum stalled — waiting for upward push",
    "5m_velocity_not_positive": "5m momentum stalled — waiting for upward push",
    "structural_confirm_count_too_low": "waiting for trend confirmation signals",
    "frame_score_5m_too_low": "5m momentum signals too weak",
    "frame_score_30m_too_low": "30m momentum signals too weak",
    "momentum_impulse_too_low": "momentum impulse too weak",
    "path_efficiency_too_low": "price path too choppy",
    "participation_component_too_low": "low participation in the move",
    "structure_component_too_low": "market structure not aligned",
    "spot_state_unavailable": "market data unavailable",
}


def _clean_sym(raw: str) -> str:
    s = (
        raw.upper()
        .replace("USDT", "")
        .replace("USDC", "")
        .replace("PF_", "")
        .replace("USD", "")
        .strip()
    )
    return s or raw.upper()


def get_recent_scan_summaries(limit: int = 6) -> list[dict]:
    """
    Return the last `limit` scan cycles as plain-English summaries drawn
    directly from scan_funnels + scan_candidates.  Replaces log-file SCAN parsing.
    Uses the candidate's `decision` field as ground truth — avoids funnel-count
    arithmetic that can contradict per-row data.
    """
    funnels = _q(
        "SELECT scan_id, ts, scanner_candidates_total, entered "
        "FROM scan_funnels ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    results = []
    for f in funnels:
        scan_id = f.get("scan_id") or ""
        ts_raw = str(f.get("ts") or "")
        ts = ts_raw[11:19] if len(ts_raw) > 10 else ts_raw
        scanned = int(f.get("scanner_candidates_total") or 0)
        entered = int(f.get("entered") or 0)
        entered_sym = ""
        block = ""

        if not scan_id:
            results.append(
                {
                    "ts": ts,
                    "scanned": scanned,
                    "entered": 0,
                    "entered_sym": "",
                    "top_symbol": "",
                    "top_score": 0,
                    "top_dir": "LONG",
                    "block": "no scan data",
                }
            )
            continue

        # All candidates for this scan, best score first
        candidates = _q(
            "SELECT symbol, direction, composite_score, decision, "
            "trade_blocked_reason, econ_reject_reason "
            "FROM scan_candidates WHERE scan_id=? "
            "ORDER BY CAST(composite_score AS REAL) DESC",
            (scan_id,),
        )

        if not candidates:
            results.append(
                {
                    "ts": ts,
                    "scanned": scanned,
                    "entered": 0,
                    "entered_sym": "",
                    "top_symbol": "",
                    "top_score": 0,
                    "top_dir": "LONG",
                    "block": "no candidates",
                }
            )
            continue

        top = candidates[0]
        top_sym = _clean_sym(str(top.get("symbol") or ""))
        top_score = float(top.get("composite_score") or 0)
        top_dir = str(top.get("direction") or "LONG").upper()

        if entered > 0:
            for c in candidates:
                if str(c.get("decision") or "") == "entered":
                    entered_sym = _clean_sym(str(c.get("symbol") or ""))
                    break
            entered_sym = entered_sym or top_sym
        elif scanned == 0:
            block = "no symbols passed the scanner"
        else:
            # Use the top candidate's own decision as ground truth
            decision = str(top.get("decision") or "")
            econ_raw = str(top.get("econ_reject_reason") or "")
            block_raw = str(top.get("trade_blocked_reason") or "")

            if decision == "below_threshold":
                below_reason = _BELOW_THRESHOLD_REASONS.get(block_raw)
                if below_reason:
                    block = f"{top_sym} scored {top_score:.0f} — {below_reason}"
                else:
                    block = f"best score was {top_sym} at {top_score:.0f} — not strong enough yet"
            elif decision == "econ_veto":
                reason = _ECON_REASONS.get(
                    econ_raw,
                    econ_raw.replace("_", " ")
                    if econ_raw
                    else "economics check failed",
                )
                block = f"{top_sym} scored {top_score:.0f} but skipped — {reason}"
            elif block_raw in _BLOCK_REASONS:
                block = (
                    f"{top_sym} scored {top_score:.0f} — {_BLOCK_REASONS[block_raw]}"
                )
            elif block_raw:
                block = (
                    f"{top_sym} scored {top_score:.0f} — {block_raw.replace('_', ' ')}"
                )
            else:
                block = f"best was {top_sym} at {top_score:.0f} — waiting for stronger setup"

        results.append(
            {
                "ts": ts,
                "scanned": scanned,
                "entered": entered,
                "entered_sym": entered_sym,
                "top_symbol": top_sym,
                "top_score": top_score,
                "top_dir": top_dir,
                "block": block,
            }
        )
    return results
