"""
telegram_daemon.py — Standalone Telegram/SRE Oracle process.
"""

import asyncio
import logging
import sys

from notifications.telegram_bot import run_bot


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
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
