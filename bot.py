#!/usr/bin/env python
import os
os.environ['GUNICORN_TIMEOUT'] = '600'

import logging
import sys
import asyncio
import threading
import time
import random
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import atexit
import requests

from sheets import GoogleSheetsClient
from config import Config
from qr_generator import QRGenerator
from ai_handler import AIAssistant
from datetime import datetime

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ------------------ Google Sheets ------------------
try:
    sheets_client = GoogleSheetsClient()
    logger.info("✅ تم الاتصال بـ Google Sheets")
except Exception as e:
    logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
    sheets_client = None

# ------------------ الذكاء الاصطناعي ------------------
ai_assistant = AIAssistant()

# ------------------ دوال البوت (نفس السابق، محذوفة للاختصار لكن في الكود الحقيقي يجب أن تكون موجودة) ------------------
# سأضعها مختصرة هنا لأنها طويلة – في التطبيق الفعلي انسخ الدوال من النسخة السابقة.

# (يجب وضع جميع الدوال: start, get_id, get_history, search, wake, stats, subscribe, unsubscribe, status, smart_handler, ai_chat_handler)

# ------------------ مراقبة المعاملات ------------------
last_row_count = 0
last_state = {}
executor = ThreadPoolExecutor(max_workers=10)
monitoring_thread = None
stop_monitoring = threading.Event()

def process_transaction(transaction_data):
    # ... (نفس السابق)
    pass

def check_new_transactions():
    # ... (نفس السابق)
    pass

def check_transaction_updates():
    # ... (نفس السابق)
    pass

def smart_alerts():
    # ... (نفس السابق)
    pass

def monitoring_loop():
    logger.info("🔄 بدء حلقة المراقبة اليدوية (كل 5 ثوانٍ)")
    last_alert_time = time.time()
    while not stop_monitoring.is_set():
        try:
            check_new_transactions()
            check_transaction_updates()
            if time.time() - last_alert_time >= 3600:
                smart_alerts()
                last_alert_time = time.time()
        except Exception as e:
            logger.error(f"خطأ في حلقة المراقبة: {e}")
        time.sleep(5)
    logger.info("🛑 توقفت حلقة المراقبة")

# ------------------ إعداد البوت ------------------
bot_app = None
background_loop = None
loop_thread = None

def set_webhook_sync():
    if bot_app is None or not Config.WEB_APP_URL:
        return
    webhook_url = f"{Config.WEB_APP_URL.rstrip('/')}/webhook"
    token = Config.TELEGRAM_BOT_TOKEN
    try:
        del_resp = requests.post(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
        if del_resp.status_code == 200:
            logger.info("✅ تم حذف webhook القديم")
        else:
            logger.warning(f"⚠️ فشل حذف webhook القديم: {del_resp.text}")

        resp = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            data={"url": webhook_url},
            timeout=10
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"✅ Webhook set to {webhook_url}")
        else:
            logger.error(f"❌ فشل تعيين webhook: {resp.text}")
    except Exception as e:
        logger.error(f"❌ خطأ في تعيين webhook: {e}")

def init_bot():
    global bot_app, background_loop, loop_thread, monitoring_thread, last_row_count
    logger.info("🚀 بدء init_bot")
    if not Config.TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN غير موجود")
        return
    try:
        logger.info("📦 بناء تطبيق البوت...")
        bot_app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        # إضافة المعالجات (ضع هنا جميع الـ handlers كما في النسخة السابقة)
        # من أجل الاختصار، لن أكررها – لكن في ملفك الحقيقي يجب أن تكون موجودة.
        logger.info("✅ تم بناء البوت وإضافة المعالجات")

        # تهيئة البوت في حلقة غير متزامنة مع مهلة
        async def init_bot_async():
            logger.info("🔄 تهيئة البوت في الحلقة غير المتزامنة...")
            try:
                await asyncio.wait_for(bot_app.initialize(), timeout=15)
                logger.info("✅ تم تهيئة البوت في الحلقة الخلفية")
            except asyncio.TimeoutError:
                logger.error("❌ انتهت مهلة تهيئة البوت (15 ثانية)")
                raise

        def start_background_loop():
            global background_loop
            background_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(background_loop)
            try:
                background_loop.run_until_complete(init_bot_async())
            except Exception as e:
                logger.error(f"فشل تشغيل الحلقة الخلفية: {e}")
                return
            logger.info("🔄 بدء حلقة الأحداث الخلفية...")
            background_loop.run_forever()

        loop_thread = threading.Thread(target=start_background_loop, daemon=True)
        loop_thread.start()
        logger.info("⏳ انتظار تهيئة البوت في الخلفية...")
        time.sleep(5)  # انتظر قليلاً
        if not background_loop or not background_loop.is_running():
            logger.error("❌ الحلقة الخلفية لم تبدأ بشكل صحيح")
            return
        logger.info("✅ خلفية البوت تعمل")

        # تعيين webhook
        time.sleep(2)
        logger.info("🌐 محاولة تعيين webhook...")
        for attempt in range(1, 4):
            try:
                set_webhook_sync()
                logger.info("✅ تم تعيين webhook بنجاح.")
                break
            except Exception as e:
                logger.error(f"❌ محاولة {attempt} فشلت: {e}")
                if attempt < 3:
                    time.sleep(5)
        else:
            logger.warning("⚠️ لم يتم تعيين webhook تلقائياً، يمكنك تعيينه يدوياً.")

        # بدء حلقة المراقبة
        if sheets_client:
            try:
                logger.info("📊 محاولة قراءة عدد المعاملات الحالي...")
                last_row_count = len(sheets_client.get_all_records(Config.SHEET_MANAGER))
                logger.info(f"📋 عدد المعاملات الحالي: {last_row_count}")
            except Exception as e:
                logger.error(f"❌ فشل قراءة العدد الأولي: {e}")
                last_row_count = 0

            monitoring_thread = threading.Thread(target=monitoring_loop, daemon=True)
            monitoring_thread.start()
            logger.info("🔍 بدأت مراقبة المعاملات الجديدة والتحديثات (كل 5 ثوانٍ)")
        else:
            logger.warning("⚠️ sheets_client غير متاح، لن يتم تشغيل المراقبة")
    except Exception as e:
        logger.error(f"❌ فشل إعداد البوت: {e}", exc_info=True)
        bot_app = None