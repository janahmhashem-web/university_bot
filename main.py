#!/usr/bin/env python
import logging
import sys
import os
import json
import asyncio
import threading
import time
import random
from flask import Flask, request, jsonify, render_template_string
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit
import requests

from sheets import GoogleSheetsClient
from config import Config
from email_service import EmailService
from qr_generator import QRGenerator
from ai_handler import AIAssistant
from datetime import datetime

# ------------------ إعداد التسجيل ------------------
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

app = Flask(__name__)

# ------------------ الذكاء الاصطناعي ------------------
ai_assistant = AIAssistant()

# ------------------ دوال البوت الأساسية ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)
    msg = "👋 *مرحباً بك في بوت متابعة المعاملات*\n\n"
    msg += "📌 *الأوامر العامة:*\n"
    msg += "🔹 /id [رقم] - تفاصيل معاملة\n"
    msg += "🔹 /history [رقم] - سجل تتبع معاملة\n"
    msg += "🔹 /search [كلمة] - بحث في المعاملات\n"
    msg += "🔹 /wake - للتأكد من أن البوت يعمل\n"
    if is_admin:
        msg += "\n👑 *أوامر المدير:*\n"
        msg += "🔹 /stats - إحصائيات عامة\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات حالياً.")
        return
    if context.args:
        transaction_id = context.args[0]
        row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
        if row_info:
            data = row_info['data']
            msg = f"🔍 *تفاصيل المعاملة {transaction_id}:*\n"
            for key in ['اسم صاحب المعاملة الثلاثي', 'الحالة', 'الموظف المسؤول']:
                if key in data:
                    msg += f"• {key}: {data[key]}\n"
            msg += f"\n🔗 [رابط المتابعة]({Config.WEB_APP_URL}/transaction/{transaction_id})"
            await update.message.reply_text(msg, parse_mode='Markdown', disable_web_page_preview=True)
        else:
            await update.message.reply_text(f"❌ لا توجد معاملة بالرقم {transaction_id}")
    else:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /id 123")

async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if context.args:
        transaction_id = context.args[0]
        try:
            ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
            if not ws:
                await update.message.reply_text("❌ لا يوجد سجل تاريخ.")
                return
            records = ws.get_all_records()
            history = [r for r in records if str(r.get('ID')) == transaction_id]
            if history:
                msg = f"📜 *سجل تتبع المعاملة {transaction_id}:*\n"
                for entry in history[-5:]:
                    time_str = entry.get('timestamp', '')
                    action = entry.get('action', '')
                    msg += f"• {time_str}: {action}\n"
                await update.message.reply_text(msg, parse_mode='Markdown')
            else:
                await update.message.reply_text(f"لا يوجد سجل للمعاملة {transaction_id}")
        except Exception as e:
            logger.error(f"خطأ في history: {e}")
            await update.message.reply_text("حدث خطأ أثناء جلب السجل.")
    else:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /history 123")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if context.args:
        keyword = ' '.join(context.args)
        records = sheets_client.get_all_records(Config.SHEET_MANAGER)
        found = []
        for r in records:
            if keyword in str(r.values()):
                found.append(r.get('ID', ''))
        if found:
            await update.message.reply_text(f"🔎 المعاملات التي تحتوي على '{keyword}':\n" + "\n".join(found[:10]))
        else:
            await update.message.reply_text("لا توجد نتائج.")
    else:
        await update.message.reply_text("الرجاء إدخال كلمة للبحث: /search كلمة")

async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ البوت نشط وجاهز!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != Config.ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح فقط للمدير.")
        return
    if not sheets_client:
        await update.message.reply_text("⚠️ غير متصل بقاعدة البيانات.")
        return
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
    total = len(records)
    completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
    pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
    msg = f"📊 *إحصائيات*\nإجمالي المعاملات: {total}\nمكتملة: {completed}\nقيد المعالجة: {pending}"
    await update.message.reply_text(msg, parse_mode='Markdown')

