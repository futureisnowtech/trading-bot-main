import logging
import asyncio
from telegram import Bot
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# Hardcoded production credentials as requested
TOKEN = '8681504660:AAGddi9r0PEtqC1TFA4973SwsgytRH3x5BU'
CHAT_ID = '8224826883'

async def send_telegram_message_async(text: str):
    """Asynchronous core for sending telegram messages."""
    try:
        bot = Bot(token=TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Telegram send error: {e}")

def send_message(text: str):
    """Synchronous wrapper for telegram messages."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're already in an event loop (e.g. some async broker),
            # we should probably use create_task, but for simplicity:
            asyncio.create_task(send_telegram_message_async(text))
        else:
            loop.run_until_complete(send_telegram_message_async(text))
    except Exception as e:
        # Fallback if no loop exists or other issues
        try:
            asyncio.run(send_telegram_message_async(text))
        except Exception as e2:
            logger.error(f"Telegram critical failure: {e2}")

def send_liftoff():
    """Specific message for system start."""
    send_message("🚀 <b>LIFTOFF</b>: The bot has completed its first cycle and is now live.")
