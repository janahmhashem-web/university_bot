#!/usr/bin/env python
import logging
import sys
import os
import json
import asyncio
import threading
import time
import random
import base64
import re
import uuid
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import atexit
import requests
from collections import deque
from flask import Flask, request, jsonify, render_template_string, Response, abort, redirect, url_for, session
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import gspread

from sheets import GoogleSheetsClient
from config import Config
from qr_generator import QRGenerator
from ai_handler import AIAssistant

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

MAX_WORKERS = 20
WRITE_RATE_LIMIT = 250
RATE_WINDOW = 60
write_timestamps = deque(maxlen=WRITE_RATE_LIMIT)

def rate_limit_write():
    while len(write_timestamps) >= WRITE_RATE_LIMIT:
        oldest = write_timestamps[0]
        if time.time() - oldest < RATE_WINDOW:
            time.sleep(0.05)
        else:
            write_timestamps.popleft()
    write_timestamps.append(time.time())

executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

required_env_vars = ['GOOGLE_CREDENTIALS_JSON', 'SPREADSHEET_ID', 'TELEGRAM_BOT_TOKEN', 'WEB_APP_URL']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    logger.error(f"❌ المتغيرات البيئية المفقودة: {', '.join(missing_vars)}")
else:
    logger.info("✅ جميع المتغيرات البيئية الأساسية موجودة")

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))

# ------------------ Google Sheets ------------------
sheets_client = None
try:
    sheets_client = GoogleSheetsClient()
    logger.info("✅ تم الاتصال بـ Google Sheets")
except Exception as e:
    logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
    sheets_client = None

# ------------------ الذكاء الاصطناعي المتطور ------------------
ai_assistant = None
try:
    ai_assistant = AIAssistant(sheets_client=sheets_client)
    logger.info("✅ تم تهيئة Groq AI مع التعلم الآلي والذاكرة")
except Exception as e:
    logger.error(f"❌ فشل تهيئة AI: {e}")
    ai_assistant = None

if sheets_client:
    ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
    if ws:
        records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
        logger.info(f"📊 عدد المعاملات الفريدة في ورقة manager: {len(records)}")
    else:
        logger.error("❌ الورقة manager غير موجودة")