# ------------------ معالج الذكاء الاصطناعي ------------------
async def ai_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or ""
    logger.info(f"🤖 استعلام ذكي من {user_name}: {user_message[:50]}...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    response = await ai_assistant.get_response(user_message, user_id, user_name)
    await update.message.reply_text(response)

# ------------------ إعداد البوت وحلقة الأحداث الخلفية ------------------
bot_app = None
background_loop = None
loop_thread = None

if Config.TELEGRAM_BOT_TOKEN:
    try:
        bot_app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start))
        bot_app.add_handler(CommandHandler("id", get_id))
        bot_app.add_handler(CommandHandler("history", get_history))
        bot_app.add_handler(CommandHandler("search", search))
        bot_app.add_handler(CommandHandler("wake", wake))
        bot_app.add_handler(CommandHandler("stats", stats))
        # معالج الذكاء الاصطناعي للرسائل النصية العادية
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_handler))
        logger.info("✅ تم بناء البوت وإضافة المعالجات (بما في ذلك الذكاء الاصطناعي)")

        async def init_bot():
            await bot_app.initialize()
            logger.info("✅ تم تهيئة البوت في الحلقة الخلفية")

        def start_background_loop():
            global background_loop
            background_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(background_loop)
            background_loop.run_until_complete(init_bot())
            background_loop.run_forever()

        loop_thread = threading.Thread(target=start_background_loop, daemon=True)
        loop_thread.start()
        logger.info("⏳ انتظار تهيئة البوت في الخلفية...")
        time.sleep(2)
    except Exception as e:
        logger.error(f"❌ فشل إعداد البوت: {e}")
        bot_app = None

# ------------------ Webhook ------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    if bot_app is None or background_loop is None:
        return "Bot not initialized", 500
    try:
        logger.info("📩 تم استقبال طلب webhook")
        json_str = request.get_data(as_text=True)
        update = Update.de_json(json.loads(json_str), bot_app.bot)
        asyncio.run_coroutine_threadsafe(bot_app.process_update(update), background_loop)
        return "OK"
    except Exception as e:
        logger.error(f"خطأ في webhook: {e}")
        return "Error", 500

def set_webhook_sync():
    if bot_app is None or not Config.WEB_APP_URL:
        return
    webhook_url = f"{Config.WEB_APP_URL.rstrip('/')}/webhook"
    token = Config.TELEGRAM_BOT_TOKEN
    try:
        del_resp = requests.post(f"https://api.telegram.org/bot{token}/deleteWebhook")
        if del_resp.status_code == 200:
            logger.info("✅ تم حذف webhook القديم")
        else:
            logger.warning(f"⚠️ فشل حذف webhook القديم: {del_resp.text}")

        resp = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            data={"url": webhook_url}
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"✅ Webhook set to {webhook_url}")
        else:
            logger.error(f"❌ فشل تعيين webhook: {resp.text}")
    except Exception as e:
        logger.error(f"❌ خطأ في تعيين webhook: {e}")

if Config.WEB_APP_URL and bot_app:
    def delayed_webhook():
        time.sleep(5)
        set_webhook_sync()
    threading.Thread(target=delayed_webhook).start()
    logger.info("⏳ سيتم تعيين webhook بعد 5 ثوانٍ...")

# ------------------ مراقبة المعاملات الجديدة (كل 10 ثوانٍ) ------------------
last_row_count = 0

