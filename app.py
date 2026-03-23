#!/usr/bin/env python
import os
import sys
import json
import asyncio
import threading
import time
import random
import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from flask import Flask, request, jsonify, render_template_string, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests
import qrcode
from io import BytesIO

from dotenv import load_dotenv
load_dotenv()

# ---------- إعداد التسجيل ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ---------- إعدادات النظام ----------
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

# ---------- Google Sheets Client ----------
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
            logger.info("✅ تم الاتصال بـ Google Sheets")
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
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        return img_base64

# ---------- AI Assistant (Groq) ----------
from openai import OpenAI

class AIAssistant:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv('GROQ_API_KEY'),
            base_url="https://api.groq.com/openai/v1"
        )
        self.sheets = None
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

            prompt = f"""أنت مساعد ذكي لنظام إدارة المعاملات. أنت ملم بكل تفاصيل المعاملات.
المستخدم: {user_name} (ID: {user_id})
المعلومات المتاحة:
{context}

رسالة المستخدم: {user_message}

أجب بلغة عربية فصيحة ومهذبة، وقدم تحليلاً ذكياً إذا طُلب منك تقييم حالة معاملة. استخدم البيانات المتاحة لتكون دقيقاً.
"""
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
if sheets_client:
    ai_assistant.sheets = sheets_client

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
    try:
        if not sheets_client:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات حالياً.")
            return
        if not context.args:
            await update.message.reply_text("الرجاء إدخال رقم المعاملة: /id 123")
            return
        transaction_id = context.args[0]
        logger.info(f"🔍 البحث عن ID: {transaction_id}")
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
    except Exception as e:
        logger.error(f"❌ خطأ في get_id: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ.")

async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
            time_str = entry.get('timestamp', '')
            action = entry.get('action', '')
            user = entry.get('user', '')
            msg += f"• {time_str} - {action} (بواسطة: {user})\n"
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"خطأ في history: {e}")
        await update.message.reply_text("حدث خطأ.")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"خطأ في search: {e}")
        await update.message.reply_text("حدث خطأ.")