# ------------------ دوال مساعدة عامة ------------------
async def notify_user(transaction_id, message):
    if not sheets_client or not bot_app or not background_loop:
        return
    try:
        ws = sheets_client.get_worksheet(Config.SHEET_USERS)
        if not ws:
            return
        records = ws.get_all_records()
        for row in records:
            if str(row.get('transaction_id')) == str(transaction_id):
                chat_id = row.get('chat_id')
                if chat_id:
                    keyboard = [[InlineKeyboardButton("📜 عرض سجل التغييرات", callback_data=f"history_{transaction_id}")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await bot_app.bot.send_message(
                        chat_id=int(chat_id),
                        text=message,
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
                break
    except Exception as e:
        logger.error(f"فشل إرسال إشعار للمستخدم: {e}")

def save_user_chat(transaction_id, chat_id):
    try:
        ws = sheets_client.get_worksheet(Config.SHEET_USERS)
        if not ws:
            ws = sheets_client.spreadsheet.worksheet(Config.SHEET_USERS)
            if not ws:
                ws = sheets_client.spreadsheet.add_worksheet(title=Config.SHEET_USERS, rows=1, cols=2)
                ws.append_row(['transaction_id', 'chat_id'])
        records = ws.get_all_records()
        for i, row in enumerate(records):
            if str(row.get('transaction_id')) == transaction_id:
                ws.update_cell(i+2, 2, str(chat_id))
                return
        ws.append_row([transaction_id, str(chat_id)])
    except Exception as e:
        logger.error(f"فشل حفظ ربط المستخدم: {e}")

def fix_transaction_link(transaction_id):
    try:
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return
        all_rows = ws.get_all_values()
        headers = ws.row_values(1)
        id_col = link_col = None
        for idx, h in enumerate(headers):
            if h == 'ID':
                id_col = idx + 1
            elif h == 'الرابط':
                link_col = idx + 1
        if not id_col or not link_col:
            return
        row_num = None
        for i, row in enumerate(all_rows):
            if i == 0:
                continue
            if len(row) >= id_col and str(row[id_col-1]) == transaction_id:
                row_num = i + 1
                break
        if row_num:
            cell_addr = gspread.utils.rowcol_to_a1(row_num, link_col)
            current = ws.acell(cell_addr).value
            if current and isinstance(current, str) and current.startswith("'="):
                clean = current[1:]
                ws.update_acell(cell_addr, clean, value_input_option='USER_ENTERED')
                logger.info(f"✅ تم إصلاح رابط المعاملة {transaction_id}")
    except Exception as e:
        logger.error(f"فشل إصلاح الرابط: {e}")

def get_all_transactions_list():
    if not sheets_client:
        return "⚠️ النظام غير متصل بقاعدة البيانات."
    records = sheets_client.get_latest_transactions_sorted_fast(Config.SHEET_MANAGER)
    if not records:
        return "لا توجد معاملات حتى الآن."
    result = "📋 *قائمة جميع المعاملات:*\n"
    for r in records:
        result += f"• `{r.get('ID', '')}` - {r.get('اسم صاحب المعاملة الثلاثي', '')} - {r.get('الحالة', '')}\n"
    return result

def get_transactions_by_status(status):
    if not sheets_client:
        return "⚠️ النظام غير متصل بقاعدة البيانات."
    records = sheets_client.filter_transactions(Config.SHEET_MANAGER, status=status)
    if not records:
        return f"لا توجد معاملات بالحالة '{status}'."
    result = f"📋 *المعاملات بحالة {status}:*\n"
    for r in records:
        result += f"• `{r.get('ID', '')}` - {r.get('اسم صاحب المعاملة الثلاثي', '')}\n"
    return result

def get_transactions_with_errors():
    if not sheets_client:
        return "⚠️ النظام غير متصل بقاعدة البيانات."
    records = sheets_client.get_latest_transactions_sorted_fast(Config.SHEET_MANAGER)
    errors = []
    for r in records:
        if r.get('الحالة') == 'خطأ':
            errors.append(r)
        else:
            for key, value in r.items():
                if isinstance(value, str) and 'خطأ' in value:
                    errors.append(r)
                    break
    if not errors:
        return "لا توجد معاملات بها أخطاء."
    result = "⚠️ *المعاملات التي بها أخطاء:*\n"
    for r in errors:
        result += f"• `{r.get('ID', '')}` - {r.get('اسم صاحب المعاملة الثلاثي', '')} - {r.get('الحالة', '')}\n"
    return result

# ------------------ دوال البوت الأساسية ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)
    args = context.args

    if args:
        transaction_id = args[0]
        if sheets_client:
            data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
            if data:
                save_user_chat(transaction_id, user_id)
                await update.message.reply_text(
                    f"✅ تم ربط حسابك بالمعاملة\n\n🆔 {transaction_id}\nاستعمل رقم معاملتك للحصول على المعلومات."
                )
            else:
                await update.message.reply_text("❌ المعاملة غير موجودة")
        else:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return

    # أزرار المستخدم العادي
    user_keyboard = [
        [InlineKeyboardButton("🔍 تفاصيل معاملتي", callback_data="my_id")],
        [InlineKeyboardButton("📜 سجل تتبع معاملتي", callback_data="my_history")],
        [InlineKeyboardButton("📱 تعليمات QR", callback_data="cmd_qr")],
        [InlineKeyboardButton("💬 الدعم الفني", callback_data="cmd_support")],
        [InlineKeyboardButton("🤖 أسأل المساعد", callback_data="cmd_ai_chat")],
    ]
    # أزرار المدير الإضافية
    admin_keyboard = [
        [InlineKeyboardButton("📊 إحصائيات متقدمة", callback_data="cmd_advanced_stats")],
        [InlineKeyboardButton("🏢 إحصائيات الأقسام", callback_data="cmd_dept_stats")],
        [InlineKeyboardButton("👥 إحصائيات الموظفين", callback_data="cmd_emp_stats")],
        [InlineKeyboardButton("📈 توزيع الحالات", callback_data="cmd_status_dist")],
        [InlineKeyboardButton("📋 آخر 10 معاملات", callback_data="cmd_recent")],
        [InlineKeyboardButton("🔍 بحث متقدم", callback_data="cmd_advanced_search")],
        [InlineKeyboardButton("⚙️ إدارة المعاملات", callback_data="cmd_admin_manage")],
    ]

    keyboard = user_keyboard
    if is_admin:
        keyboard.extend(admin_keyboard)

    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "👋 *أهلاً بك في نظام متابعة المعاملات*\n\n"
    if is_admin:
        msg += "👑 *أنت مدير* - لديك صلاحيات إضافية.\n"
    else:
        msg += "🔹 *أنت مستخدم عادي* - يمكنك متابعة معاملتك فقط.\n"
    msg += "👇 استخدم الأزرار المناسبة."
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def get_user_transaction_id(chat_id):
    """استرجاع رقم المعاملة المرتبطة بالمستخدم"""
    if not sheets_client:
        return None
    try:
        ws = sheets_client.get_worksheet(Config.SHEET_USERS)
        if not ws:
            return None
        records = ws.get_all_records()
        for row in records:
            if str(row.get('chat_id')) == str(chat_id):
                return row.get('transaction_id')
        return None
    except Exception as e:
        logger.error(f"خطأ في استرجاع معاملة المستخدم: {e}")
        return None

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)

    if data == "cmd_id":
        context.user_data['awaiting'] = 'id'
        await query.edit_message_text("📌 أرسل رقم المعاملة (ID):", parse_mode='Markdown')
    elif data == "cmd_history":
        context.user_data['awaiting'] = 'history'
        await query.edit_message_text("📌 أرسل رقم المعاملة (ID) لمعرفة سجل التتبع:", parse_mode='Markdown')
    elif data == "my_id":
        tid = await get_user_transaction_id(user_id)
        if tid:
            context.args = [tid]
            await get_id(update, context)
        else:
            await query.edit_message_text("⚠️ لم يتم ربط حسابك بأي معاملة بعد. استخدم رابط البوت لربط حسابك.")
    elif data == "my_history":
        tid = await get_user_transaction_id(user_id)
        if tid:
            context.args = [tid]
            await get_history(update, context)
        else:
            await query.edit_message_text("⚠️ لم يتم ربط حسابك بأي معاملة بعد.")
    elif data == "cmd_ai_chat":
        context.user_data['awaiting'] = 'ai_chat'
        await query.edit_message_text("🤖 *المساعد الذكي*\nأرسل سؤالك عن المعاملات (مثال: ما هي حالة معاملتي؟)، وسأجيب بذكاء.", parse_mode='Markdown')
    elif data == "cmd_search":
        context.user_data['awaiting'] = 'search'
        await query.edit_message_text("🔎 أدخل كلمة البحث (اسم، قسم، أو رقم معاملة):", parse_mode='Markdown')
    elif data == "cmd_analyze":
        context.user_data['awaiting'] = 'analyze'
        await query.edit_message_text("📊 أرسل رقم المعاملة (ID) لتحليلها:", parse_mode='Markdown')
    elif data == "cmd_qr":
        transaction_id = await get_user_transaction_id(user_id)
        instruction_text = "📱 *كيفية استخدام رمز QR:*\n1️⃣ اطبع رمز QR\n2️⃣ الصقه في مكان واضح\n3️⃣ سيتم التتبع بنجاح"
        if transaction_id:
            base_url = request.host_url.rstrip('/')
            direct_token = sheets_client.get_direct_token(transaction_id)
            if direct_token:
                edit_link = f"{base_url}/transaction/{transaction_id}?token={direct_token}"
            else:
                edit_link = f"{base_url}/verify-email?transaction_id={transaction_id}"
            qr_base64 = QRGenerator.generate_qr(edit_link)
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=base64.b64decode(qr_base64),
                caption=instruction_text + f"\n\n🔗 {edit_link}",
                parse_mode='Markdown'
            )
            await query.message.delete()
        else:
            await query.edit_message_text(instruction_text + "\n\n📌 لم يتم ربط حسابك بأي معاملة بعد.", parse_mode='Markdown')
    elif data == "cmd_support":
        await query.edit_message_text("📨 استخدم الأمر `/support` للتواصل مع الدعم.", parse_mode='Markdown')
    elif data == "cmd_stats":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        if not sheets_client:
            await query.edit_message_text("⚠️ غير متصل بقاعدة البيانات.")
            return
        records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
        total = len(records)
        completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
        pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
        msg = f"📊 *إحصائيات*\nإجمالي: {total}\nمكتملة: {completed}\nقيد المعالجة: {pending}"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data == "cmd_advanced_stats":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        if not sheets_client:
            await query.edit_message_text("⚠️ غير متصل بقاعدة البيانات.")
            return
        total = len(sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER))
        dept_count = len(sheets_client.get_distinct_departments())
        emp_count = len(sheets_client.get_distinct_employees())
        status_dist = sheets_client.get_status_distribution()
        msg = f"📊 *إحصائيات عامة*\n• إجمالي: {total}\n• الأقسام: {dept_count}\n• الموظفون: {emp_count}\n• التوزيع:\n"
        for status, count in status_dist.items():
            if count > 0:
                msg += f"   - {status}: {count}\n"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data == "cmd_dept_stats":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        if not sheets_client:
            await query.edit_message_text("⚠️ غير متصل بقاعدة البيانات.")
            return
        stats = sheets_client.get_department_stats()
        if not stats:
            await query.edit_message_text("لا توجد بيانات.")
            return
        msg = "🏢 *إحصائيات الأقسام*\n"
        for dept, count in stats.items():
            msg += f"• {dept}: {count}\n"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data == "cmd_emp_stats":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        if not sheets_client:
            await query.edit_message_text("⚠️ غير متصل بقاعدة البيانات.")
            return
        workload = sheets_client.get_employee_workload()
        if not workload:
            await query.edit_message_text("لا توجد بيانات.")
            return
        msg = "👥 *حمل العمل*\n"
        for emp, data in list(workload.items())[:20]:
            msg += f"• {emp}: {data['total']} معاملة ({data['delayed']} متأخرة)\n"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data == "cmd_status_dist":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        if not sheets_client:
            await query.edit_message_text("⚠️ غير متصل بقاعدة البيانات.")
            return
        dist = sheets_client.get_status_distribution()
        msg = "📈 *توزيع الحالات*\n"
        for status, count in dist.items():
            if count > 0:
                msg += f"• {status}: {count}\n"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data == "cmd_recent":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        if not sheets_client:
            await query.edit_message_text("⚠️ غير متصل بقاعدة البيانات.")
            return
        recent = sheets_client.get_recent_transactions(10)
        if not recent:
            await query.edit_message_text("لا توجد معاملات.")
            return
        msg = "📋 *آخر 10 معاملات*\n"
        for r in recent:
            msg += f"• `{r.get('ID', '')}` - {r.get('اسم صاحب المعاملة الثلاثي', '')} - {r.get('الحالة', '')}\n"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data == "cmd_advanced_search":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        context.user_data['awaiting'] = 'adv_search'
        await query.edit_message_text(
            "🔍 *البحث المتقدم*\nأدخل معايير البحث:\n`القسم:...` أو `الموظف:...` أو `الحالة:...`\nمثال: `القسم:تقنيات, الحالة:جديد`",
            parse_mode='Markdown'
        )
    elif data.startswith("history_"):
        transaction_id = data.split("_", 1)[1]
        if not sheets_client:
            await query.edit_message_text("⚠️ النظام غير متصل بقاعدة البيانات.")
            return
        ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        if not ws:
            await query.edit_message_text("❌ لا يوجد سجل تاريخ.")
            return
        records = ws.get_all_records()
        history = [r for r in records if str(r.get('ID')) == transaction_id]
        if history:
            history.sort(key=lambda x: x.get('timestamp', ''))
            msg = f"📜 *سجل تتبع المعاملة {transaction_id}:*\n"
            for entry in history:
                time_str = entry.get('timestamp', '')
                action = entry.get('action', '')
                user = entry.get('user', '')
                msg += f"• {time_str} - {action} (بواسطة: {user})\n"
            await query.edit_message_text(msg, parse_mode='Markdown')
        else:
            await query.edit_message_text(f"لا يوجد سجل للمعاملة {transaction_id}")
    else:
        await query.edit_message_text("⚠️ أمر غير معروف.")

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if context.args:
        transaction_id = context.args[0]
        data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
        if data:
            msg = f"🔍 *تفاصيل المعاملة {transaction_id}:*\n"
            for key in ['اسم صاحب المعاملة الثلاثي', 'الحالة', 'الموظف المسؤول']:
                if key in data and data[key]:
                    msg += f"• {key}: {data[key]}\n"
            base_url = request.host_url.rstrip('/')
            msg += f"\n🔗 [رابط المتابعة]({base_url}/view/{transaction_id})"
            await update.message.reply_text(msg, parse_mode='Markdown', disable_web_page_preview=True)
        else:
            await update.message.reply_text(f"❌ لا توجد معاملة بالرقم {transaction_id}")
    else:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /id <رقم>")

async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if context.args:
        transaction_id = context.args[0]
        ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        if not ws:
            await update.message.reply_text("❌ لا يوجد سجل تاريخ.")
            return
        records = ws.get_all_records()
        history = [r for r in records if str(r.get('ID')) == transaction_id]
        if history:
            history.sort(key=lambda x: x.get('timestamp', ''))
            msg = f"📜 *سجل تتبع المعاملة {transaction_id}:*\n"
            for entry in history:
                time_str = entry.get('timestamp', '')
                action = entry.get('action', '')
                user = entry.get('user', '')
                msg += f"• {time_str} - {action} (بواسطة: {user})\n"
            await update.message.reply_text(msg, parse_mode='Markdown')
        else:
            await update.message.reply_text(f"لا يوجد سجل للمعاملة {transaction_id}")
    else:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /history <رقم>")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if context.args:
        keyword = ' '.join(context.args)
        records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
        found = []
        for r in records:
            if keyword in str(r.values()):
                found.append(r.get('ID', ''))
        if found:
            await update.message.reply_text(f"🔎 نتائج البحث عن '{keyword}':\n" + "\n".join(found[:10]))
        else:
            await update.message.reply_text("لا توجد نتائج.")
    else:
        await update.message.reply_text("الرجاء إدخال كلمة البحث: /search <كلمة>")

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
        records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
        total = len(records)
        completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
        pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
        msg = f"📊 *إحصائيات*\nإجمالي المعاملات: {total}\nمكتملة: {completed}\nقيد المعالجة: {pending}"
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"خطأ في stats: {e}")
        await update.message.reply_text("حدث خطأ.")

