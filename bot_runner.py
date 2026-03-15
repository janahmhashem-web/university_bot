#!/usr/bin/env python
import logging
import sys
from config import Config
from telegram_bot import TransactionBot

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def main():
    try:
        bot = TransactionBot(Config.TELEGRAM_BOT_TOKEN)
        bot.sheets.ensure_sheets_exist()
        logger.info("✅ البوت جاهز (Polling كل ثانية)")
        bot.app.run_polling(poll_interval=1.0)
    except Exception as e:
        logger.error(f"❌ فشل تشغيل البوت: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()