#!/usr/bin/env python
import logging
import os
import json
import asyncio
from flask import Flask, request

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ------------------ الإعدادات الأساسية ------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# متغيرات البيئة (تأكد من ضبطها في Railway)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_CHAT_ID", 0))

# ------------------ دوال البوت ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"📨 /start من {update.effective_user.id}")
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_ID)
    msg = "👋 *مرحباً بك في البوت*\n\n"
    msg += "📌 الأوامر العامة:\n"
    msg += "• /id [رقم] - تفاصيل معاملة\n"
    msg += "• /history [رقم] - سجل التتبع\n"
    msg += "• /search [كلمة] - بحث\n"
    msg += "• /wake - تحديث فوري\n"
    if is_admin:
        msg += "\n👑 أوامر المدير:\n"
        msg += "• /stats - إحصائيات\n"
    await update.message.reply_text(msg, parse_mode='Markdown')
    logger.info("✅ تم الرد على /start")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ترد على أي رسالة نصية (للتأكيد أن البوت يعمل)"""
    logger.info(f"📨 رسالة: {update.message.text}")
    await update.message.reply_text(f"استقبلت: {update.message.text}")

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await update.message.reply_text(f"🔍 بحث عن معاملة: {context.args[0]}")
    else:
        await update.message.reply_text("الرجاء إدخال رقم: /id 123")

async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📜 سجل التتبع (قيد التطوير)")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 بحث (قيد التطوير)")

async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ البوت نشط!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ هذا الأمر للمدير فقط.")
        return
    await update.message.reply_text("📊 إحصائيات (قيد التطوير)")

# ------------------ بناء التطبيق ------------------
app = Application.builder().token(TOKEN).build()

# إضافة المعالجات
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("id", get_id))
app.add_handler(CommandHandler("history", get_history))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("wake", wake))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

# تهيئة البوت (مرة واحدة عند بدء التشغيل)
async def init_bot():
    await app.initialize()
    logger.info("✅ البوت مهيأ وجاهز")

asyncio.run(init_bot())

# ------------------ Flask Webhook ------------------
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    logger.info("📩 تم استقبال طلب webhook")
    try:
        json_str = request.get_data(as_text=True)
        update = Update.de_json(json.loads(json_str), app.bot)
        # معالجة التحديث في حلقة أحداث جديدة (آمن لـ Flask)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(app.process_update(update))
        loop.close()
        return "OK"
    except Exception as e:
        logger.error(f"❌ خطأ في webhook: {e}")
        return "Error", 500

@flask_app.route('/ping')
def ping():
    return "pong"

# ------------------ تشغيل الخادم ------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)