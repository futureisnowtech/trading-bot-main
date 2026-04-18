"""
dashboard/data/scanner_data.py — Scanner status, funnel steps, log summary.
Named scanner_data to avoid shadowing the project-root scanner.py.
"""

import re
from datetime import datetime, timedelta

from db import _q, _q1, _tail_log, LOG_PATH


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
    # Use runtime paper flag instead of hardcoded paper=1
    from db import _runtime_paper_flag

    _paper_flag = _runtime_paper_flag()
    re2 = _q1(
        "SELECT COUNT(*) AS n FROM trades "
        "WHERE datetime(replace(substr(ts,1,19),'T',' ')) >= ? "
        "AND paper=? AND action IN ('BUY','SELL') AND pnl_usd=0",
        (cutoff_1h, _paper_flag),
    )
    entry_count_1h = re2.get("n") or 0
    return {
        "buckets": buckets,
        "error_count_1h": error_count_1h,
        "veto_count_1h": veto_count_1h,
        "entry_count_1h": entry_count_1h,
    }
