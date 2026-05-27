"""
monitoring/log_alerter.py — Telegram log watchdog daemon.

Tails logs/bot.log from the current end (no replay on startup).
For each new line that matches a watched namespace and level,
sends a Telegram alert rate-limited to 1 per 60s per namespace.

Usage:
    from monitoring.log_alerter import start_log_alerter
    start_log_alerter()
"""

import os
import time
import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)

LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "bot.log"
)

ALERT_NAMESPACES = [
    "spot_engine",
    "scheduler",
    "runtime.spot_position_truth",
    "runtime.spot_position_repair",
    "runtime.spot_kill_switch",
    "spot_kill_switch",
]

ALERT_LEVELS = {"WARNING", "ERROR", "CRITICAL"}

RATE_LIMIT_SECONDS = 60


class LogAlertWatchdog:
    def __init__(self):
        self.last_alert_times: dict[str, float] = {}
        self.log_path = LOG_PATH

    def _should_alert(self, namespace: str, level: str) -> bool:
        """Return True if the rate limit allows an alert for this namespace."""
        if level not in ALERT_LEVELS:
            return False
        now = time.monotonic()
        last = self.last_alert_times.get(namespace, 0.0)
        if now - last >= RATE_LIMIT_SECONDS:
            self.last_alert_times[namespace] = now
            return True
        return False

    def _parse_line(self, line: str) -> Optional[tuple[str, str]]:
        """
        Parse a log line in the format:
            2026-05-03 02:17:04,123 spot_engine WARNING some message

        Returns (namespace, level) if the line matches alert criteria, else None.
        """
        line = line.rstrip()
        if not line:
            return None

        # SILENCE FILTER: Do not spam Telegram for expected 401/Auth failures 
        # that are already being logged for the operator to see locally.
        silence_strings = ["401: Unauthorized", "Connection failed: Coinbase Spot API"]
        if any(s in line for s in silence_strings):
            return None

        parts = line.split()
        # Minimum: date time namespace level message...
        # parts[0] = date, parts[1] = time, parts[2] = namespace, parts[3] = level
        if len(parts) < 4:
            return None
        namespace = parts[2]
        level = parts[3]
        if namespace not in ALERT_NAMESPACES:
            return None
        if level not in ALERT_LEVELS:
            return None
        return (namespace, level)

    def _add_insight(self, line: str) -> str:
        """Translates terse system errors into human-readable strategic insights."""
        if "limit_order_rejected" in line:
            return "\n💡 <b>Insight:</b> Order canceled to protect capital. We use Maker-Only orders to avoid the 0.60% Taker fee. The price moved too fast, so the exchange safely rejected it rather than overcharging us."
        if "taker_fallback_disabled" in line:
            return "\n💡 <b>Insight:</b> Trade skipped. The Maker order didn't fill, and our strict Maker-Only policy blocked the Taker fallback to prevent unnecessary fee drag."
        if "Non-critical background state telemetry error" in line:
            return "\n💡 <b>Insight:</b> A background telemetry or API call timed out. The system safely caught it. No action needed."
        if "mixed_mode_paper_like_live_order" in line:
            return "\n💡 <b>Insight:</b> A catastrophic structural error was prevented. The bot caught a Paper order trying to execute in a Live lane and halted the system."
        return ""

    def tail_forever(self):
        """Main loop: open log file, seek to end, then read new lines continuously."""
        while True:
            if not os.path.exists(self.log_path):
                # Bot may not have started yet — wait silently
                time.sleep(5)
                continue
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as fh:
                    # Seek to end so we don't replay historical lines
                    fh.seek(0, 2)
                    while True:
                        line = fh.readline()
                        if not line:
                            time.sleep(1)
                            # Check if file was rotated (inode changed)
                            try:
                                if not os.path.exists(self.log_path):
                                    break
                                current_ino = os.fstat(fh.fileno()).st_ino
                                disk_ino = os.stat(self.log_path).st_ino
                                if current_ino != disk_ino:
                                    break
                            except OSError:
                                break
                            continue
                        parsed = self._parse_line(line)
                        if parsed is None:
                            continue
                        namespace, level = parsed
                        if self._should_alert(namespace, level):
                            try:
                                from notifications.telegram_bot import send_message
                                
                                insight = self._add_insight(line)
                                msg = (
                                    f"<b>[{level}] {namespace}</b>\n"
                                    f"<code>{line.strip()}</code>{insight}"
                                )
                                send_message(msg)
                            except Exception as send_exc:
                                logger.debug(f"log_alerter send failed: {send_exc}")
            except Exception as exc:
                logger.debug(f"log_alerter tail_forever error: {exc}")
                time.sleep(5)

    def start(self) -> threading.Thread:
        """Start tail_forever as a daemon thread. Returns the thread."""
        t = threading.Thread(
            target=self.tail_forever,
            daemon=True,
            name="LogAlertWatchdog",
        )
        t.start()
        return t


def start_log_alerter() -> threading.Thread:
    """Module-level convenience function to start the watchdog."""
    w = LogAlertWatchdog()
    return w.start()
