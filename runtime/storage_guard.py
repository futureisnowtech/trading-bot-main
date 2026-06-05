"""Low-disk guardrails for SQLite/WAL-backed runtime state."""

from __future__ import annotations

import os
import shutil

def runtime_storage_status(
    *, path: str | None = None, min_free_mb: int | None = None
) -> dict:
    from config import DB_PATH, MIN_FREE_DISK_MB

    target_path = path or os.path.dirname(DB_PATH) or "."
    threshold_mb = int(min_free_mb if min_free_mb is not None else MIN_FREE_DISK_MB)

    os.makedirs(target_path, exist_ok=True)
    usage = shutil.disk_usage(target_path)
    free_mb = usage.free / (1024 * 1024)

    return {
        "path": target_path,
        "free_mb": free_mb,
        "threshold_mb": float(threshold_mb),
        "ok": free_mb >= threshold_mb,
    }