def check_new_transactions():
    global last_row_count
    try:
        if not sheets_client:
            logger.warning("⏳ sheets_client غير متصل، تخطي الدورة")
            return

        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            logger.error("❌ لا يمكن الوصول إلى ورقة manager")
            return

        records = ws.get_all_records()
        current_count = len(records)
        logger.info(f"📊 عدد السجلات الحالي: {current_count}, آخر عدد معروف: {last_row_count}")

        if current_count > last_row_count:
            logger.info(f"📦 تم اكتشاف {current_count - last_row_count} معاملات جديدة")
            for i in range(last_row_count, current_count):
                row_number = i + 2
                new_row = records[i]
                transaction_id = new_row.get('ID')
                if not transaction_id:
                    now = datetime.now()
                    date_str = now.strftime("%Y%m%d%H%M%S")
                    random_part = random.randint(1000, 9999)
                    transaction_id = f"MUT-{date_str}-{random_part}"
                    try:
                        # كتابة ID في العمود H (8)
                        ws.update_cell(row_number, 8, transaction_id)
                        logger.info(f"🆔 تم توليد ID {transaction_id} للصف {row_number}")

                        # كتابة رابط المعاملة في العمود U (21)
                        transaction_link = f"{Config.WEB_APP_URL}/transaction/{transaction_id}"
                        ws.update_cell(row_number, 21, transaction_link)
                        logger.info(f"🔗 تم كتابة الرابط في العمود U للصف {row_number}")

                        # تهيئة عمود V (آخر تعديل بواسطة) – يترك فارغاً
                        ws.update_cell(row_number, 22, "")
                        # تهيئة عمود W (آخر تعديل بتاريخ) – وقت الإنشاء
                        ws.update_cell(row_number, 23, now.isoformat())
                        # تهيئة عمود X (عدد التعديلات) – 0
                        ws.update_cell(row_number, 24, 0)

                        # إدراج صف في شيت QR
                        qr_ws = sheets_client.get_worksheet(Config.SHEET_QR)
                        if qr_ws:
                            name = new_row.get('اسم صاحب المعاملة الثلاثي', '')
                            email = new_row.get('البريد الإلكتروني', '')
                            qr_image_url = QRGenerator.get_qr_url(transaction_link)
                            qr_ws.append_row([
                                name,
                                email,
                                transaction_id,
                                transaction_link,
                                qr_image_url,
                                transaction_link
                            ])
                            logger.info(f"📸 تم إدراج بيانات QR للمعاملة {transaction_id}")

                    except Exception as e:
                        logger.error(f"❌ فشل كتابة البيانات للصف {row_number}: {e}")
                        continue

                customer_email = new_row.get('البريد الإلكتروني')
                customer_name = new_row.get('اسم صاحب المعاملة الثلاثي')
                if transaction_id and customer_email:
                    try:
                        transaction_link = f"{Config.WEB_APP_URL}/transaction/{transaction_id}"
                        qr_url = QRGenerator.get_qr_url(transaction_link)
                        EmailService.send_customer_email(
                            customer_email,
                            customer_name,
                            transaction_id,
                            qr_url
                        )
                        logger.info(f"📧 تم إرسال إيميل للمعاملة {transaction_id}")

                        history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
                        if history_ws:
                            history_ws.append_row([
                                datetime.now().isoformat(),
                                transaction_id,
                                "تم إنشاء المعاملة",
                                "النظام"
                            ])
                    except Exception as e:
                        logger.error(f"❌ فشل إرسال إيميل للمعاملة {transaction_id}: {e}")
                else:
                    logger.warning(f"⚠️ بيانات ناقصة للمعاملة: ID={transaction_id}, email={customer_email}, name={customer_name}")
            last_row_count = current_count
    except Exception as e:
        logger.error(f"❌ خطأ في دالة المراقبة: {e}", exc_info=True)

if sheets_client:
    try:
        last_row_count = len(sheets_client.get_all_records(Config.SHEET_MANAGER))
    except Exception as e:
        logger.error(f"❌ فشل قراءة العدد الأولي: {e}")
        last_row_count = 0

    scheduler = BackgroundScheduler()
    scheduler.start()
    scheduler.add_job(
        func=check_new_transactions,
        trigger=IntervalTrigger(seconds=10),
        id='check_transactions',
        replace_existing=True
    )
    logger.info("🔍 بدأت مراقبة المعاملات الجديدة باستخدام APScheduler (كل 10 ثوانٍ)")
    atexit.register(lambda: scheduler.shutdown())

# ------------------ نقاط نهاية API ------------------
@app.route('/api/transactions', methods=['GET'])
def api_transactions():
    if not sheets_client:
        return jsonify([])
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
    result = [{
        'id': r.get('ID', ''),
        'name': r.get('اسم صاحب المعاملة الثلاثي', ''),
        'status': r.get('الحالة', ''),
        'employee': r.get('الموظف المسؤول', '')
    } for r in records]
    return jsonify(result)

