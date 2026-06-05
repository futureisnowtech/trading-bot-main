"""Runtime storage maintenance for SQLite/WAL-backed lean Kalshi services."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from config import BOT_LOG_PATH, DB_PATH, FORECAST_LOG_PATH

DEFAULT_LOG_MAX_MB = 8
DEFAULT_WAL_CHECKPOINT_MB = 16
DEFAULT_LOG_BACKUPS = 2


def _file_size(path: str | Path) -> int:
    try:
        return int(Path(path).stat().st_size)
    except FileNotFoundError:
        return 0


def rotate_log_file(
    path: str | Path,
    *,
    max_mb: int = DEFAULT_LOG_MAX_MB,
    backups: int = DEFAULT_LOG_BACKUPS,
) -> dict:
    file_path = Path(path)
    max_bytes = max(1, int(max_mb)) * 1024 * 1024
    size_before = _file_size(file_path)

    if size_before <= max_bytes:
        return {
            "path": str(file_path),
            "rotated": False,
            "size_before": size_before,
            "size_after": size_before,
        }

    file_path.parent.mkdir(parents=True, exist_ok=True)
    oldest = Path(f"{file_path}.{backups}")
    if oldest.exists():
        oldest.unlink()

    for idx in range(max(1, backups) - 1, 0, -1):
        src = Path(f"{file_path}.{idx}")
        dst = Path(f"{file_path}.{idx + 1}")
        if src.exists():
            src.replace(dst)

    rotated_copy = Path(f"{file_path}.1")
    if file_path.exists():
        file_path.replace(rotated_copy)

    file_path.write_text("", encoding="utf-8")
    return {
        "path": str(file_path),
        "rotated": True,
        "size_before": size_before,
        "size_after": _file_size(file_path),
        "archive_path": str(rotated_copy),
    }


def checkpoint_sqlite_wal(
    db_path: str | Path = DB_PATH,
    *,
    wal_threshold_mb: int = DEFAULT_WAL_CHECKPOINT_MB,
) -> dict:
    db_file = Path(db_path)
    wal_file = Path(f"{db_file}-wal")
    wal_before = _file_size(wal_file)
    threshold_bytes = max(1, int(wal_threshold_mb)) * 1024 * 1024

    if wal_before <= threshold_bytes:
        return {
            "db_path": str(db_file),
            "checkpointed": False,
            "wal_bytes_before": wal_before,
            "wal_bytes_after": wal_before,
        }

    try:
        with sqlite3.connect(str(db_file), timeout=30.0) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        wal_after = _file_size(wal_file)
        return {
            "db_path": str(db_file),
            "checkpointed": True,
            "wal_bytes_before": wal_before,
            "wal_bytes_after": wal_after,
        }
    except Exception as exc:
        return {
            "db_path": str(db_file),
            "checkpointed": False,
            "wal_bytes_before": wal_before,
            "wal_bytes_after": _file_size(wal_file),
            "error": str(exc),
        }


def maintain_runtime_storage(
    *,
    bot_log_path: str = BOT_LOG_PATH,
    forecast_log_path: str = FORECAST_LOG_PATH,
    db_path: str = DB_PATH,
    log_max_mb: int = DEFAULT_LOG_MAX_MB,
    wal_threshold_mb: int = DEFAULT_WAL_CHECKPOINT_MB,
    backups: int = DEFAULT_LOG_BACKUPS,
) -> dict:
    """Run low-risk maintenance to keep runtime disk usage bounded."""
    return {
        "bot_log": rotate_log_file(bot_log_path, max_mb=log_max_mb, backups=backups),
        "forecast_log": rotate_log_file(
            forecast_log_path,
            max_mb=log_max_mb,
            backups=backups,
        ),
        "wal": checkpoint_sqlite_wal(db_path, wal_threshold_mb=wal_threshold_mb),
    }
