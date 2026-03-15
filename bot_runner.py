#!/usr/bin/env python
import logging
import sys
from config import Config

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def main():
    try:
        from telegram.ext import Application, CommandHandler
        async def start(update, context):
            await update.message.reply_text("مرحباً! البوت يعمل محلياً.")
        app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        logger.info("✅ البوت المحلي يعمل (Polling)")
        app.run_polling(poll_interval=1.0)
    except Exception as e:
        logger.error(f"❌ فشل تشغيل البوت المحلي: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()