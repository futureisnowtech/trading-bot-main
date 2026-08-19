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

    # httpx logs the full request URL at INFO. Telegram embeds the bot token in
    # its path, so long-poll getUpdates was writing the live credential into
    # bot.log every ~10s -- tens of thousands of plaintext copies on disk.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

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