async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ البوت نشط وجاهز!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"خطأ في stats: {e}")
        await update.message.reply_text("حدث خطأ.")

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"خطأ في subscribe: {e}")
        await update.message.reply_text("حدث خطأ، حاول مرة أخرى.")

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"خطأ في unsubscribe: {e}")
        await update.message.reply_text("حدث خطأ.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"خطأ في status: {e}")
        await update.message.reply_text("حدث خطأ.")

async def smart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    logger.info(f"🧠 معالجة رسالة عادية: {text}")

    if text.isdigit() or (text.startswith('MUT-') and len(text) > 10):
        context.args = [text]
        await get_id(update, context)
        return

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

# ---------- مراقبة المعاملات ----------
last_row_count = 0
last_state = {}
executor = ThreadPoolExecutor(max_workers=10)
stop_monitoring = threading.Event()
monitoring_thread = None

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
                else:
                    logger.warning("⚠️ ورقة manager غير موجودة")
            except Exception as e:
                logger.error(f"❌ فشل قراءة العدد الأولي: {e}")
                last_row_count = 0

            monitoring_thread = threading.Thread(target=monitoring_loop, daemon=True)
            monitoring_thread.start()
            logger.info("🔍 بدأت مراقبة المعاملات الجديدة والتحديثات (كل 10 ثوانٍ)")
        else:
            logger.warning("⚠️ sheets_client غير متاح، لن يتم تشغيل المراقبة")
    except Exception as e:
        logger.error(f"❌ فشل إعداد البوت: {e}", exc_info=True)

# ---------- واجهة Flask (قوالب HTML كاملة) ----------
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
        <h1 class="text-2xl font-bold mb-4">📋 جميع المعاملات (المدير)</h1>
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
    <title>تعديل المعاملة</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
        * { font-family: 'Inter', sans-serif; }
        .ios-card { background: rgba(255,255,255,0.8); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.3); border-radius: 16px; }
        .ios-input { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 16px; font-size: 16px; width: 100%; }
        .ios-input:focus { border-color: #007aff; outline: none; box-shadow: 0 0 0 3px rgba(0,122,255,0.1); }
        .ios-select { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 16px; font-size: 16px; width: 100%; }
        .label-ios { font-size: 14px; font-weight: 600; color: #6b7280; margin-bottom: 4px; display: block; }
        .timeline-item { border-right: 2px solid #007aff; position: relative; padding-right: 20px; margin-bottom: 20px; }
        .timeline-dot { width: 12px; height: 12px; background: #007aff; border-radius: 50%; position: absolute; right: -7px; top: 5px; }
    </style>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-3xl mx-auto">
        <div class="ios-card rounded-2xl p-4 mb-4 shadow-sm flex justify-between items-center">
            <h1 class="text-xl font-semibold">🔍 تتبع المعاملة <span id="transaction-id" class="text-blue-600"></span></h1>
            <a href="/" class="text-blue-500 text-sm">← العودة</a>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">📋 معلومات أساسية</h2>
            <div id="readonly-fields" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">✏️ تحديث البيانات</h2>
            <form id="editForm" class="space-y-4">
                <div id="editable-fields" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
                <button type="submit" class="w-full bg-blue-500 hover:bg-blue-600 text-white font-medium py-3 px-4 rounded-xl transition shadow-sm">💾 حفظ التغييرات</button>
            </form>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">📜 سجل الحركات</h2>
            <div id="history-timeline" class="space-y-2"></div>
        </div>

        <div id="message" class="fixed bottom-4 left-1/2 transform -translate-x-1/2 bg-gray-800 text-white px-6 py-3 rounded-xl shadow-lg opacity-0 transition-opacity"></div>
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
            .then(res => res.ok ? res.json() : Promise.reject())
            .then(data => {
                const readonlyKeys = [
                    'Timestamp', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف', 'البريد الإلكتروني',
                    'القسم', 'نوع المعاملة', 'المرافقات'
                ];
                const rc = document.getElementById('readonly-fields');
                rc.innerHTML = '';
                readonlyKeys.forEach(key => {
                    if (data[key] !== undefined) {
                        const value = data[key] || '-';
                        let display = value;
                        if (key === 'المرافقات' && value.startsWith('http')) {
                            display = `<a href="${value}" target="_blank" class="text-blue-500 underline">📎 فتح المرفق</a>`;
                        }
                        rc.innerHTML += `
                            <div class="bg-gray-50 p-3 rounded-xl">
                                <span class="label-ios">${key}</span>
                                <div class="text-gray-900 mt-1">${display}</div>
                            </div>
                        `;
                    }
                });

                const excluded = ['ID', 'LOG_JSON', 'آخر تعديل بتاريخ', 'آخر تعديل بواسطة', 'الرابط'];
                const editableKeys = Object.keys(data).filter(k => !readonlyKeys.includes(k) && !excluded.includes(k));
                const ec = document.getElementById('editable-fields');
                ec.innerHTML = '';

                editableKeys.forEach(key => {
                    let inputType = 'text';
                    let options = '';

                    if (key.includes('تاريخ')) {
                        inputType = 'date';
                    } else if (key === 'الحالة') {
                        inputType = 'select';
                        options = `
                            <select name="${key}" class="ios-select">
                                <option value="جديد" ${data[key] === 'جديد' ? 'selected' : ''}>جديد</option>
                                <option value="قيد المعالجة" ${data[key] === 'قيد المعالجة' ? 'selected' : ''}>قيد المعالجة</option>
                                <option value="مكتملة" ${data[key] === 'مكتملة' ? 'selected' : ''}>مكتملة</option>
                                <option value="متأخرة" ${data[key] === 'متأخرة' ? 'selected' : ''}>متأخرة</option>
                            </select>
                        `;
                    } else if (key === 'التأخير') {
                        inputType = 'select';
                        options = `
                            <select name="${key}" class="ios-select">
                                <option value="لا" ${data[key] !== 'نعم' ? 'selected' : ''}>لا</option>
                                <option value="نعم" ${data[key] === 'نعم' ? 'selected' : ''}>نعم</option>
                            </select>
                        `;
                    } else if (key === 'الأولوية') {
                        inputType = 'select';
                        options = `
                            <select name="${key}" class="ios-select">
                                <option value="عادية" ${data[key] !== 'مستعجلة' ? 'selected' : ''}>عادية</option>
                                <option value="مستعجلة" ${data[key] === 'مستعجلة' ? 'selected' : ''}>مستعجلة</option>
                            </select>
                        `;
                    }

                    if (inputType === 'select') {
                        ec.innerHTML += `
                            <div>
                                <label class="label-ios">${key}</label>
                                ${options}
                            </div>
                        `;
                    } else if (inputType === 'date') {
                        ec.innerHTML += `
                            <div>
                                <label class="label-ios">${key}</label>
                                <input type="date" name="${key}" value="${data[key] ? data[key].split('T')[0] : ''}" class="ios-input">
                            </div>
                        `;
                    } else {
                        ec.innerHTML += `
                            <div>
                                <label class="label-ios">${key}</label>
                                <input type="text" name="${key}" value="${data[key] || ''}" class="ios-input">
                            </div>
                        `;
                    }
                });
            })
            .catch(() => {
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
                showMessage('✅ تم الحفظ');
                loadHistory();
            } else {
                showMessage('❌ فشل', true);
            }
        });

        function loadHistory() {
            fetch(`/api/history/${id}`).then(r => r.json()).then(h => {
                const t = document.getElementById('history-timeline');
                if (h.length === 0) {
                    t.innerHTML = '<p class="text-gray-500">لا يوجد سجل</p>';
                    return;
                }
                let html = '';
                h.forEach(i => {
                    html += `
                        <div class="timeline-item">
                            <span class="timeline-dot"></span>
                            <span class="text-sm text-gray-500">${i.time}</span>
                            <p class="text-gray-800">${i.action}</p>
                            <p class="text-xs text-gray-400">${i.user}</p>
                        </div>
                    `;
                });
                t.innerHTML = html;
            });
        }
        loadHistory();
    </script>
</body>
</html>
"""

VIEW_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>تفاصيل المعاملة</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
        * { font-family: 'Inter', sans-serif; }
        .ios-card { background: rgba(255,255,255,0.8); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.3); border-radius: 16px; }
        .label-ios { font-size: 14px; font-weight: 600; color: #6b7280; margin-bottom: 4px; display: block; }
        .timeline-item { border-right: 2px solid #007aff; position: relative; padding-right: 20px; margin-bottom: 20px; }
        .timeline-dot { width: 12px; height: 12px; background: #007aff; border-radius: 50%; position: absolute; right: -7px; top: 5px; }
    </style>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-3xl mx-auto">
        <div class="ios-card rounded-2xl p-4 mb-4 shadow-sm flex justify-between items-center">
            <h1 class="text-xl font-semibold">🔍 تفاصيل المعاملة <span id="transaction-id" class="text-blue-600"></span></h1>
            <span class="text-gray-500 text-sm">(للمتابعة فقط)</span>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">📋 معلومات المعاملة</h2>
            <div id="fields" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">📜 سجل الحركات</h2>
            <div id="history-timeline" class="space-y-2"></div>
        </div>
    </div>

    <script>
        const id = window.location.pathname.split('/').pop();
        document.getElementById('transaction-id').innerText = id;

        fetch(`/api/transaction/${id}`)
            .then(res => res.ok ? res.json() : Promise.reject())
            .then(data => {
                const fieldsDiv = document.getElementById('fields');
                fieldsDiv.innerHTML = '';
                const excluded = ['ID', 'LOG_JSON', 'آخر تعديل بتاريخ', 'آخر تعديل بواسطة', 'الرابط'];
                for (let key in data) {
                    if (!excluded.includes(key)) {
                        const value = data[key] || '-';
                        let display = value;
                        if (key === 'المرافقات' && value.startsWith('http')) {
                            display = `<a href="${value}" target="_blank" class="text-blue-500 underline">📎 فتح المرفق</a>`;
                        }
                        fieldsDiv.innerHTML += `
                            <div class="bg-gray-50 p-3 rounded-xl">
                                <span class="label-ios">${key}</span>
                                <div class="text-gray-900 mt-1">${display}</div>
                            </div>
                        `;
                    }
                }
            })
            .catch(() => {
                document.body.innerHTML = '<div class="text-center text-red-500 p-10">❌ المعاملة غير موجودة</div>';
            });

        function loadHistory() {
            fetch(`/api/history/${id}`).then(r => r.json()).then(h => {
                const t = document.getElementById('history-timeline');
                if (h.length === 0) {
                    t.innerHTML = '<p class="text-gray-500">لا يوجد سجل</p>';
                    return;
                }
                let html = '';
                h.forEach(i => {
                    html += `
                        <div class="timeline-item">
                            <span class="timeline-dot"></span>
                            <span class="text-sm text-gray-500">${i.time}</span>
                            <p class="text-gray-800">${i.action}</p>
                            <p class="text-xs text-gray-400">${i.user}</p>
                        </div>
                    `;
                });
                t.innerHTML = html;
            });
        }
        loadHistory();
    </script>
</body>
</html>
"""

NEW_TRANSACTION_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>طلب معاملة جديدة</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-2xl mx-auto bg-white rounded-xl shadow p-6">
        <h1 class="text-2xl font-bold mb-6 text-center">📋 تقديم معاملة جديدة</h1>
        <form method="POST">
            <div class="mb-4">
                <label class="block text-gray-700 font-bold mb-2">الاسم الثلاثي</label>
                <input type="text" name="name" required class="w-full p-2 border rounded-lg">
            </div>
            <div class="mb-4">
                <label class="block text-gray-700 font-bold mb-2">رقم الهاتف</label>
                <input type="tel" name="phone" required class="w-full p-2 border rounded-lg">
            </div>
            <div class="mb-4">
                <label class="block text-gray-700 font-bold mb-2">القسم</label>
                <input type="text" name="department" class="w-full p-2 border rounded-lg">
            </div>
            <div class="mb-4">
                <label class="block text-gray-700 font-bold mb-2">نوع المعاملة</label>
                <input type="text" name="type" class="w-full p-2 border rounded-lg">
            </div>
            <div class="mb-4">
                <label class="block text-gray-700 font-bold mb-2">المرافقات (رابط)</label>
                <input type="url" name="attachments" class="w-full p-2 border rounded-lg">
            </div>
            <button type="submit" class="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700">إرسال الطلب</button>
        </form>
    </div>
</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>تم استلام طلبك</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-2xl mx-auto bg-white rounded-xl shadow p-6 text-center">
        <h1 class="text-2xl font-bold text-green-600 mb-4">✅ تم استلام طلبك بنجاح</h1>
        <p class="text-gray-700 mb-2">رقم معاملتك هو:</p>
        <p class="text-3xl font-mono font-bold text-blue-600 mb-6">{transaction_id}</p>
        <div class="bg-yellow-100 border-l-4 border-yellow-500 text-yellow-700 p-4 mb-6 text-right">
            <p class="font-bold">⚠️ تنبيه هام:</p>
            <p>يجب عليك <strong>حفظ هذا الرقم</strong> جيداً، لأنه الرابط الوحيد للتواصل مع البوت ومتابعة معاملتك. بدون هذا الرقم لن نتمكن من مساعدتك.</p>
        </div>
        <p class="mb-6">اضغط الزر أدناه لفتح البوت والبدء:</p>
        <a href="https://t.me/{bot_username}?start={transaction_id}" 
           class="inline-block bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700 transition">
            🚀 فتح البوت
        </a>
        <p class="text-sm text-gray-500 mt-6">يمكنك أيضًا تتبع معاملتك عبر الرابط:<br>
        <a href="{web_app_url}/view/{transaction_id}" class="text-blue-500 underline">{web_app_url}/view/{transaction_id}</a>
        </p>
    </div>
</body>
</html>
"""

# ---------- نقاط نهاية API ----------
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

        employee_name = updates.get('الموظف المسؤول', 'غير معروف')
        if 'آخر تعديل بواسطة' in headers:
            col_v = headers.index('آخر تعديل بواسطة') + 1
            ws.update_cell(row, col_v, employee_name)
        else:
            ws.update_cell(row, 22, employee_name)

        now = datetime.now().isoformat()
        if 'آخر تعديل بتاريخ' in headers:
            col_w = headers.index('آخر تعديل بتاريخ') + 1
            ws.update_cell(row, col_w, now)
        else:
            ws.update_cell(row, 23, now)

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

        if Config.ADMIN_CHAT_ID and background_loop and bot_app:
            try:
                asyncio.run_coroutine_threadsafe(
                    bot_app.bot.send_message(
                        chat_id=Config.ADMIN_CHAT_ID,
                        text=f"✏️ *تحديث معاملة*\nالمعاملة: {id}\nبواسطة: {employee_name}",
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
        history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
                   for r in records if str(r.get('ID')) == id]
        history.sort(key=lambda x: x['time'], reverse=True)
        return jsonify(history)
    except Exception as e:
        logger.error(f"خطأ في جلب التاريخ: {e}")
        return jsonify([])

@app.route('/ping')
def ping():
    return "pong"

@app.route('/qr/<id>')
def qr_page(id):
    view_link = f"{Config.WEB_APP_URL}/view/{id}"
    qr_base64 = QRGenerator.generate_qr(view_link)
    html = f"""
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>QR Code للمعاملة {id}</title>
        <style>
            body {{ display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f5f5f5; }}
            img {{ max-width: 90%; max-height: 90%; border: 1px solid #ddd; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
        </style>
    </head>
    <body>
        <img src="data:image/png;base64,{qr_base64}" alt="QR Code للمعاملة {id}">
    </body>
    </html>
    """
    return html

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

@app.route('/new-transaction', methods=['GET', 'POST'])
def new_transaction():
    if request.method == 'GET':
        return render_template_string(NEW_TRANSACTION_HTML)
    else:
        try:
            data = {
                'اسم صاحب المعاملة الثلاثي': request.form.get('name'),
                'رقم الهاتف': request.form.get('phone'),
                'القسم': request.form.get('department'),
                'نوع المعاملة': request.form.get('type'),
                'المرافقات': request.form.get('attachments'),
            }
            now = datetime.now()
            date_str = now.strftime("%Y%m%d%H%M%S")
            random_part = random.randint(1000, 9999)
            transaction_id = f"MUT-{date_str}-{random_part}"

            ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
            if not ws:
                return "خطأ في الاتصال بـ Google Sheets", 500

            headers = ws.row_values(1)
            new_row = []
            for col in headers:
                if col == 'ID':
                    new_row.append(transaction_id)
                else:
                    new_row.append(data.get(col, ''))
            ws.append_row(new_row)
            logger.info(f"✅ تم إضافة معاملة جديدة: {transaction_id}")

            return render_template_string(
                SUCCESS_HTML,
                transaction_id=transaction_id,
                bot_username=Config.BOT_USERNAME,
                web_app_url=Config.WEB_APP_URL
            )
        except Exception as e:
            logger.error(f"خطأ في إضافة المعاملة: {e}")
            return "حدث خطأ أثناء حفظ البيانات", 500

@app.route('/form-submit', methods=['POST'])
def form_submit():
    try:
        data = request.json
        now = datetime.now()
        date_str = now.strftime("%Y%m%d%H%M%S")
        random_part = random.randint(1000, 9999)
        transaction_id = f"MUT-{date_str}-{random_part}"

        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return jsonify({'success': False, 'error': 'Sheets not accessible'}), 500

        headers = ws.row_values(1)
        new_row = []
        for col in headers:
            if col == 'ID':
                new_row.append(transaction_id)
            else:
                value = data.get(col, '')
                new_row.append(value)
        ws.append_row(new_row)
        logger.info(f"✅ تم إضافة معاملة جديدة من Google Form: {transaction_id}")

        success_url = f"{Config.WEB_APP_URL}/transaction-success/{transaction_id}"
        return jsonify({'success': True, 'redirect_url': success_url})
    except Exception as e:
        logger.error(f"خطأ في form-submit: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/transaction-success/<transaction_id>')
def transaction_success(transaction_id):
    return render_template_string(
        SUCCESS_HTML,
        transaction_id=transaction_id,
        bot_username=Config.BOT_USERNAME,
        web_app_url=Config.WEB_APP_URL
    )

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

# ---------- بدء البوت في الخلفية (يتم تشغيله عند استيراد الملف) ----------
_bot_started = False
if not _bot_started:
    _bot_started = True
    bot_thread = threading.Thread(target=setup_bot, daemon=True)
    bot_thread.start()
    logger.info("🔥 تم بدء خيط البوت في الخلفية")