@app.route('/api/transaction/<id>', methods=['GET', 'POST'])
def api_transaction(id):
    if not sheets_client:
        return jsonify({'success': False, 'message': 'غير متصل بـ Google Sheets'}), 500

    if request.method == 'GET':
        data = sheets_client.get_row_by_id(Config.SHEET_MANAGER, id)
        if not data:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(data['data'])

    else:
        updates = request.json
        row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, id)
        if not row_info:
            return jsonify({'success': False, 'message': 'المعاملة غير موجودة'})
        row = row_info['row']
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        headers = ws.row_values(1)

        for key, value in updates.items():
            if key in headers:
                col = headers.index(key) + 1
                ws.update_cell(row, col, value)

        # تحديث عمود V (آخر تعديل بواسطة) باسم الموظف المسؤول
        employee_name = updates.get('الموظف المسؤول', 'غير معروف')
        if 'آخر تعديل بواسطة' in headers:
            col_v = headers.index('آخر تعديل بواسطة') + 1
            ws.update_cell(row, col_v, employee_name)
        else:
            ws.update_cell(row, 22, employee_name)

        # تحديث عمود W (آخر تعديل بتاريخ)
        now = datetime.now().isoformat()
        if 'آخر تعديل بتاريخ' in headers:
            col_w = headers.index('آخر تعديل بتاريخ') + 1
            ws.update_cell(row, col_w, now)
        else:
            ws.update_cell(row, 23, now)

        # تحديث عمود X (عدد التعديلات)
        try:
            current_count_cell = ws.cell(row, 24).value
            current_count = int(current_count_cell) if current_count_cell and str(current_count_cell).isdigit() else 0
        except:
            current_count = 0
        new_count = current_count + 1
        if 'عدد التعديلات' in headers:
            col_x = headers.index('عدد التعديلات') + 1
            ws.update_cell(row, col_x, new_count)
        else:
            ws.update_cell(row, 24, new_count)

        # تسجيل الحركة في TransactionHistory
        try:
            history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
            if history_ws:
                history_ws.append_row([
                    datetime.now().isoformat(),
                    id,
                    f"تم تحديث الحقول: {', '.join(updates.keys())}",
                    employee_name
                ])
        except Exception as e:
            logger.error(f"فشل تسجيل التاريخ: {e}")

        # إرسال إشعار للمدير عبر البوت
        if Config.ADMIN_CHAT_ID and background_loop and bot_app:
            try:
                asyncio.run_coroutine_threadsafe(
                    bot_app.bot.send_message(
                        chat_id=Config.ADMIN_CHAT_ID,
                        text=f"✏️ *تحديث معاملة*\n"
                             f"المعاملة: {id}\n"
                             f"تم تعديل الحقول: {', '.join(updates.keys())}\n"
                             f"بواسطة: {employee_name}",
                        parse_mode='Markdown'
                    ),
                    background_loop
                )
            except Exception as e:
                logger.error(f"فشل إرسال إشعار البوت: {e}")

        return jsonify({'success': True, 'message': 'تم الحفظ بنجاح'})

@app.route('/api/history/<id>')
def api_transaction_history(id):
    if not sheets_client:
        return jsonify([])
    try:
        ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        if not ws:
            return jsonify([])
        records = ws.get_all_records()
        history = [{'time': r.get('timestamp', ''), 'action': r.get('action', '')}
                   for r in records if str(r.get('ID')) == id]
        history.sort(key=lambda x: x['time'], reverse=True)
        return jsonify(history)
    except Exception as e:
        logger.error(f"خطأ في جلب التاريخ: {e}")
        return jsonify([])

# ------------------ صفحات HTML ------------------
INDEX_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>المعاملات</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-6xl mx-auto">
        <h1 class="text-2xl font-bold mb-4">📋 جميع المعاملات</h1>
        <div class="bg-white rounded-xl shadow overflow-x-auto">
            <table class="min-w-full">
                <thead class="bg-gray-50">
                    <tr>
                        <th class="px-4 py-2 text-right">ID</th>
                        <th class="px-4 py-2 text-right">الاسم</th>
                        <th class="px-4 py-2 text-right">الحالة</th>
                        <th class="px-4 py-2 text-right">الموظف</th>
                        <th class="px-4 py-2 text-right"></th>
                    </tr>
                </thead>
                <tbody id="transactions"></tbody>
            </table>
        </div>
    </div>
    <script>
        fetch('/api/transactions').then(r=>r.json()).then(data => {
            const tbody = document.getElementById('transactions');
            data.forEach(t => {
                const row = `<tr class="border-t">
                    <td class="px-4 py-2">${t.id}</td>
                    <td class="px-4 py-2">${t.name}</td>
                    <td class="px-4 py-2">${t.status}</td>
                    <td class="px-4 py-2">${t.employee}</td>
                    <td class="px-4 py-2"><a href="/transaction/${t.id}" class="text-blue-500 underline">✏️ تعديل</a></td>
                </tr>`;
                tbody.innerHTML += row;
            });
        });
    </script>
