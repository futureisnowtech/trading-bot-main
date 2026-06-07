"""
telegram_daemon.py — Standalone Telegram/SRE Oracle process.
"""

import asyncio
import logging
import sys

from notifications.telegram_bot import run_bot
from runtime.logging_setup import configure_runtime_logging

configure_runtime_logging()
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)


def main() -> int:
    try:
        asyncio.run(run_bot())
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
