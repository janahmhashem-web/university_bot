#!/usr/bin/env python
import logging
import sys
import os
import json
import asyncio
import threading
import time
from flask import Flask, request, jsonify, render_template_string
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit
import requests  # لإرسال طلبات http متزامنة لتعيين webhook

from sheets import GoogleSheetsClient
from config import Config
from email_service import EmailService
from qr_generator import QRGenerator
from datetime import datetime

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# تهيئة Google Sheets Client
try:
    sheets_client = GoogleSheetsClient()
    logger.info("✅ تم الاتصال بـ Google Sheets")
except Exception as e:
    logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
    sheets_client = None

app = Flask(__name__)

# ---------- دوال البوت ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)
    msg = "👋 *مرحباً بك في بوت متابعة المعاملات*\n\n"
    msg += "📌 *الأوامر العامة:*\n"
    msg += "🔹 /id [رقم] - تفاصيل معاملة\n"
    msg += "🔹 /history [رقم] - سجل التتبع\n"
    msg += "🔹 /search [كلمة] - بحث\n"
    msg += "🔹 /wake - تحديث فوري\n"
    if is_admin:
        msg += "\n👑 *أوامر المدير:*\n"
        msg += "🔹 /stats - إحصائيات\n"
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
            for key, value in data.items():
                msg += f"• {key}: {value}\n"
            await update.message.reply_text(msg, parse_mode='Markdown')
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
                for entry in history:
                    msg += f"• {entry.get('تاريخ', '')}: {entry.get('حالة', '')}\n"
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
    await update.message.reply_text(f"📊 *إحصائيات*\nإجمالي المعاملات: {total}", parse_mode='Markdown')

# ---------- إعداد البوت مع حلقة أحداث خلفية ----------
bot_app = None
loop = None
thread = None

def start_bot_loop():
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_forever()

if Config.TELEGRAM_BOT_TOKEN:
    try:
        bot_app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start))
        bot_app.add_handler(CommandHandler("id", get_id))
        bot_app.add_handler(CommandHandler("history", get_history))
        bot_app.add_handler(CommandHandler("search", search))
        bot_app.add_handler(CommandHandler("wake", wake))
        bot_app.add_handler(CommandHandler("stats", stats))
        logger.info("✅ تم بناء البوت وإضافة المعالجات")
        
        # بدء حلقة الأحداث في خيط منفصل
        thread = threading.Thread(target=start_bot_loop, daemon=True)
        thread.start()
        # تهيئة البوت داخل الحلقة
        future = asyncio.run_coroutine_threadsafe(bot_app.initialize(), loop)
        future.result(timeout=10)
        logger.info("✅ تم تهيئة البوت")
    except Exception as e:
        logger.error(f"❌ فشل تهيئة البوت: {e}")
        bot_app = None

# ---------- Webhook ----------
@app.route('/webhook', methods=['POST'])
def webhook():
    if bot_app is None or loop is None:
        return "Bot not initialized", 500
    try:
        json_str = request.get_data(as_text=True)
        update = Update.de_json(json.loads(json_str), bot_app.bot)
        # إرسال المهمة إلى حلقة الأحداث الخلفية
        asyncio.run_coroutine_threadsafe(bot_app.process_update(update), loop)
        return "OK"
    except Exception as e:
        logger.error(f"خطأ في webhook: {e}")
        return "Error", 500

def set_webhook_sync():
    """تعيين webhook باستخدام requests (طريقة متزامنة موثوقة)"""
    if bot_app is None or not Config.WEB_APP_URL:
        logger.warning("لا يمكن تعيين webhook: bot_app أو WEB_APP_URL غير معرف")
        return
    webhook_url = f"{Config.WEB_APP_URL.rstrip('/')}/webhook"
    token = Config.TELEGRAM_BOT_TOKEN
    try:
        # حذف webhook القديم
        requests.post(f"https://api.telegram.org/bot{token}/deleteWebhook")
        # تعيين webhook الجديد
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

# تعيين webhook مع تأخير
if Config.WEB_APP_URL and bot_app:
    def delayed_webhook():
        time.sleep(5)
        set_webhook_sync()
    threading.Thread(target=delayed_webhook).start()
    logger.info("⏳ سيتم تعيين webhook بعد 5 ثوانٍ...")

# ---------- مراقبة المعاملات الجديدة باستخدام APScheduler ----------
last_row_count = 0

