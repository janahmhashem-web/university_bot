import os
import sys
import logging
import threading
import time
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests

from sheets import GoogleSheetsClient
from config import Config
from qr_generator import QRGenerator
from ai_handler import AIAssistant
from bot_handlers import (
    start, get_id, get_history, search, wake, stats,
    subscribe, unsubscribe, status, ai_chat_handler
)

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

# ------------------ AI ------------------
ai_assistant = AIAssistant()

# ------------------ FastAPI ------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # بدء البوت في خلفية
    threading.Thread(target=init_bot, daemon=True).start()
    yield

app = FastAPI(lifespan=lifespan)

# ------------------ Webhook ------------------
@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        asyncio.run_coroutine_threadsafe(
            bot_app.process_update(update),
            background_loop
        )
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return {"ok": False}

# ------------------ صفحات HTML (مبسطة) ------------------
INDEX_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head><meta charset="UTF-8"><title>المعاملات</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-100 p-4">
<div class="max-w-6xl mx-auto"><h1 class="text-2xl font-bold mb-4">📋 جميع المعاملات (المدير)</h1>
<div class="bg-white rounded-xl shadow overflow-x-auto"><table class="min-w-full"><thead class="bg-gray-50"><tr><th class="px-4 py-2 text-right">ID</th><th class="px-4 py-2 text-right">الاسم</th><th class="px-4 py-2 text-right">الحالة</th><th class="px-4 py-2 text-right">الموظف</th><th class="px-4 py-2 text-right"></th></tr></thead><tbody id="transactions"></tbody></table></div></div>
<script>fetch('/api/transactions').then(r=>r.json()).then(data=>{const tbody=document.getElementById('transactions');data.forEach(t=>{const row=`<tr class="border-t"><td class="px-4 py-2">${t.id}</td><td class="px-4 py-2">${t.name}</td><td class="px-4 py-2">${t.status}</td><td class="px-4 py-2">${t.employee}</td><td class="px-4 py-2"><a href="/transaction/${t.id}" class="text-blue-500 underline">✏️ تعديل</a></td></tr>`;tbody.innerHTML+=row;});});</script>
</body></html>"""

@app.get("/")
async def index():
    return HTMLResponse(INDEX_HTML)

@app.get("/api/transactions")
async def api_transactions():
    if not sheets_client:
        return JSONResponse([])
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
    result = [{
        'id': r.get('ID', ''),
        'name': r.get('اسم صاحب المعاملة الثلاثي', ''),
        'status': r.get('الحالة', ''),
        'employee': r.get('الموظف المسؤول', '')
    } for r in records]
    return JSONResponse(result)

# باقي المسارات (qr, transaction, view, new-transaction, etc.) يمكن إضافتها حسب الحاجة
# للاختصار، أضف مسار /qr و /view و /new-transaction كما في Flask سابقاً.

# ------------------ إعداد البوت ------------------
bot_app = None
background_loop = None

def set_webhook():
    if not Config.WEB_APP_URL or not Config.TELEGRAM_BOT_TOKEN:
        return
    webhook_url = f"{Config.WEB_APP_URL.rstrip('/')}/webhook"
    token = Config.TELEGRAM_BOT_TOKEN
    try:
        requests.post(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            data={"url": webhook_url},
            timeout=10
        )
        logger.info(f"Webhook set: {resp.json()}")
    except Exception as e:
        logger.error(f"Webhook error: {e}")

def init_bot():
    global bot_app, background_loop
    if not Config.TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN غير موجود")
        return

    bot_app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
    # تخزين dependencies في bot_data
    bot_app.bot_data['sheets_client'] = sheets_client
    bot_app.bot_data['ai_assistant'] = ai_assistant

    # إضافة المعالجات
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("id", get_id))
    bot_app.add_handler(CommandHandler("history", get_history))
    bot_app.add_handler(CommandHandler("search", search))
    bot_app.add_handler(CommandHandler("wake", wake))
    bot_app.add_handler(CommandHandler("stats", stats))
    bot_app.add_handler(CommandHandler("subscribe", subscribe))
    bot_app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    bot_app.add_handler(CommandHandler("status", status))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_handler))

    async def init_async():
        await bot_app.initialize()

    def run_loop():
        global background_loop
        background_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(background_loop)
        background_loop.run_until_complete(init_async())
        background_loop.run_forever()

    threading.Thread(target=run_loop, daemon=True).start()
    time.sleep(2)
    set_webhook()
    logger.info("✅ البوت جاهز")

# ------------------ تشغيل التطبيق ------------------
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)