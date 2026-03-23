#!/usr/bin/env python
import os
import logging
import sys
import json
import asyncio
import threading
import time
import random
import base64
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, render_template_string, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests
import qrcode
from io import BytesIO
from datetime import datetime

# ---------- إعداد التسجيل ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ---------- إعدادات البيئة ----------
from dotenv import load_dotenv
load_dotenv()

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    BOT_USERNAME = os.getenv('BOT_USERNAME')
    ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '0'))
    SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
    WEB_APP_URL = os.getenv('WEB_APP_URL', 'https://your-app.up.railway.app')

    SHEET_MANAGER = "manager"
    SHEET_QR = "QR"
    SHEET_ARCHIVE = "Archive"
    SHEET_HISTORY = "TransactionHistory"
    SHEET_SUBSCRIBERS = "Subscribers"

# ---------- Google Sheets ----------
import gspread
from oauth2client.service_account import ServiceAccountCredentials

class GoogleSheetsClient:
    def __init__(self):
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if not creds_json:
                raise ValueError("❌ GOOGLE_CREDENTIALS_JSON غير موجود")
            creds_dict = json.loads(creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(Config.SPREADSHEET_ID)
            logger.info(f"✅ تم الاتصال بـ Google Sheets")
        except Exception as e:
            logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
            raise

    def get_worksheet(self, sheet_name):
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except Exception as e:
            logger.error(f"❌ فشل فتح الورقة {sheet_name}: {e}")
            return None

    def get_all_records(self, sheet_name):
        ws = self.get_worksheet(sheet_name)
        if ws:
            return ws.get_all_records()
        return []

    def get_row_by_id(self, sheet_name, transaction_id):
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return None
        records = ws.get_all_records()
        for idx, row in enumerate(records):
            if str(row.get('ID')) == str(transaction_id):
                return {'row': idx + 2, 'data': row}
        return None

# ---------- QR Generator ----------
class QRGenerator:
    @staticmethod
    def generate_qr(data, size=300):
        qr = qrcode.QRCode(version=1, box_size=10, border=5, error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img = img.resize((size, size))
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()

# ---------- AI Assistant (Groq) ----------
from openai import OpenAI

class AIAssistant:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv('GROQ_API_KEY'), base_url="https://api.groq.com/openai/v1")
        self.sheets = sheets_client  # سيتم تعيينه لاحقاً
        logger.info("✅ تم تهيئة Groq AI")

    async def get_response(self, user_message, user_id, user_name="", transaction_id=None):
        try:
            context = ""
            if transaction_id:
                row_info = self.sheets.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
                if row_info:
                    context = "\n".join([f"{k}: {v}" for k, v in row_info['data'].items() if v])
            else:
                subs_ws = self.sheets.get_worksheet(Config.SHEET_SUBSCRIBERS)
                if subs_ws:
                    records = subs_ws.get_all_records()
                    for r in records:
                        if str(r.get('user_id')) == str(user_id):
                            tx_id = r.get('transaction_id')
                            if tx_id:
                                row_info = self.sheets.get_row_by_id(Config.SHEET_MANAGER, tx_id)
                                if row_info:
                                    context = "\n".join([f"{k}: {v}" for k, v in row_info['data'].items() if v])
                                    break
            try:
                total = len(self.sheets.get_all_records(Config.SHEET_MANAGER))
                context += f"\nإجمالي المعاملات في النظام: {total}"
            except:
                pass

            prompt = f"""أنت مساعد ذكي لنظام إدارة المعاملات.
المستخدم: {user_name} (ID: {user_id})
المعلومات: {context}
رسالة المستخدم: {user_message}
أجب بلغة عربية فصيحة ومهذبة."""
            completion = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1000
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ خطأ في Groq: {e}")
            return "عذراً، حدث خطأ. حاول مرة أخرى."

# ---------- إنشاء كائنات عامة ----------
try:
    sheets_client = GoogleSheetsClient()
except Exception as e:
    logger.error(f"❌ فشل تهيئة Sheets: {e}")
    sheets_client = None

ai_assistant = AIAssistant()
ai_assistant.sheets = sheets_client  # ربط الـ sheets

app = Flask(__name__)

# ---------- دوال البوت ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)
    msg = "👋 *مرحباً بك في بوت متابعة المعاملات*\n\n"
    if context.args:
        transaction_id = context.args[0]
        msg += f"تم استلام معاملتك رقم: *{transaction_id}*\n"
        msg += f"يمكنك متابعتها عبر الرابط: [عرض التفاصيل]({Config.WEB_APP_URL}/view/{transaction_id})\n\n"
    else:
        msg += "📌 *الأوامر العامة:*\n"
        msg += "🔹 /id [رقم] - تفاصيل معاملة\n"
        msg += "🔹 /history [رقم] - سجل تتبع معاملة\n"
        msg += "🔹 /search [كلمة] - بحث في المعاملات\n"
        msg += "🔹 /wake - للتأكد من أن البوت يعمل\n"
        msg += "🔹 /subscribe [رقم] - متابعة معاملة\n"
        msg += "🔹 /unsubscribe [رقم] - إلغاء متابعة\n"
        msg += "🔹 /status [رقم] - تحليل ذكي للحالة\n"
        if is_admin:
            msg += "\n👑 *أوامر المدير:*\n"
            msg += "🔹 /stats - إحصائيات عامة\n"
    await update.message.reply_text(msg, parse_mode='Markdown', disable_web_page_preview=True)

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if not context.args:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /id 123")
        return
    transaction_id = context.args[0]
    row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
    if not row_info:
        await update.message.reply_text(f"❌ لا توجد معاملة بالرقم {transaction_id}")
        return
    data = row_info['data']
    msg = f"🔍 *تفاصيل المعاملة {transaction_id}:*\n"
    for key in ['اسم صاحب المعاملة الثلاثي', 'الحالة', 'الموظف المسؤول']:
        if key in data and data[key]:
            msg += f"• {key}: {data[key]}\n"
    msg += f"\n🔗 [رابط المتابعة]({Config.WEB_APP_URL}/view/{transaction_id})"
    await update.message.reply_text(msg, parse_mode='Markdown', disable_web_page_preview=True)

async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if not context.args:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /history 123")
        return
    transaction_id = context.args[0]
    ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
    if not ws:
        await update.message.reply_text("❌ لا يوجد سجل تاريخ.")
        return
    records = ws.get_all_records()
    history = [r for r in records if str(r.get('ID')) == transaction_id]
    if not history:
        await update.message.reply_text(f"لا يوجد سجل للمعاملة {transaction_id}")
        return
    history.sort(key=lambda x: x.get('timestamp', ''))
    msg = f"📜 *سجل تتبع المعاملة {transaction_id}:*\n"
    for entry in history:
        msg += f"• {entry.get('timestamp')} - {entry.get('action')} (بواسطة: {entry.get('user')})\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if not context.args:
        await update.message.reply_text("الرجاء إدخال كلمة للبحث: /search كلمة")
        return
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

async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ البوت نشط وجاهز!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != Config.ADMIN_CHAT_ID:
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

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /subscribe MUT-...")
        return
    transaction_id = context.args[0]
    row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
    if not row_info:
        await update.message.reply_text("❌ المعاملة غير موجودة")
        return
    subs_ws = sheets_client.get_worksheet(Config.SHEET_SUBSCRIBERS)
    if not subs_ws:
        await update.message.reply_text("⚠️ خطأ في قاعدة البيانات")
        return
    records = subs_ws.get_all_records()
    existing = [r for r in records if str(r.get('user_id')) == str(update.effective_user.id) and str(r.get('transaction_id')) == transaction_id]
    if existing:
        await update.message.reply_text("✅ أنت بالفعل متابع لهذه المعاملة")
        return
    subs_ws.append_row([update.effective_user.id, transaction_id])
    await update.message.reply_text(f"✅ تم تفعيل متابعة المعاملة {transaction_id}\nستصلك إشعارات فورية عند أي تحديث.")

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /unsubscribe MUT-...")
        return
    transaction_id = context.args[0]
    subs_ws = sheets_client.get_worksheet(Config.SHEET_SUBSCRIBERS)
    if not subs_ws:
        await update.message.reply_text("⚠️ خطأ في قاعدة البيانات")
        return
    records = subs_ws.get_all_records()
    for idx, row in enumerate(records):
        if str(row.get('user_id')) == str(update.effective_user.id) and str(row.get('transaction_id')) == transaction_id:
            new_records = [r for i, r in enumerate(records) if i != idx]
            data = [list(r.values()) for r in new_records] if new_records else []
            subs_ws.clear()
            if data:
                subs_ws.append_rows(data, value_input_option='USER_ENTERED')
            await update.message.reply_text(f"✅ تم إلغاء متابعة المعاملة {transaction_id}")
            return
    await update.message.reply_text("لم تكن متابعًا لهذه المعاملة.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /status MUT-...")
        return
    transaction_id = context.args[0]
    row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
    if not row_info:
        await update.message.reply_text("❌ المعاملة غير موجودة")
        return
    data = row_info['data']
    context_str = "\n".join([f"{k}: {v}" for k, v in data.items() if v])
    prompt = f"حلل حالة هذه المعاملة وأعطِ تقييماً ذكياً حول التأخير والخطوات القادمة:\n{context_str}"
    response = await ai_assistant.get_response(prompt, update.effective_user.id, update.effective_user.first_name, transaction_id)
    await update.message.reply_text(response, parse_mode='Markdown')

async def smart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    logger.info(f"🧠 معالجة رسالة عادية: {text}")
    if text.isdigit() or (text.startswith('MUT-') and len(text) > 10):
        context.args = [text]
        await get_id(update, context)
        return
    # البحث عن معاملة المستخدم في المشتركين
    transaction_id = None
    if sheets_client:
        subs_ws = sheets_client.get_worksheet(Config.SHEET_SUBSCRIBERS)
        if subs_ws:
            records = subs_ws.get_all_records()
            for r in records:
                if str(r.get('user_id')) == str(update.effective_user.id):
                    transaction_id = r.get('transaction_id')
                    break
    await ai_chat_handler(update, context, transaction_id)

async def ai_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, transaction_id=None):
    user_message = update.message.text
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or ""
    logger.info(f"🤖 استعلام ذكي من {user_name}: {user_message[:50]}...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    response = await ai_assistant.get_response(user_message, user_id, user_name, transaction_id)
    await update.message.reply_text(response)

# ---------- مراقبة المعاملات (يدوية) ----------
last_row_count = 0
last_state = {}
executor = ThreadPoolExecutor(max_workers=10)
stop_monitoring = threading.Event()

def safe_get_records(ws):
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(ws.get_all_records)
            return future.result(timeout=10)
    except Exception as e:
        logger.error(f"خطأ في جلب السجلات: {e}")
        return []

def process_transaction(transaction_data):
    try:
        ws, row_number, new_row, transaction_id = transaction_data
        if not transaction_id:
            now = datetime.now()
            date_str = now.strftime("%Y%m%d%H%M%S")
            random_part = random.randint(1000, 9999)
            transaction_id = f"MUT-{date_str}-{random_part}"
            ws.update_cell(row_number, 8, transaction_id)
            logger.info(f"🆔 تم توليد ID {transaction_id} للصف {row_number}")

        view_link = f"{Config.WEB_APP_URL}/view/{transaction_id}"
        hyperlink_formula = f'=HYPERLINK("{view_link}", "عرض المعاملة")'
        ws.update_cell(row_number, 21, hyperlink_formula)

        qr_ws = sheets_client.get_worksheet(Config.SHEET_QR)
        if qr_ws:
            name = new_row.get('اسم صاحب المعاملة الثلاثي', '')
            email = new_row.get('البريد الإلكتروني', '')
            qr_page_link = f"{Config.WEB_APP_URL}/qr/{transaction_id}"
            qr_image_url = f"{Config.WEB_APP_URL}/qr_image/{transaction_id}"
            qr_ws.append_row([name, email, transaction_id, view_link, qr_image_url, qr_page_link])
            new_row_num = len(qr_ws.get_all_values())
            qr_ws.update_cell(new_row_num, 4, f'=HYPERLINK("{view_link}", "عرض المعاملة")')
            qr_ws.update_cell(new_row_num, 5, f'=IMAGE("{qr_image_url}")')
            qr_ws.update_cell(new_row_num, 6, f'=HYPERLINK("{qr_page_link}", "عرض QR كبير")')
            logger.info(f"📸 تم إدراج بيانات QR للمعاملة {transaction_id}")

        history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        if history_ws:
            history_ws.append_row([
                datetime.now().isoformat(),
                transaction_id,
                "تم إنشاء المعاملة",
                "النظام"
            ])

        global last_state
        last_state[transaction_id] = (
            new_row.get('الحالة', ''),
            new_row.get('المؤسسة التالية', ''),
            new_row.get('التأخير', '')
        )
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة المعاملة: {e}", exc_info=True)

def check_new_transactions():
    global last_row_count
    try:
        if not sheets_client:
            return
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return
        records = safe_get_records(ws)
        current_count = len(records)
        if current_count > last_row_count:
            logger.info(f"📦 تم اكتشاف {current_count - last_row_count} معاملات جديدة")
            tasks = []
            for i in range(last_row_count, current_count):
                row_number = i + 2
                new_row = records[i]
                transaction_id = new_row.get('ID')
                tasks.append((ws, row_number, new_row, transaction_id))
            for task in tasks:
                executor.submit(process_transaction, task)
            last_row_count = current_count
            logger.info(f"✅ تم تفويض {len(tasks)} معاملات للمعالجة المتوازية (10 خيوط)")
    except Exception as e:
        logger.error(f"❌ خطأ في دالة المراقبة: {e}", exc_info=True)

def check_transaction_updates():
    try:
        if not sheets_client or not bot_app or not background_loop:
            return
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return
        records = safe_get_records(ws)
        for row in records:
            tx_id = row.get('ID')
            if not tx_id:
                continue
            current_state = (
                row.get('الحالة', ''),
                row.get('المؤسسة التالية', ''),
                row.get('التأخير', '')
            )
            if tx_id in last_state:
                old_state = last_state[tx_id]
                if old_state != current_state:
                    subs_ws = sheets_client.get_worksheet(Config.SHEET_SUBSCRIBERS)
                    if subs_ws:
                        subs = safe_get_records(subs_ws)
                        for sub in subs:
                            if sub.get('transaction_id') == tx_id:
                                user_id = sub.get('user_id')
                                changes = []
                                if old_state[0] != current_state[0]:
                                    changes.append(f"الحالة: {old_state[0]} → {current_state[0]}")
                                if old_state[1] != current_state[1]:
                                    changes.append(f"المؤسسة التالية: {old_state[1]} → {current_state[1]}")
                                if old_state[2] != current_state[2]:
                                    changes.append(f"التأخير: {old_state[2]} → {current_state[2]}")
                                msg = f"🔄 *تحديث معاملة {tx_id}*\n" + "\n".join(changes)
                                try:
                                    asyncio.run_coroutine_threadsafe(
                                        bot_app.bot.send_message(chat_id=user_id, text=msg, parse_mode='Markdown'),
                                        background_loop
                                    )
                                except Exception as e:
                                    logger.error(f"فشل إرسال إشعار للمستخدم {user_id}: {e}")
                    last_state[tx_id] = current_state
            else:
                last_state[tx_id] = current_state
    except Exception as e:
        logger.error(f"خطأ في check_transaction_updates: {e}")

def smart_alerts():
    try:
        if not sheets_client or not bot_app or not background_loop:
            return
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return
        records = safe_get_records(ws)
        for row in records:
            tx_id = row.get('ID')
            delay = row.get('التأخير', '')
            status = row.get('الحالة', '')
            if delay == 'نعم' and status != 'مكتملة':
                msg = f"⚠️ *تنبيه: معاملة {tx_id} متأخرة*\nيرجى مراجعة السبب."
                subs_ws = sheets_client.get_worksheet(Config.SHEET_SUBSCRIBERS)
                if subs_ws:
                    subs = safe_get_records(subs_ws)
                    for sub in subs:
                        if sub.get('transaction_id') == tx_id:
                            user_id = sub.get('user_id')
                            try:
                                asyncio.run_coroutine_threadsafe(
                                    bot_app.bot.send_message(chat_id=user_id, text=msg, parse_mode='Markdown'),
                                    background_loop
                                )
                            except Exception as e:
                                logger.error(f"فشل إرسال تنبيه للمستخدم {user_id}: {e}")
                if Config.ADMIN_CHAT_ID:
                    asyncio.run_coroutine_threadsafe(
                        bot_app.bot.send_message(chat_id=Config.ADMIN_CHAT_ID, text=msg, parse_mode='Markdown'),
                        background_loop
                    )
    except Exception as e:
        logger.error(f"خطأ في smart_alerts: {e}")

def monitoring_loop():
    logger.info("🔄 بدء حلقة المراقبة اليدوية (كل 10 ثوانٍ)")
    last_alert_time = time.time()
    while not stop_monitoring.is_set():
        try:
            check_new_transactions()
            check_transaction_updates()
            if time.time() - last_alert_time >= 3600:
                smart_alerts()
                last_alert_time = time.time()
        except Exception as e:
            logger.error(f"خطأ في حلقة المراقبة: {e}", exc_info=True)
        time.sleep(10)
    logger.info("🛑 توقفت حلقة المراقبة")

# ---------- إعداد البوت ----------
bot_app = None
background_loop = None

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

def setup_bot():
    global bot_app, background_loop, last_row_count
    logger.info("🚀 بدء setup_bot")
    if not Config.TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN غير موجود")
        return
    try:
        bot_app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start))
        bot_app.add_handler(CommandHandler("id", get_id))
        bot_app.add_handler(CommandHandler("history", get_history))
        bot_app.add_handler(CommandHandler("search", search))
        bot_app.add_handler(CommandHandler("wake", wake))
        bot_app.add_handler(CommandHandler("stats", stats))
        bot_app.add_handler(CommandHandler("subscribe", subscribe))
        bot_app.add_handler(CommandHandler("unsubscribe", unsubscribe))
        bot_app.add_handler(CommandHandler("status", status))
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_handler))
        logger.info("✅ تم بناء البوت وإضافة المعالجات")

        async def init_bot_async():
            logger.info("🔄 تهيئة البوت في الحلقة غير المتزامنة...")
            await bot_app.initialize()
            logger.info("✅ تم تهيئة البوت في الحلقة الخلفية")

        def start_background_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(init_bot_async())
            global background_loop
            background_loop = loop
            logger.info("🔄 بدء حلقة الأحداث الخلفية...")
            loop.run_forever()

        thread = threading.Thread(target=start_background_loop, daemon=True)
        thread.start()
        logger.info("⏳ انتظار تهيئة البوت في الخلفية...")
        time.sleep(3)
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
                ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
                if ws:
                    records = safe_get_records(ws)
                    last_row_count = len(records)
                    logger.info(f"📋 عدد المعاملات الحالي: {last_row_count}")
            except Exception as e:
                logger.error(f"❌ فشل قراءة العدد الأولي: {e}")

            monitoring_thread = threading.Thread(target=monitoring_loop, daemon=True)
            monitoring_thread.start()
            logger.info("🔍 بدأت مراقبة المعاملات الجديدة والتحديثات (كل 10 ثوانٍ)")
        else:
            logger.warning("⚠️ sheets_client غير متاح، لن يتم تشغيل المراقبة")
    except Exception as e:
        logger.error(f"❌ فشل إعداد البوت: {e}", exc_info=True)

