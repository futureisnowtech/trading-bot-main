"""Shared logging setup for the lean Kalshi runtime."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from config import BOT_LOG_PATH, LOG_LEVEL

_RUNTIME_LOG_BYTES = 50 * 1024 * 1024
_RUNTIME_LOG_BACKUPS = 3


def configure_runtime_logging(*, log_path: str = BOT_LOG_PATH) -> None:
    """Configure root logging once with stream + rotating file handlers."""
    root = logging.getLogger()
    if getattr(root, "_sovereign_runtime_logging_configured", False):
        return

    level = getattr(logging, str(LOG_LEVEL or "INFO").upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    root.handlers.clear()
    root.setLevel(level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=_RUNTIME_LOG_BYTES,
            backupCount=_RUNTIME_LOG_BACKUPS,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    root._sovereign_runtime_logging_configured = True