async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    transaction_id = None
    if sheets_client:
        try:
            ws = sheets_client.get_worksheet(Config.SHEET_USERS)
            if ws:
                records = ws.get_all_records()
                for row in records:
                    if str(row.get('chat_id')) == str(user_id):
                        transaction_id = row.get('transaction_id')
                        break
        except Exception as e:
            logger.error(f"خطأ في جلب معاملة المستخدم: {e}")
    if transaction_id:
        base_url = request.host_url.rstrip('/')
        direct_token = sheets_client.get_direct_token(transaction_id)
        if direct_token:
            edit_link = f"{base_url}/transaction/{transaction_id}?token={direct_token}"
        else:
            edit_link = f"{base_url}/verify-email?transaction_id={transaction_id}"
        qr_base64 = QRGenerator.generate_qr(edit_link)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=base64.b64decode(qr_base64),
            caption=f"📱 *رمز QR للوصول إلى المعاملة*\n\n🆔 {transaction_id}\n\n1️⃣ امسح الرمز\n2️⃣ أدخل بريدك المسجل\n3️⃣ سيتم توجيهك إلى صفحة التعديل.\n\n🔗 {edit_link}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "📌 *لم يتم ربط حسابك بأي معاملة بعد.*\n\n"
            "لربط حسابك بمعاملة، استخدم الرابط التالي:\n"
            f"`https://t.me/{Config.BOT_USERNAME}?start=رقم_المعاملة`",
            parse_mode='Markdown'
        )

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or ""
    if Config.ADMIN_CHAT_ID:
        await context.bot.send_message(
            chat_id=Config.ADMIN_CHAT_ID,
            text=f"📩 *رسالة دعم جديدة*\nمن: {user_name} (ID: {user_id})\n\nلطلب مساعدة، يرجى الرد عليه مباشرة.",
            parse_mode='Markdown'
        )
    await update.message.reply_text(
        "📨 تم إرسال طلبك إلى فريق الدعم. سيتم الرد عليك في أقرب وقت.\n"
        "شكراً لتواصلك معنا."
    )

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if not context.args:
        await update.message.reply_text("الرجاء إدخال رقم المعاملة: /analyze <MUT-...>")
        return
    transaction_id = context.args[0]
    data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
    if not data:
        await update.message.reply_text(f"❌ لا توجد معاملة بالرقم {transaction_id}")
        return
    ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
    history = []
    if ws:
        records = ws.get_all_records()
        history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
                   for r in records if str(r.get('ID')) == transaction_id]
        history.sort(key=lambda x: x['time'])
    await update.message.reply_text("🔍 جاري التحليل...")
    if ai_assistant:
        analysis = await ai_assistant.analyze_transaction(data, history)
    else:
        analysis = "❌ خدمة التحليل غير متاحة."
    await update.message.reply_text(analysis, parse_mode='Markdown')