# ---------- واجهة Flask ----------
# قوالب HTML (مختصرة للاختصار – يمكن إضافتها كاملة من الردود السابقة)
# سنضع نموذجاً بسيطاً هنا لكن في الملف الفعلي يجب وضع القوالب الكاملة.
INDEX_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head><meta charset="UTF-8"><title>المعاملات</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-100 p-4"><div class="max-w-6xl mx-auto"><h1 class="text-2xl font-bold mb-4">📋 جميع المعاملات (المدير)</h1>
<div class="bg-white rounded-xl shadow overflow-x-auto"><table class="min-w-full"><thead class="bg-gray-50"><tr><th class="px-4 py-2 text-right">ID</th><th class="px-4 py-2 text-right">الاسم</th><th class="px-4 py-2 text-right">الحالة</th><th class="px-4 py-2 text-right">الموظف</th><th></th></tr></thead><tbody id="transactions"></tbody></table></div></div>
<script>fetch('/api/transactions').then(r=>r.json()).then(data=>{const tbody=document.getElementById('transactions');data.forEach(t=>{const row=`<tr class="border-t"><td class="px-4 py-2">${t.id}</td><td class="px-4 py-2">${t.name}</td><td class="px-4 py-2">${t.status}</td><td class="px-4 py-2">${t.employee}</td><td class="px-4 py-2"><a href="/transaction/${t.id}" class="text-blue-500 underline">✏️ تعديل</a></td></tr>`;tbody.innerHTML+=row;});});</script></body></html>"""

EDIT_HTML = "<!DOCTYPE html><html><body>صفحة تعديل المعاملة (يمكنك إضافة القالب الكامل لاحقاً)</body></html>"
VIEW_HTML = "<!DOCTYPE html><html><body>صفحة عرض المعاملة</body></html>"
NEW_TRANSACTION_HTML = "<!DOCTYPE html><html><body>نموذج معاملة جديدة</body></html>"
SUCCESS_HTML = "<!DOCTYPE html><html><body>تم إنشاء المعاملة بنجاح</body></html>"

@app.route('/api/transactions', methods=['GET'])
def api_transactions():
    if not sheets_client:
        return jsonify([])
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
    result = [{'id': r.get('ID',''), 'name': r.get('اسم صاحب المعاملة الثلاثي',''), 'status': r.get('الحالة',''), 'employee': r.get('الموظف المسؤول','')} for r in records]
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
                col = headers.index(key)+1
                ws.update_cell(row, col, value)
        # تحديث أعمدة إضافية
        employee_name = updates.get('الموظف المسؤول', 'غير معروف')
        now = datetime.now().isoformat()
        # تحديث آخر تعديل بواسطة وتاريخ وعدد التعديلات
        # ... (يمكن إكمالها من الكود السابق)
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
        history = [{'time': r.get('timestamp',''), 'action': r.get('action',''), 'user': r.get('user','')} for r in records if str(r.get('ID')) == id]
        history.sort(key=lambda x: x['time'], reverse=True)
        return jsonify(history)
    except Exception as e:
        logger.error(f"خطأ في جلب التاريخ: {e}")
        return jsonify([])

@app.route('/qr/<id>')
def qr_page(id):
    view_link = f"{Config.WEB_APP_URL}/view/{id}"
    qr_base64 = QRGenerator.generate_qr(view_link)
    return f'<img src="data:image/png;base64,{qr_base64}">'

@app.route('/qr_image/<id>')
def qr_image(id):
    view_link = f"{Config.WEB_APP_URL}/view/{id}"
    qr_base64 = QRGenerator.generate_qr(view_link)
    img_data = base64.b64decode(qr_base64)
    return Response(img_data, mimetype='image/png')

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/transaction/<id>')
def edit_transaction_page(id):
    return render_template_string(EDIT_HTML)

@app.route('/view/<id>')
def view_transaction_page(id):
    return render_template_string(VIEW_HTML)

@app.route('/new-transaction', methods=['GET','POST'])
def new_transaction():
    # يمكنك إضافة منطق إنشاء المعاملة هنا
    return render_template_string(NEW_TRANSACTION_HTML)

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

# ---------- تشغيل النظام ----------
if __name__ == "__main__":
    # تشغيل البوت في خلفية
    bot_thread = threading.Thread(target=setup_bot, daemon=True)
    bot_thread.start()
    # تشغيل خادم Flask
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)