</body>
</html>"""

EDIT_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>تفاصيل المعاملة</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap');
        * { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
        .ios-card { background: rgba(255,255,255,0.8); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.3); }
        .ios-input { background: rgba(249,250,251,0.9); border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 16px; font-size: 16px; transition: all 0.2s; }
        .ios-input:focus { border-color: #007aff; outline: none; box-shadow: 0 0 0 3px rgba(0,122,255,0.1); }
        .label-ios { font-size: 14px; font-weight: 600; color: #6b7280; margin-bottom: 4px; display: block; }
        .timeline-item { border-right: 2px solid #007aff; position: relative; padding-right: 20px; margin-bottom: 20px; }
        .timeline-dot { width: 12px; height: 12px; background: #007aff; border-radius: 50%; position: absolute; right: -7px; top: 5px; }
    </style>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-3xl mx-auto">
        <div class="ios-card rounded-2xl p-4 mb-4 shadow-sm flex justify-between items-center">
            <h1 class="text-xl font-semibold text-gray-800">🔍 تتبع المعاملة <span id="transaction-id" class="text-blue-600"></span></h1>
            <a href="/" class="text-blue-500 text-sm">← العودة</a>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold text-gray-700 mb-3">📋 معلومات أساسية</h2>
            <div id="readonly-fields" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold text-gray-700 mb-3">✏️ تحديث البيانات</h2>
            <form id="editForm" class="space-y-4">
                <div id="editable-fields" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
                <button type="submit" class="w-full bg-blue-500 hover:bg-blue-600 text-white font-medium py-3 px-4 rounded-xl transition duration-200 shadow-sm">💾 حفظ التغييرات</button>
            </form>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold text-gray-700 mb-3">📜 سجل الحركات</h2>
            <div id="history-timeline" class="space-y-2"></div>
        </div>

        <div id="message" class="fixed bottom-4 left-1/2 transform -translate-x-1/2 bg-gray-800 text-white px-6 py-3 rounded-xl shadow-lg opacity-0 transition-opacity duration-300"></div>
    </div>

    <script>
        const id = window.location.pathname.split('/').pop();
        document.getElementById('transaction-id').innerText = id;

        function showMessage(text, isError = false) {
            const msgDiv = document.getElementById('message');
            msgDiv.innerText = text;
            msgDiv.classList.remove('opacity-0');
            msgDiv.classList.add('opacity-100');
            if (isError) msgDiv.classList.add('bg-red-600');
            else msgDiv.classList.remove('bg-red-600');
            setTimeout(() => msgDiv.classList.remove('opacity-100'), 3000);
        }

        fetch(`/api/transaction/${id}`)
            .then(res => res.ok ? res.json() : Promise.reject('فشل التحميل'))
            .then(data => {
                const readonlyKeys = [
                    'Timestamp', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف', 'البريد الإلكتروني',
                    'القسم', 'نوع المعاملة', 'المرافقات'
                ];
                const readonlyContainer = document.getElementById('readonly-fields');
                readonlyContainer.innerHTML = '';
                readonlyKeys.forEach(key => {
                    if (data[key] !== undefined) {
                        const value = data[key] || '-';
                        let display = value;
                        if (key === 'المرافقات' && value.startsWith('http')) {
                            display = `<a href="${value}" target="_blank" class="text-blue-500 underline">📎 فتح المرفق</a>`;
                        }
                        readonlyContainer.innerHTML += `
                            <div class="bg-gray-50 p-3 rounded-xl">
                                <span class="label-ios">${key}</span>
                                <div class="text-gray-900 mt-1">${display}</div>
                            </div>
                        `;
                    }
                });

                const editableKeys = Object.keys(data).filter(k => !readonlyKeys.includes(k) && k !== 'ID' && k !== 'LOG_JSON');
                const editableContainer = document.getElementById('editable-fields');
                editableContainer.innerHTML = '';
                editableKeys.forEach(key => {
                    editableContainer.innerHTML += `
                        <div>
                            <label class="label-ios">${key}</label>
                            <input type="text" name="${key}" value="${data[key] || ''}" class="ios-input w-full">
                        </div>
                    `;
                });
            })
            .catch(err => {
                document.body.innerHTML = '<div class="text-center text-red-500 p-10">❌ المعاملة غير موجودة</div>';
            });

        document.getElementById('editForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const updates = Object.fromEntries(formData.entries());

            const res = await fetch(`/api/transaction/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            });
            const result = await res.json();
            if (result.success) {
                showMessage('✅ تم الحفظ بنجاح');
                loadHistory();
            } else {
                showMessage('❌ فشل الحفظ', true);
            }
        });

        function loadHistory() {
            fetch(`/api/history/${id}`)
                .then(res => res.json())
                .then(history => {
                    const timeline = document.getElementById('history-timeline');
                    if (history.length === 0) {
                        timeline.innerHTML = '<p class="text-gray-500">لا يوجد سجل بعد</p>';
                        return;
                    }
                    let html = '';
                    history.forEach(item => {
                        html += `
                            <div class="timeline-item">
                                <span class="timeline-dot"></span>
                                <span class="text-sm text-gray-500">${item.time}</span>
                                <p class="text-gray-800">${item.action}</p>
                            </div>
                        `;
                    });
                    timeline.innerHTML = html;
                });
        }

        loadHistory();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/transaction/<id>')
def edit_transaction_page(id):
    return render_template_string(EDIT_HTML)

@app.route('/test-email')
def test_email():
    success = EmailService.send_customer_email(Config.EMAIL_USER, "اختبار", "TEST123", "https://example.com/qr.png")
    return "تم الإرسال" if success else "فشل"

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)