def check_new_transactions():
    global last_row_count
    try:
        if not sheets_client:
            logger.warning("⏳ sheets_client غير متصل، تخطي الدورة")
            return
        records = sheets_client.get_all_records(Config.SHEET_MANAGER)
        current_count = len(records)
        logger.info(f"📊 عدد السجلات الحالي: {current_count}, آخر عدد معروف: {last_row_count}")
        if current_count > last_row_count:
            logger.info(f"📦 تم اكتشاف {current_count - last_row_count} معاملات جديدة")
            for i in range(last_row_count, current_count):
                new_row = records[i]
                transaction_id = new_row.get('ID')
                customer_email = new_row.get('البريد الإلكتروني')
                customer_name = new_row.get('اسم صاحب المعاملة الثلاثي')
                if transaction_id and customer_email:
                    try:
                        qr_url = QRGenerator.get_qr_url(f"{Config.WEB_APP_URL}?id={transaction_id}")
                        EmailService.send_customer_email(
                            customer_email,
                            customer_name,
                            transaction_id,
                            qr_url
                        )
                        logger.info(f"📧 تم إرسال إيميل للمعاملة {transaction_id}")
                    except Exception as e:
                        logger.error(f"❌ فشل إرسال إيميل للمعاملة {transaction_id}: {e}")
                else:
                    logger.warning(f"⚠️ بيانات ناقصة للمعاملة: ID={transaction_id}, email={customer_email}, name={customer_name}")
            last_row_count = current_count
    except Exception as e:
        logger.error(f"❌ خطأ في دالة المراقبة: {e}", exc_info=True)

# جدولة المهمة
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
        trigger=IntervalTrigger(seconds=30),
        id='check_transactions',
        replace_existing=True
    )
    logger.info("🔍 بدأت مراقبة المعاملات الجديدة باستخدام APScheduler")
    atexit.register(lambda: scheduler.shutdown())

# ---------- صفحات HTML مبسطة ----------
INDEX_HTML = """
<!DOCTYPE html>
<html dir="rtl">
<head><meta charset="UTF-8"><title>المعاملات</title></head>
<body>
<h1>قائمة المعاملات</h1>
<div id="transactions"></div>
<script>
fetch('/api/transactions').then(r=>r.json()).then(data => {
    let html = '<table border="1"><tr><th>ID</th><th>الاسم</th><th>الحالة</th><th>الموظف</th></tr>';
    data.forEach(t => {
        html += `<tr><td>${t.id}</td><td>${t.name}</td><td>${t.status}</td><td>${t.employee}</td></tr>`;
    });
    html += '</table>';
    document.getElementById('transactions').innerHTML = html;
});
</script>
</body>
</html>
"""

EDIT_HTML = """
<!DOCTYPE html>
<html dir="rtl">
<head><meta charset="UTF-8"><title>تعديل معاملة</title></head>
<body>
<h1>تعديل المعاملة <span id="tid"></span></h1>
<div id="form"></div>
<script>
const urlParams = new URLSearchParams(window.location.search);
const id = window.location.pathname.split('/').pop();
document.getElementById('tid').innerText = id;

fetch(`/api/transaction/${id}`).then(r=>r.json()).then(data => {
    let form = '<form id="editForm">';
    for (let key in data) {
        form += `<label>${key}: <input name="${key}" value="${data[key]}"></label><br>`;
    }
    form += '<button type="submit">حفظ</button></form>';
    document.getElementById('form').innerHTML = form;

    document.getElementById('editForm').onsubmit = async (e) => {
        e.preventDefault();
        const formData = new FormData(e.target);
        const updates = Object.fromEntries(formData.entries());
        const res = await fetch(`/api/transaction/${id}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(updates)
        });
        const result = await res.json();
        alert(result.message);
    };
});
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

@app.route('/api/transactions')
def api_transactions():
    if not sheets_client:
        return jsonify([])
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
    result = []
    for r in records:
        result.append({
            'id': r.get('ID', ''),
            'name': r.get('اسم صاحب المعاملة الثلاثي', ''),
            'status': r.get('الحالة', ''),
            'employee': r.get('الموظف المسؤول', '')
        })
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
        if 'آخر تعديل بتاريخ' in headers:
            col = headers.index('آخر تعديل بتاريخ') + 1
            ws.update_cell(row, col, datetime.now().isoformat())
        return jsonify({'success': True, 'message': 'تم الحفظ بنجاح'})

@app.route('/test-email')
def test_email():
    success = EmailService.send_customer_email(Config.EMAIL_USER, "اختبار", "TEST123", "https://example.com/qr.png")
    return "تم الإرسال" if success else "فشل"

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)