async def assign_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /assign <transaction_id> <employee_name> """
    if update.effective_user.id != Config.ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ غير مصرح.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("الاستخدام: /assign <ID> <اسم الموظف>")
        return
    tid = context.args[0]
    emp = ' '.join(context.args[1:])
    data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, tid)
    if not data:
        await update.message.reply_text(f"❌ المعاملة {tid} غير موجودة.")
        return
    success = sheets_client.update_transaction_field(tid, 'الموظف المسؤول', emp)
    if success:
        await update.message.reply_text(f"✅ تم تعيين {emp} كمسؤول عن المعاملة {tid}.")
        sheets_client.add_history_entry(tid, f"تعيين مسؤول: {emp}", update.effective_user.first_name)
    else:
        await update.message.reply_text(f"❌ فشل التحديث.")

async def set_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /set_status <transaction_id> <status> """
    if update.effective_user.id != Config.ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ غير مصرح.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("الاستخدام: /set_status <ID> <حالة>\nالحالات: جديد, قيد المعالجة, مكتملة, متأخرة")
        return
    tid = context.args[0]
    status = context.args[1]
    valid_status = ['جديد', 'قيد المعالجة', 'مكتملة', 'متأخرة']
    if status not in valid_status:
        await update.message.reply_text(f"حالة غير صالحة. الخيارات: {', '.join(valid_status)}")
        return
    data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, tid)
    if not data:
        await update.message.reply_text(f"❌ المعاملة {tid} غير موجودة.")
        return
    success = sheets_client.update_transaction_field(tid, 'الحالة', status)
    if success:
        await update.message.reply_text(f"✅ تم تغيير حالة المعاملة {tid} إلى {status}.")
        sheets_client.add_history_entry(tid, f"تغيير الحالة إلى {status}", update.effective_user.first_name)
        await notify_user(tid, f"📢 تم تغيير حالة معاملتك إلى {status}.")
    else:
        await update.message.reply_text(f"❌ فشل التحديث.")

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /feedback 1 (مفيد) أو /feedback 0 (غير مفيد) """
    if not context.args:
        await update.message.reply_text("الاستخدام: /feedback 1 (للمفيد) أو /feedback 0 (لغير المفيد)")
        return
    score = context.args[0]
    helpful = score == "1"
    if ai_assistant:
        ai_assistant.record_feedback(
            user_id=update.effective_user.id,
            user_message="(تم التقييم عبر الأمر)",
            ai_response="(تم التقييم)",
            helpful=helpful
        )
        await update.message.reply_text("✅ شكراً لتقييمك! هذا يساعدنا على تحسين الإجابات.")
    else:
        await update.message.reply_text("الخدمة غير متاحة حالياً.")

async def smart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)

    if 'awaiting' in context.user_data:
        awaiting = context.user_data.pop('awaiting')
        if awaiting == 'id':
            context.args = [text]
            await get_id(update, context)
        elif awaiting == 'history':
            context.args = [text]
            await get_history(update, context)
        elif awaiting == 'search':
            context.args = [text]
            await search(update, context)
        elif awaiting == 'analyze':
            context.args = [text]
            await analyze(update, context)
        elif awaiting == 'ai_chat':
            # استخدام المساعد الذكي المتطور
            if ai_assistant:
                response = await ai_assistant.get_response(
                    user_message=text,
                    user_id=user_id,
                    user_name=update.effective_user.first_name or "مستخدم",
                    is_admin=is_admin
                )
                await update.message.reply_text(response)
            else:
                await update.message.reply_text("عذراً، المساعد الذكي غير متاح حالياً.")
        elif awaiting == 'adv_search':
            criteria = {}
            parts = text.split(',')
            for part in parts:
                if ':' in part:
                    key, val = part.split(':', 1)
                    key = key.strip()
                    val = val.strip()
                    if key == 'القسم':
                        criteria['department'] = val
                    elif key == 'الموظف':
                        criteria['employee'] = val
                    elif key == 'الحالة':
                        criteria['status'] = val
            if not criteria:
                await update.message.reply_text("❌ معايير غير صحيحة.")
                return
            records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
            filtered = []
            for r in records:
                match = True
                if 'department' in criteria and criteria['department'].lower() not in r.get('القسم', '').lower():
                    match = False
                if 'employee' in criteria and criteria['employee'].lower() not in r.get('الموظف المسؤول', '').lower():
                    match = False
                if 'status' in criteria and criteria['status'].lower() != r.get('الحالة', '').lower():
                    match = False
                if match:
                    filtered.append(r)
            if not filtered:
                await update.message.reply_text("❌ لا توجد معاملات.")
                return
            msg = f"🔍 *نتائج ({len(filtered)} معاملة)*\n"
            for r in filtered[:20]:
                msg += f"• `{r.get('ID')}` - {r.get('اسم صاحب المعاملة الثلاثي')} - {r.get('الحالة')}\n"
            await update.message.reply_text(msg, parse_mode='Markdown')
        return
    else:
        # أي رسالة عادية: نمرر إلى المساعد الذكي
        if ai_assistant:
            response = await ai_assistant.get_response(
                user_message=text,
                user_id=user_id,
                user_name=update.effective_user.first_name or "مستخدم",
                is_admin=is_admin
            )
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("عذراً، المساعد الذكي غير متاح حالياً.")

# ------------------ إعداد البوت وحلقة الأحداث ------------------
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
        bot_app.add_handler(CommandHandler("qr", qr_command))
        bot_app.add_handler(CommandHandler("support", support_command))
        bot_app.add_handler(CommandHandler("analyze", analyze))
        bot_app.add_handler(CommandHandler("assign", assign_employee))
        bot_app.add_handler(CommandHandler("set_status", set_status))
        bot_app.add_handler(CommandHandler("feedback", feedback_command))
        bot_app.add_handler(CallbackQueryHandler(button_callback))
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_handler))
        logger.info("✅ تم بناء البوت")

        async def init_bot():
            await bot_app.initialize()
            logger.info("✅ تم تهيئة البوت في الخلفية")
        def start_background_loop():
            global background_loop
            background_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(background_loop)
            background_loop.run_until_complete(init_bot())
            background_loop.run_forever()
        loop_thread = threading.Thread(target=start_background_loop, daemon=True)
        loop_thread.start()
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
        requests.post(f"https://api.telegram.org/bot{token}/deleteWebhook")
        resp = requests.post(f"https://api.telegram.org/bot{token}/setWebhook", data={"url": webhook_url})
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"✅ Webhook set to {webhook_url}")
        else:
            logger.error(f"❌ فشل تعيين webhook: {resp.text}")
    except Exception as e:
        logger.error(f"خطأ في تعيين webhook: {e}")

if Config.WEB_APP_URL and bot_app:
    threading.Thread(target=lambda: (time.sleep(5), set_webhook_sync())).start()

# ------------------ نقاط نهاية API ------------------
@app.route('/api/submit', methods=['POST'])
def api_submit():
    global sheets_client
    if sheets_client is None:
        logger.error("sheets_client is None, attempting to reconnect...")
        try:
            sheets_client = GoogleSheetsClient()
            global ai_assistant
            try:
                ai_assistant = AIAssistant(sheets_client=sheets_client)
            except Exception as e:
                logger.error(f"Failed to reinit AI: {e}")
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            return jsonify({'success': False, 'error': 'النظام غير متصل بقاعدة البيانات'}), 500
    try:
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        function = request.form.get('function', '').strip()
        department = request.form.get('department', '').strip()
        transaction_type = request.form.get('transaction_type', '').strip()
        attachments_text = request.form.get('attachments_text', '').strip()
        uploaded_file = request.files.get('attachment_file')
        attachments = attachments_text
        if uploaded_file and uploaded_file.filename:
            file_data = uploaded_file.read()
            file_link = sheets_client.upload_file_to_drive(file_data, uploaded_file.filename)
            if file_link:
                attachments = attachments_text + "\n" + file_link if attachments_text else file_link
        if not name or not phone:
            return jsonify({'success': False, 'error': 'الاسم والهاتف مطلوبان'}), 400
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return jsonify({'success': False, 'error': 'ورقة manager غير موجودة'}), 500
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        transaction_id = f"MUT-{now.strftime('%Y%m%d%H%M%S')}-{random.randint(1000,9999)}"
        headers = ws.row_values(1)
        new_row = [''] * len(headers)
        base_url = request.host_url.rstrip('/')
        edit_link = f"{base_url}/transaction/{transaction_id}"
        hyperlink_formula = f'=HYPERLINK("{edit_link}", "تعديل المعاملة")'
        for idx, header in enumerate(headers):
            if header == 'Timestamp':
                new_row[idx] = timestamp
            elif header == 'اسم صاحب المعاملة الثلاثي':
                new_row[idx] = name
            elif header == 'رقم الهاتف':
                new_row[idx] = phone
            elif header == 'الوظيفة':
                new_row[idx] = function
            elif header == 'القسم':
                new_row[idx] = department
            elif header == 'نوع المعاملة':
                new_row[idx] = transaction_type
            elif header == 'المرافقات':
                new_row[idx] = attachments
            elif header == 'ID':
                new_row[idx] = transaction_id
            elif header == 'الرابط':
                new_row[idx] = hyperlink_formula
        sheets_client.safe_append_row(ws, new_row, batch=True)
        logger.info(f"✅ تم إنشاء المعاملة {transaction_id}")
        fix_transaction_link(transaction_id)
        global last_row_count
        last_row_count = len(ws.get_all_values())
        if department:
            executor.submit(sheets_client.append_to_department_sheet, department, new_row, headers)
        def update_qr():
            qr_ws = sheets_client.get_worksheet(Config.SHEET_QR)
            if qr_ws:
                qr_row = [transaction_id, f'=IMAGE("{base_url}/qr_image/{transaction_id}")', hyperlink_formula]
                sheets_client.safe_append_row(qr_ws, qr_row, batch=True)
        executor.submit(update_qr)
        executor.submit(sheets_client.add_history_entry, transaction_id, "تم إنشاء المعاملة", "API")
        if Config.ADMIN_CHAT_ID and background_loop and bot_app:
            asyncio.run_coroutine_threadsafe(
                bot_app.bot.send_message(
                    chat_id=Config.ADMIN_CHAT_ID,
                    text=f"🆕 *معاملة جديدة*\nالاسم: {name}\nالهاتف: {phone}\nID: {transaction_id}\nالوظيفة: {function}\nالقسم: {department}",
                    parse_mode='Markdown'
                ),
                background_loop
            )
        return jsonify({
            'success': True,
            'id': transaction_id,
            'edit_link': edit_link,
            'deep_link': f"https://t.me/{Config.BOT_USERNAME}?start={transaction_id}"
        })
    except Exception as e:
        logger.error(f"🔥 خطأ في /api/submit: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/headers')
def api_headers():
    if not sheets_client:
        return jsonify([])
    ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
    if not ws:
        return jsonify([])
    return jsonify(ws.row_values(1))

@app.route('/api/transactions', methods=['GET'])
def api_transactions():
    if not sheets_client:
        return jsonify([])
    status = request.args.get('status')
    employee = request.args.get('employee')
    department = request.args.get('department')
    if status or employee or department:
        records = sheets_client.filter_transactions(Config.SHEET_MANAGER, status, employee, department)
    else:
        records = sheets_client.get_latest_transactions_sorted_fast(Config.SHEET_MANAGER)
    result = [{
        'id': r.get('ID', ''),
        'name': r.get('اسم صاحب المعاملة الثلاثي', ''),
        'status': r.get('الحالة', ''),
        'employee': r.get('الموظف المسؤول', ''),
        'department': r.get('القسم', ''),
        'last_modified': r.get('آخر تعديل بتاريخ', '')
    } for r in records]
    return jsonify(result)

@app.route('/api/transaction/<id>', methods=['GET', 'POST'])
def api_transaction(id):
    if not sheets_client:
        return jsonify({'success': False, 'message': 'غير متصل'}), 500
    if request.method == 'GET':
        data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, id)
        if not data:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(data)
    else:
        updates = request.json
        old_data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, id)
        if not old_data:
            return jsonify({'success': False, 'message': 'المعاملة غير موجودة'}), 404
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        headers = ws.row_values(1)
        new_row = [''] * len(headers)
        employee_name = updates.get('الموظف المسؤول', old_data.get('الموظف المسؤول', 'غير معروف'))
        now = datetime.now()
        for idx, header in enumerate(headers):
            value = old_data.get(header, '')
            if header in updates:
                value = updates[header]
            if header == 'آخر تعديل بواسطة':
                value = employee_name
            elif header == 'آخر تعديل بتاريخ':
                value = now.strftime("%Y-%m-%d %H:%M:%S")
            elif header == 'عدد التعديلات':
                try:
                    value = int(old_data.get(header, 0)) + 1
                except:
                    value = 1
            new_row[idx] = value
        sheets_client.safe_append_row(ws, new_row, batch=True)
        changes = []
        for key, new_value in updates.items():
            old_value = old_data.get(key, '')
            if new_value != old_value:
                if key == 'الموظف المسؤول':
                    changes.append(f"👤 المسؤول الآن: {new_value}")
                elif key == 'المؤسسة التالية':
                    changes.append(f"🏢 المؤسسة الآن: {new_value}")
                elif key == 'الحالة':
                    changes.append(f"📌 الحالة الآن: {new_value}")
                elif key == 'التأخير':
                    changes.append(f"⚠️ التأخير الآن: {new_value}")
                elif key == 'الأولوية':
                    changes.append(f"⚡ الأولوية الآن: {new_value}")
                elif key == 'تاريخ التحويل':
                    changes.append(f"📅 تاريخ التحويل الآن: {new_value}")
                elif key == 'سبب التحويل':
                    changes.append(f"📝 سبب التحويل: {new_value}")
                elif key == 'الموافق':
                    changes.append(f"✅ تمت الموافقة من قبل: {new_value}")
                elif key == 'ملاحظات إضافية':
                    changes.append(f"💬 ملاحظات جديدة: {new_value}")
                elif key == 'آخر إجراء':
                    changes.append(f"🔄 آخر إجراء: {new_value}")
                else:
                    changes.append(f"📝 {key}: {new_value}")
        user_message = f"✏️ *معاملتك {id} تم تحديثها*\n\n"
        if changes:
            user_message += "\n".join(changes)
        else:
            user_message += "تم تحديث بيانات المعاملة.\n"
        user_message += f"\n🔍 لمتابعة كل التغييرات اضغط الزر أدناه."
        changes_str = ', '.join(updates.keys())
        sheets_client.add_history_entry(id, f"تحديث: {changes_str}", employee_name)
        if background_loop and bot_app:
            asyncio.run_coroutine_threadsafe(notify_user(id, user_message), background_loop)
        if updates.get('الحالة') == 'مكتملة':
            if hasattr(sheets_client, 'archive_transaction'):
                old_dept = old_data.get('القسم', '')
                archive_success = sheets_client.archive_transaction(id, department_name=old_dept)
                if archive_success:
                    return jsonify({'success': True, 'message': 'تم الحفظ والمعاملة مؤرشفة'})
                else:
                    return jsonify({'success': True, 'message': 'تم الحفظ ولكن فشلت الأرشفة'})
            else:
                logger.warning("archive_transaction غير متوفر في sheets_client")
        return jsonify({'success': True, 'message': 'تم إضافة سجل التحديث بنجاح'})

@app.route('/api/history/<id>')
def api_transaction_history(id):
    if not sheets_client:
        return jsonify([])
    ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
    if not ws:
        return jsonify([])
    records = ws.get_all_records()
    history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
               for r in records if str(r.get('ID')) == id]
    history.sort(key=lambda x: x['time'], reverse=True)
    return jsonify(history)

# ------------------ صفحة التحقق بالبريد ------------------
@app.route('/verify-email', methods=['GET', 'POST'])
def verify_email_page():
    transaction_id = request.args.get('transaction_id')
    if not transaction_id:
        return "❌ المعاملة غير معروفة", 400
    data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
    if not data:
        return "❌ المعاملة غير موجودة", 404
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if not email:
            return "الرجاء إدخال البريد الإلكتروني", 400
        if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
            return "صيغة البريد الإلكتروني غير صحيحة", 400
        if not sheets_client.is_email_allowed(email):
            return f"🚫 غير مصرح: البريد الإلكتروني {email} غير مسجل في النظام.", 403
        # توليد توكن لا ينتهي (صلاحية دائمة)
        token = sheets_client.generate_access_token(transaction_id, email, expiry_minutes=None)
        if not token:
            return "حدث خطأ أثناء توليد رابط الدخول", 500
        base_url = request.host_url.rstrip('/')
        edit_url = f"{base_url}/transaction/{transaction_id}?token={token}"
        logger.info(f"✅ إعادة التوجيه إلى: {edit_url} (صلاحية دائمة)")
        return redirect(edit_url)
    return '''
    <!DOCTYPE html>
    <html dir="rtl">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>التحقق من البريد الإلكتروني</title>
    <style>body{font-family:'Inter',sans-serif;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);margin:0;padding:20px;min-height:100vh;display:flex;align-items:center;justify-content:center;}
    .card{max-width:420px;width:100%;background:rgba(255,255,255,0.95);backdrop-filter:blur(10px);border-radius:48px;box-shadow:0 25px 50px -12px rgba(0,0,0,0.25);overflow:hidden;border:1px solid rgba(255,255,255,0.2);}
    .header{background:linear-gradient(135deg,#667eea,#764ba2);padding:32px;text-align:center;color:white;}
    .header h1{margin:0;font-size:28px;font-weight:700;}
    .content{padding:32px;}
    input{width:100%;padding:14px 18px;margin:8px 0;border:1px solid #e5e7eb;border-radius:32px;font-size:16px;background:#f9fafb;transition:0.2s;}
    input:focus{outline:none;border-color:#8b5cf6;box-shadow:0 0 0 3px rgba(139,92,246,0.2);}
    button{background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;padding:14px;font-size:16px;font-weight:600;border-radius:40px;width:100%;cursor:pointer;margin-top:15px;}
    button:hover{transform:translateY(-2px);box-shadow:0 10px 20px -5px rgba(102,126,234,0.4);}
    .info{background:#f3f4f6;border-radius:32px;padding:14px;margin-bottom:20px;font-size:13px;text-align:center;color:#4b5563;}</style>
    </head>
    <body><div class="card"><div class="header"><h1>🔐 التحقق من البريد</h1></div><div class="content"><div class="info">💡 أدخل بريدك الجامعي المسجل في النظام للوصول إلى صفحة تعديل المعاملة.</div>
    <form method="POST"><input type="email" name="email" placeholder="example@it.jan.ah" required><button type="submit">تحقق</button></form></div></div></body></html>
    '''

# ------------------ صفحة تعديل المعاملة ------------------
EDIT_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>تعديل المعاملة | نظام التتبع</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        * { font-family: 'Inter', sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 24px; }
        .glass-card { background: rgba(255,255,255,0.95); backdrop-filter: blur(10px); border-radius: 32px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.25); border: 1px solid rgba(255,255,255,0.2); transition: all 0.3s; }
        .status-badge { display: inline-block; padding: 6px 16px; border-radius: 40px; font-size: 13px; font-weight: 600; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .status-new { background: #e5e7eb; color: #1f2937; }
        .status-processing { background: #fef3c7; color: #b45309; }
        .status-completed { background: #d1fae5; color: #065f46; }
        .status-delayed { background: #fee2e2; color: #991b1b; }
        .timeline { position: relative; padding-right: 30px; }
        .timeline-item { position: relative; padding-bottom: 28px; border-right: 2px solid #c4b5fd; margin-right: 12px; }
        .timeline-dot { position: absolute; right: -9px; top: 5px; width: 14px; height: 14px; background: #8b5cf6; border-radius: 50%; box-shadow: 0 0 0 4px rgba(139,92,246,0.2); }
        .timeline-time { font-size: 12px; color: #6c757d; margin-bottom: 4px; direction: ltr; text-align: right; }
        .timeline-action { font-weight: 600; color: #1f2937; margin-bottom: 4px; }
        .timeline-user { font-size: 12px; color: #9ca3af; }
        .btn-save { background: linear-gradient(135deg, #667eea, #764ba2); transition: transform 0.2s, box-shadow 0.2s; }
        .btn-save:hover { transform: translateY(-2px); box-shadow: 0 10px 20px -5px rgba(102,126,234,0.4); }
        .info-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; }
        .info-card { background: #faf5ff; border-radius: 24px; padding: 20px; transition: all 0.2s; border: 1px solid #f3e8ff; }
        .info-card:hover { transform: translateY(-2px); box-shadow: 0 8px 15px rgba(0,0,0,0.05); }
        .label { font-size: 12px; font-weight: 600; color: #8b5cf6; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; display: block; }
        .value { font-size: 16px; font-weight: 500; color: #1f2937; word-break: break-word; }
        input, select, textarea { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 16px; padding: 12px 16px; font-size: 15px; width: 100%; transition: 0.2s; }
        input:focus, select:focus, textarea:focus { border-color: #8b5cf6; outline: none; box-shadow: 0 0 0 3px rgba(139,92,246,0.1); }
        .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); background: #1f2937; color: white; padding: 12px 24px; border-radius: 40px; font-size: 14px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 50; }
    </style>
</head>
<body>
    <div class="max-w-5xl mx-auto">
        <div class="glass-card p-6 mb-6 text-center md:text-right">
            <h1 class="text-2xl font-bold bg-gradient-to-r from-purple-600 to-blue-600 bg-clip-text text-transparent">📝 تعديل المعاملة</h1>
            <p class="text-gray-500 text-sm mt-1">رقم المعاملة: <span id="transaction-id" class="font-mono text-purple-600"></span></p>
        </div>
        <div class="glass-card p-6 mb-6">
            <h2 class="text-lg font-semibold flex items-center gap-2 mb-5 text-purple-700">📋 <span>معلومات أساسية</span></h2>
            <div id="readonly-fields" class="info-grid"></div>
        </div>
        <div class="glass-card p-6 mb-6">
            <h2 class="text-lg font-semibold flex items-center gap-2 mb-5 text-purple-700">✏️ <span>تحديث البيانات</span></h2>
            <form id="editForm" class="space-y-5">
                <div id="editable-fields" class="info-grid"></div>
                <button type="submit" class="btn-save w-full text-white font-semibold py-3 rounded-xl transition shadow-md">💾 حفظ التغييرات</button>
            </form>
        </div>
        <div class="glass-card p-6 mb-6">
            <h2 class="text-lg font-semibold flex items-center gap-2 mb-5 text-purple-700">📜 <span>سجل الحركات</span></h2>
            <div id="history-timeline" class="timeline"></div>
        </div>
    </div>
    <div id="message" class="toast"></div>
    <script>
        const id = window.location.pathname.split('/').pop();
        document.getElementById('transaction-id').innerText = id;
        function showMessage(text, isError = false) {
            const msgDiv = document.getElementById('message');
            msgDiv.innerText = text;
            msgDiv.style.background = isError ? '#dc2626' : '#1f2937';
            msgDiv.style.opacity = '1';
            setTimeout(() => msgDiv.style.opacity = '0', 3000);
        }
        function formatDateTime(dateStr) {
            if (!dateStr) return '—';
            try {
                let d = new Date(dateStr);
                if (isNaN(d.getTime())) d = new Date(dateStr.replace(' ', 'T'));
                if (isNaN(d.getTime())) return dateStr;
                return d.toLocaleString('en-GB', { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit' }).replace(',', '');
            } catch(e) { return dateStr; }
        }
        Promise.all([
            fetch(`/api/transaction/${id}`).then(r => r.json()),
            fetch('/api/headers').then(r => r.json())
        ]).then(([data, headers]) => {
            const readonlyKeys = ['Timestamp', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف', 'الوظيفة', 'القسم', 'نوع المعاملة', 'المرافقات', 'ID'];
            const excludedKeys = ['LOG_JSON', 'الرابط', 'عدد التعديلات', 'البريد الإلكتروني الموظف'];
            const rc = document.getElementById('readonly-fields');
            rc.innerHTML = '';
            readonlyKeys.forEach(key => {
                if (data[key] !== undefined) {
                    let value = data[key] || '—';
                    let display = value;
                    if (key === 'المرافقات' && value.startsWith('http')) {
                        display = `<a href="${value}" target="_blank" class="text-blue-600 underline">📎 فتح المرفق</a>`;
                    } else if (key === 'Timestamp') {
                        display = formatDateTime(value);
                    }
                    rc.innerHTML += `<div class="info-card"><div class="label">${key}</div><div class="value">${display}</div></div>`;
                }
            });
            const editableKeys = headers.filter(key => !readonlyKeys.includes(key) && !excludedKeys.includes(key));
            const ec = document.getElementById('editable-fields');
            ec.innerHTML = '';
            editableKeys.forEach(key => {
                let inputType = 'text';
                let options = '';
                if (key.includes('تاريخ')) {
                    inputType = 'date';
                } else if (key === 'الحالة') {
                    inputType = 'select';
                    options = `<select name="${key}" class="w-full p-3 border rounded-xl bg-gray-50 focus:border-purple-500">
                        <option value="جديد" ${data[key] === 'جديد' ? 'selected' : ''}>جديد</option>
                        <option value="قيد المعالجة" ${data[key] === 'قيد المعالجة' ? 'selected' : ''}>قيد المعالجة</option>
                        <option value="مكتملة" ${data[key] === 'مكتملة' ? 'selected' : ''}>مكتملة</option>
                        <option value="متأخرة" ${data[key] === 'متأخرة' ? 'selected' : ''}>متأخرة</option>
                    </select>`;
                } else if (key === 'التأخير') {
                    inputType = 'select';
                    options = `<select name="${key}" class="w-full p-3 border rounded-xl bg-gray-50">
                        <option value="لا" ${data[key] !== 'نعم' ? 'selected' : ''}>لا</option>
                        <option value="نعم" ${data[key] === 'نعم' ? 'selected' : ''}>نعم</option>
                    </select>`;
                } else if (key === 'الأولوية') {
                    inputType = 'select';
                    options = `<select name="${key}" class="w-full p-3 border rounded-xl bg-gray-50">
                        <option value="عادية" ${data[key] !== 'مستعجلة' ? 'selected' : ''}>عادية</option>
                        <option value="مستعجلة" ${data[key] === 'مستعجلة' ? 'selected' : ''}>مستعجلة</option>
                    </select>`;
                }
                const currentValue = data[key] || '';
                if (inputType === 'select') {
                    ec.innerHTML += `<div><div class="label">${key}</div>${options}</div>`;
                } else if (inputType === 'date') {
                    let val = currentValue.split('T')[0] || '';
                    ec.innerHTML += `<div><div class="label">${key}</div><input type="date" name="${key}" value="${val}" class="w-full p-3 border rounded-xl"></div>`;
                } else {
                    ec.innerHTML += `<div><div class="label">${key}</div><input type="text" name="${key}" value="${currentValue}" class="w-full p-3 border rounded-xl"></div>`;
                }
            });
        }).catch(() => {
            document.body.innerHTML = '<div class="text-center text-red-500 p-10">❌ المعاملة غير موجودة أو حدث خطأ في تحميل البيانات</div>';
        });
        document.getElementById('editForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const updates = Object.fromEntries(formData.entries());
            const res = await fetch(`/api/transaction/${id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(updates) });
            const result = await res.json();
            if (result.success) {
                showMessage('✅ تم الحفظ بنجاح');
                loadHistory();
            } else {
                showMessage('❌ فشل الحفظ', true);
            }
        });
        function loadHistory() {
            fetch(`/api/history/${id}`).then(r => r.json()).then(h => {
                const t = document.getElementById('history-timeline');
                if (h.length === 0) { t.innerHTML = '<p class="text-gray-500 text-center py-8">لا يوجد سجل</p>'; return; }
                let html = '';
                h.forEach(i => {
                    let timeFormatted = formatDateTime(i.time);
                    html += `<div class="timeline-item"><span class="timeline-dot"></span><div class="timeline-time">${timeFormatted}</div><div class="timeline-action">${i.action}</div><div class="timeline-user">بواسطة: ${i.user}</div></div>`;
                });
                t.innerHTML = html;
            });
        }
        loadHistory();
    </script>
</body>
</html>
"""

