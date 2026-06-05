#!/usr/bin/env python3
"""Report runtime storage pressure and common local dev-cache hotspots."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
os.chdir(_REPO_ROOT)
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import BOT_LOG_PATH, CSV_LOG_DIR, DB_PATH, FORECAST_LOG_PATH
from runtime.storage_guard import runtime_storage_status
from runtime.storage_maintenance import checkpoint_sqlite_wal


def _size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return int(path.stat().st_size)
    return sum(int(p.stat().st_size) for p in path.rglob("*") if p.is_file())


def _fmt_bytes(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{num}B"


def main() -> int:
    home = Path.home()
    targets = [
        ("runtime_dir", Path(DB_PATH).parent),
        ("db", Path(DB_PATH)),
        ("bot_log", Path(BOT_LOG_PATH)),
        ("forecast_log", Path(FORECAST_LOG_PATH)),
        ("csv_dir", Path(CSV_LOG_DIR)),
        ("gemini_tmp", home / ".gemini" / "tmp"),
        ("codex_cache", home / "Library" / "Caches" / "com.openai.codex"),
        ("google_cache", home / "Library" / "Caches" / "Google"),
        ("xcode", home / "Library" / "Developer" / "Xcode"),
    ]

    print("=== Storage Audit ===")
    headroom = runtime_storage_status()
    print(
        f"Disk headroom: {headroom['free_mb']:.0f}MB free "
        f"(threshold={headroom['threshold_mb']:.0f}MB) at {headroom['path']}"
    )

    for label, path in targets:
        print(f"{label:14} {_fmt_bytes(_size_bytes(path))}  {path}")

    wal = checkpoint_sqlite_wal(DB_PATH, wal_threshold_mb=999999)
    print(
        f"wal_bytes      {_fmt_bytes(int(wal['wal_bytes_before']))}  {DB_PATH}-wal"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