@app.route('/transaction/<id>')
def edit_transaction_page(id):
    token = request.args.get('token')
    if not token:
        return redirect(url_for('verify_email_page', transaction_id=id))
    if not sheets_client or not sheets_client.verify_access_token(token, id):
        abort(403, description="رمز الوصول غير صالح أو منتهي الصلاحية.")
    return render_template_string(EDIT_HTML)

# ------------------ صفحة المدير ------------------
INDEX_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>لوحة التحكم - المعاملات</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .status-badge { display: inline-block; padding: 4px 12px; border-radius: 40px; font-size: 12px; font-weight: 600; }
        .status-new { background: #e5e7eb; color: #1f2937; }
        .status-processing { background: #fef3c7; color: #b45309; }
        .status-completed { background: #d1fae5; color: #065f46; }
        .status-delayed { background: #fee2e2; color: #991b1b; }
        table { border-collapse: separate; border-spacing: 0 8px; }
        td, th { padding: 12px 16px; }
        tr { background: white; border-radius: 16px; transition: 0.2s; }
        tr:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,0.1); }
        .btn-edit { background: linear-gradient(135deg, #8b5cf6, #6366f1); padding: 6px 14px; border-radius: 40px; color: white; font-size: 13px; transition: 0.2s; }
        .btn-edit:hover { transform: translateY(-1px); box-shadow: 0 4px 10px rgba(139,92,246,0.4); }
    </style>
</head>
<body class="p-4">
    <div class="max-w-6xl mx-auto">
        <div class="bg-white/90 backdrop-blur rounded-2xl p-6 mb-6 shadow-xl">
            <h1 class="text-3xl font-bold bg-gradient-to-r from-purple-600 to-blue-600 bg-clip-text text-transparent">📋 جميع المعاملات</h1>
            <p class="text-gray-500 mt-1">لوحة تحكم المدير</p>
        </div>
        <div class="mb-4">
            <input type="text" id="searchInput" placeholder="🔍 ابحث بـ ID أو الاسم أو الحالة..." class="w-full p-3 border-0 rounded-2xl shadow-md focus:ring-2 focus:ring-purple-400 bg-white/90 backdrop-blur">
        </div>
        <div class="overflow-x-auto">
            <table class="min-w-full">
                <thead>
                    <tr class="bg-white/80 backdrop-blur shadow-sm rounded-2xl">
                        <th class="text-right px-4 py-3 text-purple-800">ID</th>
                        <th class="text-right px-4 py-3 text-purple-800">الاسم</th>
                        <th class="text-right px-4 py-3 text-purple-800">الحالة</th>
                        <th class="text-right px-4 py-3 text-purple-800">الموظف</th>
                        <th class="text-right px-4 py-3 text-purple-800">القسم</th>
                        <th class="text-right px-4 py-3 text-purple-800">آخر تعديل</th>
                        <th class="text-right px-4 py-3 text-purple-800"></th>
                    </tr>
                </thead>
                <tbody id="transactions"></tbody>
            </table>
        </div>
    </div>
    <script>
        function formatDate(dateStr) {
            if (!dateStr) return '—';
            try {
                let d = new Date(dateStr);
                if (isNaN(d.getTime())) return dateStr;
                return d.toLocaleString('en-GB', { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
            } catch(e) { return dateStr; }
        }
        function getStatusClass(status) {
            if (status === 'جديد') return 'status-new';
            if (status === 'قيد المعالجة') return 'status-processing';
            if (status === 'مكتملة') return 'status-completed';
            if (status === 'متأخرة') return 'status-delayed';
            return '';
        }
        fetch('/api/transactions').then(r=>r.json()).then(data => {
            const tbody = document.getElementById('transactions');
            data.forEach(t => {
                const statusClass = getStatusClass(t.status);
                const row = `<tr class="shadow-sm"><td class="rounded-r-2xl font-mono text-sm">${t.id}</td><td>${t.name || '—'}</td><td><span class="status-badge ${statusClass}">${t.status || '—'}</span></table><td>${t.employee || '—'}</td><td>${t.department || '—'}</td><td class="text-left" dir="ltr">${formatDate(t.last_modified)}</td><td class="rounded-l-2xl"><a href="/transaction/${t.id}" class="btn-edit inline-block">✏️ تعديل</a></td></tr>`;
                tbody.innerHTML += row;
            });
        });
        document.getElementById('searchInput').addEventListener('keyup', function() {
            let filter = this.value.toLowerCase();
            let rows = document.querySelectorAll('#transactions tr');
            rows.forEach(row => {
                let text = row.innerText.toLowerCase();
                row.style.display = text.includes(filter) ? '' : 'none';
            });
        });
    </script>
</body>
</html>"""

@app.route('/')
def index():
    token = request.args.get('token')
    if not token or token != Config.ADMIN_SECRET:
        abort(403)
    return render_template_string(INDEX_HTML)

# ------------------ صفحات QR ------------------
@app.route('/qr/<id>')
def qr_page(id):
    base_url = request.host_url.rstrip('/')
    direct_token = sheets_client.get_direct_token(id) if sheets_client else None
    if direct_token:
        edit_link = f"{base_url}/transaction/{id}?token={direct_token}"
    else:
        edit_link = f"{base_url}/verify-email?transaction_id={id}"
    qr_base64 = QRGenerator.generate_qr(edit_link)
    return f'''
    <!DOCTYPE html>
    <html dir="rtl"><head><meta charset="UTF-8"><title>QR Code للمعاملة {id}</title>
    <style>body{{font-family:sans-serif;background:#f0f2f5;text-align:center;padding:20px;}}.card{{max-width:500px;margin:50px auto;background:white;border-radius:24px;padding:30px;}}.qr{{margin:20px 0;}}</style>
    </head><body><div class="card"><h2>📱 رمز QR للمعاملة</h2><div class="qr"><img src="data:image/png;base64,{qr_base64}" width="200"></div>
    <p><strong>🔹 تعليمات التتبع:</strong><br>1️⃣ امسح الرمز<br>2️⃣ سيتم نقلك إلى صفحة التعديل<br>3️⃣ يمكنك متابعة المعاملة عبر البوت:</p>
    <a href="https://t.me/{Config.BOT_USERNAME}?start={id}" style="background:#0088cc;color:white;padding:10px 20px;border-radius:40px;text-decoration:none;">📱 فتح البوت</a>
    <p style="margin-top:15px;font-size:12px;">⚠️ احتفظ برقم المعاملة: <strong>{id}</strong></p></div></body></html>
    '''

@app.route('/qr_image/<id>')
def qr_image(id):
    base_url = request.host_url.rstrip('/')
    direct_token = sheets_client.get_direct_token(id) if sheets_client else None
    if direct_token:
        edit_link = f"{base_url}/transaction/{id}?token={direct_token}"
    else:
        edit_link = f"{base_url}/verify-email?transaction_id={id}"
    qr_base64 = QRGenerator.generate_qr(edit_link)
    return Response(base64.b64decode(qr_base64), mimetype='image/png')

# ------------------ صفحات الويب الأخرى ------------------
@app.route('/register', methods=['GET', 'POST'])
def register_transaction():
    if request.method == 'GET':
        return '''
        <!DOCTYPE html>
        <html dir="rtl">
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>تسجيل معاملة جديدة</title>
        <style>body{font-family:sans-serif;background:linear-gradient(135deg,#f5f0ff 0%,#f0f2f5 100%);margin:0;padding:20px;}
        .container{max-width:700px;margin:20px auto;background:white;border-radius:32px;box-shadow:0 20px 35px -10px rgba(0,0,0,0.1);overflow:hidden;}
        .header{background:#8b5cf6;color:white;padding:30px;text-align:center;}
        .content{padding:30px;}
        .form-group{margin-bottom:20px;}
        label{display:block;margin-bottom:8px;font-weight:600;}
        input,select,textarea{width:100%;padding:12px 16px;border:1px solid #e5e7eb;border-radius:16px;font-size:16px;background:#f9fafb;}
        button{background:#8b5cf6;color:white;border:none;padding:14px 24px;font-size:18px;font-weight:600;border-radius:40px;width:100%;cursor:pointer;margin-top:10px;}
        .required:after{content:" *";color:#ef4444;}
        .info-box{background:#f3f4f6;border-radius:20px;padding:15px;margin-bottom:20px;font-size:14px;text-align:center;}
        .result{margin-top:20px;padding:15px;border-radius:20px;display:none;}
        .result.success{background:#d1fae5;color:#065f46;display:block;}
        .result.error{background:#fee2e2;color:#991b1b;display:block;}</style>
        </head>
        <body>
        <div class="container"><div class="header"><h1>📝 إستمارة تسجيل معاملة </h1><p>املأ البيانات التالية</p></div>
        <div class="content"><div class="info-box">💡 بعد الإرسال سيتم إنشاء رقم معاملة فريد.</div>
        <form id="transactionForm" enctype="multipart/form-data">
            <div class="form-group"><label class="required">الاسم الثلاثي</label><input type="text" id="name" name="name" required placeholder="أكتب..."></div>
            <div class="form-group"><label class="required">رقم الهاتف</label><input type="text" id="phone" name="phone" required placeholder="أكتب..."></div>
            <div class="form-group"><label class="required">الوظيفة</label><select id="function" name="function" required><option value="طالب">طالب</option><option value="تدريسي">تدريسي</option><option value="أخرى">أخرى</option></select></div>
            <div class="form-group"><label class="required">القسم</label><select id="department" name="department" required>
                <option value="قسم تكنولوجيا المعلومات و الإتصالات">قسم تكنولوجيا المعلومات و الإتصالات</option>
                <option value="قسم التقنيات الكهربائية">قسم التقنيات الكهربائية</option>
                <option value="قسم تقنيات المكائن والمعدات">قسم تقنيات المكائن والمعدات</option>
                <option value="قسم التقنيات الميكانيكية">قسم التقنيات الميكانيكية</option>
                <option value="قسم التقنيات الإلكترونية">قسم التقنيات الإلكترونية</option>
                <option value="قسم تقنيات الصناعات الكيمياوية">قسم تقنيات الصناعات الكيمياوية</option>
                <option value="قسم تقنيات المساحة">قسم تقنيات المساحة</option>
                <option value="قسم تقنيات الموارد المائية">قسم تقنيات الموارد المائية</option>
                <option value="قسم تقنيات الأجهزة الطبية">قسم تقنيات الأجهزة الطبية</option>
            </select></div>
            <div class="form-group"><label>نوع المعاملة</label><input type="text" id="transaction_type" name="transaction_type" placeholder="مثال: تتبع، استعلام، شكوى، اقتراح"></div>
            <div class="form-group"><label>ملاحظات (اختياري) </label><textarea id="attachments_text" name="attachments_text" rows="2" placeholder="أي ملاحظات إضافية..."></textarea></div>
            <div class="form-group"><label>رفع ملف (اختياري)</label><input type="file" id="attachment_file" name="attachment_file"><small style="color:#6c757d;">سيتم رفع الملف إلى Drive.</small></div>
            <button type="submit" id="submitBtn">إرسال المعاملة</button>
        </form>
        <div id="result" class="result"></div></div></div>
        <script>
        document.getElementById('transactionForm').addEventListener('submit', async (e) => {
            e.preventDefault(); const btn = document.getElementById('submitBtn'); const resDiv = document.getElementById('result');
            btn.disabled = true; const original = btn.textContent; btn.textContent = 'جاري الإرسال...'; resDiv.innerHTML = '<div>جاري التسجيل...</div>'; resDiv.className = 'result';
            try {
                const formData = new FormData(e.target);
                const res = await fetch('/api/submit', { method: 'POST', body: formData });
                const json = await res.json();
                if (json.success) {
                    resDiv.innerHTML = `<div style="text-align:center;">✅ تم التسجيل<br>🆔 رقم المعاملة: <strong>${json.id}</strong><br><br>
                    <a href="${json.edit_link}" target="_blank" style="background:#8b5cf6;color:white;padding:8px 16px;border-radius:40px;text-decoration:none;display:inline-block;margin:5px;">🔗 عرض التفاصيل</a>
                    <a href="${json.deep_link}" target="_blank" style="background:#2c3e50;color:white;padding:8px 16px;border-radius:40px;text-decoration:none;display:inline-block;margin:5px;">📱 فتح البوت</a>
                    <p style="margin-top:15px;font-size:13px;">احتفظ برقم المعاملة لمتابعة معاملتك.</p></div>`;
                    resDiv.classList.add('success');
                } else {
                    resDiv.innerHTML = `❌ فشل التسجيل: ${json.error || 'خطأ غير معروف'}`;
                    resDiv.classList.add('error'); btn.disabled = false; btn.textContent = original;
                }
            } catch(err) { resDiv.innerHTML = '❌ خطأ في الاتصال'; resDiv.classList.add('error'); btn.disabled = false; btn.textContent = original; }
        });
        </script>
        </body></html>
        '''
    else:
        return "Use /api/submit", 405

@app.route('/verify', methods=['GET'])
def verify_page():
    name = request.args.get('name', '').strip()
    phone = request.args.get('phone', '').strip()
    if not name or not phone:
        return '''
        <!DOCTYPE html><html dir="rtl"><head><meta charset="UTF-8"><title>التحقق من المعاملة</title>
        <style>body{font-family:sans-serif;background:linear-gradient(135deg,#f5f0ff 0%,#f0f2f5 100%);margin:0;padding:20px;}
        .card{max-width:450px;margin:50px auto;background:white;border-radius:32px;overflow:hidden;}
        .header{background:#8b5cf6;color:white;padding:30px;text-align:center;}
        .content{padding:30px;}
        input{width:100%;padding:12px 16px;margin:8px 0;border:1px solid #e5e7eb;border-radius:16px;}
        button{background:#8b5cf6;color:white;border:none;padding:14px;border-radius:40px;width:100%;cursor:pointer;}
        .info{background:#f3f4f6;border-radius:20px;padding:12px;margin-bottom:20px;font-size:13px;text-align:center;}</style>
        </head><body><div class="card"><div class="header"><h1>🔍 التحقق من المعاملة</h1></div>
        <div class="content"><div class="info">💡 أدخل اسمك الثلاثي ورقم هاتفك كما في معاملتك</div>
        <form method="GET"><input type="text" name="name" placeholder="الاسم الثلاثي" required><input type="text" name="phone" placeholder="رقم الهاتف" required><button type="submit">تحقق</button></form></div></div></body></html>
        '''
    if not sheets_client:
        return "<html dir='rtl'><body><h2>⚠️ النظام غير متصل بقاعدة البيانات</h2></body></html>"
    records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
    found = False
    transaction_id = None
    name_clean = name.strip().lower()
    phone_clean = phone.strip()
    for row in records:
        row_name = str(row.get('اسم صاحب المعاملة الثلاثي', '')).strip().lower()
        row_phone = str(row.get('رقم الهاتف', '')).strip()
        if row_name == name_clean and row_phone == phone_clean:
            transaction_id = row.get('ID')
            if transaction_id:
                found = True
                break
    if found and transaction_id:
        base_url = request.host_url.rstrip('/')
        return f"""
        <!DOCTYPE html><html dir="rtl"><head><meta charset="UTF-8"><title>معاملتك</title>
        <style>body{{font-family:sans-serif;background:linear-gradient(135deg,#f5f0ff 0%,#f0f2f5 100%);margin:0;padding:20px;}}
        .card{{max-width:550px;margin:50px auto;background:white;border-radius:32px;overflow:hidden;}}
        .header{{background:#8b5cf6;color:white;padding:30px;text-align:center;}}
        .id{{font-size:32px;font-weight:bold;color:#8b5cf6;background:#f5f0ff;display:inline-block;padding:12px 28px;border-radius:60px;margin:20px 0;}}
        .btn{{display:inline-block;background:#8b5cf6;color:white;padding:12px 28px;border-radius:40px;margin:10px;text-decoration:none;}}
        .btn-telegram{{background:#2c3e50;}}
        .content{{padding:30px;text-align:center;}}</style>
        </head><body><div class="card"><div class="header"><h2>✅ تم العثور على معاملتك</h2></div>
        <div class="content"><p>رقم المعاملة الخاص بك:</p><div class="id">{transaction_id}</div>
        <a href="{base_url}/view/{transaction_id}" target="_blank" class="btn">🔗 عرض التفاصيل</a>
        <a href="https://t.me/{Config.BOT_USERNAME}?start={transaction_id}" target="_blank" class="btn btn-telegram">📱 فتح البوت</a></div></div></body></html>
        """
    else:
        return f"""
        <!DOCTYPE html><html dir="rtl"><body style="text-align:center;margin-top:50px;">
        <h2>❌ لم نجد معاملة بهذه البيانات</h2><p>الاسم: "{name}"</p><p>الهاتف: "{phone}"</p><p><a href="/verify">🔍 محاولة مرة أخرى</a></p></body></html>
        """

@app.route('/view/<id>')
def view_transaction_page(id):
    try:
        if not sheets_client:
            return "⚠️ النظام غير متصل بقاعدة البيانات", 500
        data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, id)
        if not data:
            return f"❌ المعاملة {id} غير موجودة", 404
        history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        history = []
        if history_ws:
            records = history_ws.get_all_records()
            history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
                       for r in records if str(r.get('ID')) == id]
            history.sort(key=lambda x: x['time'], reverse=False)
        html = f"""
        <!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8"><title>تفاصيل المعاملة {id}</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>*{{margin:0;padding:0;box-sizing:border-box;}}body{{font-family:'Inter',sans-serif;background:linear-gradient(135deg,#f9f5ff 0%,#f3e8ff 100%);padding:24px;min-height:100vh;}}
        .container{{max-width:1000px;margin:0 auto;}}.card{{background:white;border-radius:32px;box-shadow:0 25px 50px -12px rgba(0,0,0,0.1);overflow:hidden;margin-bottom:24px;}}
        .card-header{{background:#8b5cf6;padding:28px 32px;color:white;}}.card-header h1{{font-size:28px;font-weight:700;}}
        .card-content{{padding:32px;}}.info-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;margin-bottom:32px;}}
        .info-item{{background:#faf5ff;border-radius:24px;padding:20px;}}.info-label{{font-size:13px;font-weight:600;color:#8b5cf6;margin-bottom:8px;}}
        .info-value{{font-size:16px;font-weight:500;color:#1f2937;word-break:break-word;}}
        .status-badge{{display:inline-block;padding:6px 14px;border-radius:40px;font-size:13px;font-weight:600;}}
        .status-new{{background:#e2e3e5;color:#383d41;}}.status-processing{{background:#fff3cd;color:#856404;}}
        .status-completed{{background:#d4edda;color:#155724;}}.status-delayed{{background:#f8d7da;color:#721c24;}}
        .timeline{{position:relative;padding-right:30px;}}.timeline-item{{position:relative;padding-bottom:28px;border-right:2px solid #e9d5ff;margin-right:12px;}}
        .timeline-dot{{position:absolute;right:-10px;top:4px;width:16px;height:16px;background:#8b5cf6;border-radius:50%;box-shadow:0 0 0 4px #faf5ff;}}
        .timeline-time{{font-size:12px;color:#6c757d;margin-bottom:4px;}}.timeline-action{{font-weight:600;margin-bottom:4px;}}
        .instructions{{background:#faf5ff;border-radius:24px;padding:20px;margin-top:24px;text-align:center;}}
        .btn{{display:inline-block;background:#8b5cf6;color:white;padding:10px 20px;border-radius:40px;text-decoration:none;margin-top:12px;}}
        hr{{margin:20px 0;border:none;height:1px;background:#e9d5ff;}}</style>
        </head><body><div class="container"><div class="card"><div class="card-header"><h1>🔍 تفاصيل المعاملة</h1><p>رقم المعاملة: <strong>{id}</strong> | للمتابعة فقط</p></div>
        <div class="card-content"><div class="info-grid">
        """
        excluded = ['ID', 'LOG_JSON', 'آخر تعديل بتاريخ', 'آخر تعديل بواسطة', 'الرابط', 'عدد التعديلات', 'البريد الإلكتروني الموظف']
        for key, value in data.items():
            if key not in excluded:
                display_value = value if value else '—'
                if key == 'المرافقات' and value and value.startswith('http'):
                    display_value = f'<a href="{value}" target="_blank" style="color:#8b5cf6;text-decoration:underline;">📎 فتح المرفق</a>'
                if key == 'الحالة':
                    badge_class = "status-new" if value == "جديد" else ("status-processing" if value == "قيد المعالجة" else ("status-completed" if value == "مكتملة" else ("status-delayed" if value == "متأخرة" else "")))
                    display_value = f'<span class="status-badge {badge_class}">{value if value else "—"}</span>'
                html += f"""<div class="info-item"><div class="info-label">{key}</div><div class="info-value">{display_value}</div></div>"""
        html += """</div><h3 style="font-size:20px;font-weight:600;margin-bottom:20px;">📜 سجل الحركات</h3><div class="timeline">"""
        if history:
            for entry in history:
                try:
                    dt = datetime.fromisoformat(entry['time'])
                    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    time_str = entry['time']
                html += f"""<div class="timeline-item"><div class="timeline-dot"></div><div class="timeline-time">{time_str}</div><div class="timeline-action">{entry['action']}</div><div class="timeline-user">بواسطة: {entry['user']}</div></div>"""
        else:
            html += '<p style="color:#6c757d;">لا يوجد سجل بعد</p>'
        html += f"""</div><div class="instructions"><p>💡 يمكنك متابعة معاملتك عبر البوت:</p><a href="https://t.me/{Config.BOT_USERNAME}?start={id}" class="btn">📱 فتح البوت</a><hr><p style="font-size:13px;">⚠️ احتفظ برقم المعاملة هذا لمتابعة حالتك.</p></div></div></div></div></body></html>"""
        return html
    except Exception as e:
        logger.error(f"🔥 خطأ في عرض المعاملة {id}: {e}", exc_info=True)
        return f"حدث خطأ أثناء تحميل الصفحة: {str(e)}", 500

# ------------------ معالجة المعاملات الجديدة ------------------
last_row_count = 0

def process_new_transaction(ws, row_number, new_row, transaction_id):
    try:
        if not transaction_id:
            now = datetime.now()
            date_str = now.strftime("%Y%m%d%H%M%S")
            random_part = random.randint(1000, 9999)
            transaction_id = f"MUT-{date_str}-{random_part}"
            headers = ws.row_values(1)
            try:
                id_col = headers.index('ID') + 1
                ws.update_cell(row_number, id_col, transaction_id)
            except ValueError:
                ws.update_cell(row_number, 8, transaction_id)
            logger.info(f"🆔 تم توليد ID {transaction_id} للصف {row_number}")
        base_url = request.host_url.rstrip('/')
        edit_link = f"{base_url}/transaction/{transaction_id}"
        hyperlink_formula = f'=HYPERLINK("{edit_link}", "تعديل المعاملة")'
        try:
            headers = ws.row_values(1)
            link_col = headers.index('الرابط') + 1
            ws.update_cell(row_number, link_col, hyperlink_formula)
        except ValueError:
            ws.update_cell(row_number, 21, hyperlink_formula)
        qr_ws = sheets_client.get_worksheet(Config.SHEET_QR)
        if qr_ws:
            qr_row = [transaction_id, f'=IMAGE("{base_url}/qr_image/{transaction_id}")', hyperlink_formula]
            sheets_client.safe_append_row(qr_ws, qr_row, batch=True)
        customer_email = new_row.get('البريد الإلكتروني')
        customer_name = new_row.get('اسم صاحب المعاملة الثلاثي')
        if transaction_id and customer_email:
            qr_page_link = f"{base_url}/qr/{transaction_id}"
            try:
                from email_service import EmailService
                success = EmailService.send_customer_email(customer_email, customer_name, transaction_id, qr_page_link)
                if success:
                    logger.info(f"📧 تم إرسال إيميل للمعاملة {transaction_id}")
                else:
                    logger.error(f"❌ فشل إرسال الإيميل للمعاملة {transaction_id}")
            except Exception as e:
                logger.error(f"خطأ في إرسال الإيميل: {e}")
        history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        if history_ws:
            now = datetime.now()
            timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
            sheets_client.safe_append_row(history_ws, [timestamp, transaction_id, "تم إنشاء المعاملة", "النظام (API)"], batch=True)
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة المعاملة {transaction_id}: {e}", exc_info=True)

def check_new_transactions():
    global last_row_count
    try:
        if not sheets_client:
            return
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return
        all_values = ws.get_all_values()
        current_count = len(all_values) - 1
        if current_count > last_row_count:
            logger.info(f"📦 تم اكتشاف {current_count - last_row_count} معاملات جديدة")
            records = ws.get_all_records()
            for i in range(last_row_count, current_count):
                row_number = i + 2
                new_row = records[i]
                transaction_id = new_row.get('ID')
                if not transaction_id:
                    logger.warning(f"⚠️ صف {row_number} ليس له ID، سيتم معالجته لاحقًا")
                    continue
                executor.submit(process_new_transaction, ws, row_number, new_row, transaction_id)
            last_row_count = current_count
            logger.info(f"✅ تم تفويض المعاملات الجديدة للمعالجة المتوازية")
        else:
            logger.debug(f"لا توجد معاملات جديدة (last={last_row_count}, current={current_count})")
    except Exception as e:
        logger.error(f"❌ خطأ في دالة المراقبة: {e}", exc_info=True)

if sheets_client:
    try:
        ws_temp = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        last_row_count = len(ws_temp.get_all_values()) - 1 if ws_temp else 0
    except Exception as e:
        logger.error(f"❌ فشل قراءة العدد الأولي: {e}")
        last_row_count = 0
    scheduler = BackgroundScheduler()
    scheduler.start()
    scheduler.add_job(func=check_new_transactions, trigger=IntervalTrigger(seconds=30), id='check_transactions', replace_existing=True)
    logger.info("🔍 بدأت مراقبة المعاملات الجديدة (كل 30 ثانية)")
    atexit.register(lambda: scheduler.shutdown())
    atexit.register(lambda: executor.shutdown(wait=False))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
