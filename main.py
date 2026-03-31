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
from flask import Flask, request, jsonify, render_template_string, Response, abort, redirect, url_for, session
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit
import requests
from datetime import datetime, timedelta

from sheets import GoogleSheetsClient
from config import Config
from qr_generator import QRGenerator
from ai_handler import AIAssistant

# ------------------ التحقق من المتغيرات البيئية ------------------
required_env_vars = ['GOOGLE_CREDENTIALS_JSON', 'SPREADSHEET_ID', 'TELEGRAM_BOT_TOKEN', 'WEB_APP_URL']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    logging.error(f"❌ المتغيرات البيئية المفقودة: {', '.join(missing_vars)}")
else:
    logging.info("✅ جميع المتغيرات البيئية الأساسية موجودة")

# تأكد من أن WEB_APP_URL لا يحتوي على / في النهاية
web_app_url = os.getenv('WEB_APP_URL', '').rstrip('/')
os.environ['WEB_APP_URL'] = web_app_url
logging.info(f"WEB_APP_URL المستخدمة: {web_app_url}")

# ------------------ إعداد التسجيل ------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ------------------ Google Sheets ------------------
sheets_client = None
try:
    sheets_client = GoogleSheetsClient()
    logger.info("✅ تم الاتصال بـ Google Sheets")
except Exception as e:
    logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
    sheets_client = None

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))

# ------------------ الذكاء الاصطناعي ------------------
ai_assistant = None
try:
    ai_assistant = AIAssistant(sheets_client=sheets_client)
    logger.info("✅ تم تهيئة Groq AI")
except Exception as e:
    logger.error(f"❌ فشل تهيئة Groq AI: {e}")
    ai_assistant = None

# ------------------ فحص ورقة manager عند بدء التشغيل ------------------
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
                    await bot_app.bot.send_message(
                        chat_id=int(chat_id),
                        text=message,
                        parse_mode='Markdown'
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

# ------------------ دوال مساعدة للمدير ------------------
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
                    f"✅ تم ربط حسابك بالمعاملة\n\n"
                    f"🆔 {transaction_id}\n"
                    f" استعمل رقم معاملتك (🆔)للحصول على معلومات حول معاملتك "
                )
            else:
                await update.message.reply_text("❌ المعاملة غير موجودة")
        else:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return

    keyboard = [
        [InlineKeyboardButton("🔍 تفاصيل معاملة", callback_data="cmd_id")],
        [InlineKeyboardButton("📜 سجل تتبع معاملة", callback_data="cmd_history")],
        [InlineKeyboardButton("📱 تعليمات QR", callback_data="cmd_qr")],
        [InlineKeyboardButton("💬 التواصل مع الدعم", callback_data="cmd_support")],
        [InlineKeyboardButton("📊 تحليل معاملة", callback_data="cmd_analyze")],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("📊 إحصائيات", callback_data="cmd_stats")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "👋 *اهلاً بك في بوت متابعة المعاملات*\n\n"
    msg += "يمكنك استخدام الأزرار أدناه للوصول إلى الخدمات بسهولة:\n"
    if is_admin:
        msg += "\n👑 *أنت مدير*\nيمكنك طلب أي شيء مثل: قائمة المعاملات، إحصائيات، تحليل معاملة، إلخ."
    else:
        msg += "\n📌 *ملاحظة:* للحصول على معلومات التتبع، يجب إدخال رقم المعاملة (ID) باستخدام الأمر `/id` أو عبر الأزرار."
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cmd_id":
        await query.edit_message_text(
            "📌 أرسل رقم المعاملة (ID) لمعرفة تفاصيلها.\n\n"
            "مثال: `/id MUT-20260324123456-1234`",
            parse_mode='Markdown'
        )
    elif data == "cmd_history":
        await query.edit_message_text(
            "📌 أرسل رقم المعاملة (ID) لمعرفة سجل تتبعها.\n\n"
            "مثال: `/history MUT-20260324123456-1234`",
            parse_mode='Markdown'
        )
    elif data == "cmd_qr":
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

        instruction_text = (
            "📱 *كيفية استخدام رمز QR لتتبع المعاملة*\n\n"
            "1️⃣ قم بطباعة رمز QR الموجود في صفحة المعاملة.\n"
            "2️⃣ الصق الورقة مع المعاملة في مكان واضح.\n"
            "3️⃣  سيتم تتبع المعاملة بنجاح ✅\n"
            "💡 *نصيحة:* احتفظ بالورقة في ملف المعاملة لتسهيل التتبع."
        )

        if transaction_id:
            verify_link = f"{Config.WEB_APP_URL}/verify-email?transaction_id={transaction_id}"
            qr_base64 = QRGenerator.generate_qr(verify_link)
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=base64.b64decode(qr_base64),
                caption=instruction_text + f"\n\n🔗 *رابط التحقق:*\n`{verify_link}`\n\nقم بمسح الرمز أو فتح الرابط للدخول إلى صفحة التعديل.",
                parse_mode='Markdown'
            )
            await query.message.delete()
        else:
            await query.edit_message_text(
                instruction_text + "\n\n📌 *لم يتم ربط حسابك بأي معاملة بعد.*\n\n"
                "لربط حسابك بمعاملة، استخدم الرابط التالي:\n"
                f"`https://t.me/{Config.BOT_USERNAME}?start=رقم_المعاملة`\n\n"
                "(استبدل `رقم_المعاملة` برقم المعاملة الخاص بك)",
                parse_mode='Markdown'
            )
    elif data == "cmd_support":
        await query.edit_message_text(
            "📨 لإرسال رسالة إلى فريق الدعم، استخدم الأمر `/support`.\n"
            "سنتواصل معك في أقرب وقت.",
            parse_mode='Markdown'
        )
    elif data == "cmd_analyze":
        await query.edit_message_text(
            "📊 أرسل رقم المعاملة (ID) لتحليلها.\n\n"
            "مثال: `/analyze MUT-20260324123456-1234`",
            parse_mode='Markdown'
        )
    elif data == "cmd_stats":
        user_id = update.effective_user.id
        if user_id != Config.ADMIN_CHAT_ID:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        if not sheets_client:
            await query.edit_message_text("⚠️ غير متصل بقاعدة البيانات.")
            return
        records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
        total = len(records)
        completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
        pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
        msg = f"📊 *إحصائيات*\nإجمالي المعاملات: {total}\nمكتملة: {completed}\nقيد المعالجة: {pending}"
        await query.edit_message_text(msg, parse_mode='Markdown')

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not sheets_client:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات حالياً.")
            return
        if context.args:
            transaction_id = context.args[0]
            logger.info(f"🔍 البحث عن ID: {transaction_id}")
            data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
            if data:
                msg = f"🔍 *تفاصيل المعاملة {transaction_id}:*\n"
                for key in ['اسم صاحب المعاملة الثلاثي', 'الحالة', 'الموظف المسؤول']:
                    if key in data and data[key]:
                        msg += f"• {key}: {data[key]}\n"
                msg += f"\n🔗 [رابط المتابعة]({Config.WEB_APP_URL}/view/{transaction_id})"
                await update.message.reply_text(msg, parse_mode='Markdown', disable_web_page_preview=True)
            else:
                await update.message.reply_text(f"❌ لا توجد معاملة بالرقم {transaction_id}")
        else:
            await update.message.reply_text("الرجاء إدخال رقم المعاملة: /id 123")
    except Exception as e:
        logger.error(f"❌ خطأ في get_id: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ.")

async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
            await update.message.reply_text("الرجاء إدخال رقم المعاملة: /history 123")
    except Exception as e:
        logger.error(f"خطأ في history: {e}")
        await update.message.reply_text("حدث خطأ.")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
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
                await update.message.reply_text(f"🔎 المعاملات التي تحتوي على '{keyword}':\n" + "\n".join(found[:10]))
            else:
                await update.message.reply_text("لا توجد نتائج.")
        else:
            await update.message.reply_text("الرجاء إدخال كلمة للبحث: /search كلمة")
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
        verify_link = f"{Config.WEB_APP_URL}/verify-email?transaction_id={transaction_id}"
        qr_base64 = QRGenerator.generate_qr(verify_link)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=base64.b64decode(qr_base64),
            caption=f"📱 *رمز QR للوصول إلى المعاملة*\n\n🆔 {transaction_id}\n\n1️⃣ امسح الرمز أو اضغط الرابط\n2️⃣ أدخل بريدك الجامعي (ينتهي بـ @it.jan.ah)\n3️⃣ سيتم توجيهك إلى صفحة التعديل.\n\n🔗 {verify_link}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "📌 *لم يتم ربط حسابك بأي معاملة بعد.*\n\n"
            "لربط حسابك بمعاملة، استخدم الرابط التالي:\n"
            f"`https://t.me/{Config.BOT_USERNAME}?start=رقم_المعاملة`\n\n"
            "(استبدل `رقم_المعاملة` برقم المعاملة الخاص بك)",
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
    try:
        if not sheets_client:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
            return
        if not context.args:
            await update.message.reply_text("الرجاء إدخال رقم المعاملة: /analyze MUT-123456")
            return
        transaction_id = context.args[0]
        data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
        if not data:
            await update.message.reply_text(f"❌ لا توجد معاملة بالرقم {transaction_id}")
            return
        transaction_data = data
        ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        history = []
        if ws:
            records = ws.get_all_records()
            history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
                       for r in records if str(r.get('ID')) == transaction_id]
            history.sort(key=lambda x: x['time'])
        await update.message.reply_text("🔍 جاري تحليل المعاملة...")
        analysis = await ai_assistant.analyze_transaction(transaction_data, history)
        await update.message.reply_text(analysis, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"خطأ في /analyze: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء التحليل.")

async def smart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    logger.info(f"🧠 معالجة رسالة عادية: {text}")

    if text.isdigit() or (text.startswith('MUT-') and len(text) > 10):
        context.args = [text]
        await get_id(update, context)
        return

    if text.startswith(('بحث', 'ابحث')):
        keyword = text.replace('بحث', '').replace('ابحث', '').strip()
        if keyword:
            context.args = [keyword]
            await search(update, context)
            return

    if text.startswith(('تاريخ', 'تتبع')):
        parts = text.split()
        if len(parts) > 1 and (parts[1].isdigit() or parts[1].startswith('MUT-')):
            context.args = [parts[1]]
            await get_history(update, context)
            return

    await ai_chat_handler(update, context)

async def ai_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)
    user_name = update.effective_user.first_name or ""

    if is_admin:
        msg_lower = user_message.lower()
        if any(word in msg_lower for word in ['جميع المعاملات', 'قائمة المعاملات', 'كل المعاملات', 'عرض الكل']):
            response = get_all_transactions_list()
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if any(word in msg_lower for word in ['إحصاء', 'إحصائيات', 'stats', 'احصائيات']):
            await stats(update, context)
            return
        if 'مكتملة' in msg_lower:
            response = get_transactions_by_status('مكتملة')
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'قيد المعالجة' in msg_lower:
            response = get_transactions_by_status('قيد المعالجة')
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'جديد' in msg_lower:
            response = get_transactions_by_status('جديد')
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'متأخرة' in msg_lower:
            response = get_transactions_by_status('متأخرة')
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'خطأ' in msg_lower or 'أخطاء' in msg_lower:
            response = get_transactions_with_errors()
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'تحليل' in msg_lower:
            match = re.search(r'MUT-\d{14}-\d{4}', user_message)
            if match:
                transaction_id = match.group()
                context.args = [transaction_id]
                await analyze(update, context)
                return
            else:
                await update.message.reply_text("الرجاء إدخال رقم المعاملة بشكل صحيح: /analyze MUT-123456...")
                return
        logger.info(f"🤖 استعلام ذكي من المدير {user_name}: {user_message[:50]}...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        response = await ai_assistant.get_response(user_message, user_id, user_name)
        await update.message.reply_text(response)
        return

    if not ai_assistant:
        await update.message.reply_text("عذراً، خدمة الذكاء الاصطناعي غير متاحة حالياً.")
        return
    logger.info(f"🤖 استعلام ذكي من {user_name}: {user_message[:50]}...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    response = await ai_assistant.get_response(user_message, user_id, user_name)
    await update.message.reply_text(response)

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
        bot_app.add_handler(CallbackQueryHandler(button_callback))
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_handler))
        logger.info("✅ تم بناء البوت وإضافة المعالجات")

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

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        if not name or not phone:
            return jsonify({'success': False, 'error': 'الاسم والهاتف مطلوبان'}), 400

        if not sheets_client:
            return jsonify({'success': False, 'error': 'النظام غير متصل بقاعدة البيانات'}), 500

        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            logger.error("❌ ورقة manager غير موجودة")
            return jsonify({'success': False, 'error': 'ورقة manager غير موجودة'}), 500

        now_id = datetime.now()
        date_str = now_id.strftime("%Y%m%d%H%M%S")
        random_part = random.randint(1000, 9999)
        transaction_id = f"MUT-{date_str}-{random_part}"

        headers = ws.row_values(1)
        new_row = [''] * len(headers)
        edit_link = f"{Config.WEB_APP_URL}/transaction/{transaction_id}"
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
                new_row[idx] = edit_link
        ws.append_row(new_row)
        logger.info(f"✅ تمت كتابة المعاملة {transaction_id} في ورقة manager (الصف الجديد)")

        all_rows = ws.get_all_values()
        logger.info(f"📊 عدد الصفوف بعد الإضافة: {len(all_rows)}")
        if all_rows:
            logger.info(f"📊 آخر صف مضاف: {all_rows[-1]}")

        # ورقة QR
        qr_ws = sheets_client.get_worksheet(Config.SHEET_QR)
        if qr_ws:
            verify_link = f"{Config.WEB_APP_URL}/verify-email?transaction_id={transaction_id}"
            qr_image_url = f"{Config.WEB_APP_URL}/qr_image/{transaction_id}"
            qr_ws.append_row([transaction_id, f'=IMAGE("{qr_image_url}")', f'=HYPERLINK("{verify_link}", "فتح صفحة التحقق")'])
            logger.info(f"✅ تمت كتابة المعاملة {transaction_id} في شيت QR")

        sheets_client.add_history_entry(transaction_id, "تم إنشاء المعاملة", "النظام (API)")

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
            'view_link': f"{Config.WEB_APP_URL}/view/{transaction_id}",
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
    headers = ws.row_values(1)
    return jsonify(headers)

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
        return jsonify({'success': False, 'message': 'غير متصل بـ Google Sheets'}), 500

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
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        for idx, header in enumerate(headers):
            value = old_data.get(header, '')
            if header in updates:
                value = updates[header]
            if header == 'آخر تعديل بواسطة':
                value = employee_name
            elif header == 'آخر تعديل بتاريخ':
                value = now_str
            elif header == 'عدد التعديلات':
                try:
                    current_count = int(old_data.get(header, 0))
                except:
                    current_count = 0
                value = current_count + 1
            new_row[idx] = value

        ws.append_row(new_row)

        changes = ', '.join(updates.keys())
        sheets_client.add_history_entry(id, f"تم تحديث الحقول: {changes}", employee_name)

        user_message = f"✏️ *معاملتك {id} تم تحديثها*\n\n"
        for key, new_value in updates.items():
            old_value = old_data.get(key, '')
            if key == 'الحالة' and new_value != old_value:
                user_message += f"📌 تم تغيير الحالة إلى *{new_value}*\n"
            elif key == 'المؤسسة التالية' and new_value != old_value:
                user_message += f"🏢 تم نقل المعاملة إلى مؤسسة *{new_value}*\n"
            elif key == 'الموظف المسؤول' and new_value != old_value:
                user_message += f"👤 تم تعيين *{new_value}* مسؤولاً عن المعاملة\n"
            elif key == 'التأخير':
                if new_value == 'نعم' and old_value != 'نعم':
                    user_message += f"⚠️ *المعاملة متأخرة!* يرجى المتابعة.\n"
                elif new_value == 'لا' and old_value != 'لا':
                    user_message += f"✅ تم حل التأخير\n"
            elif key == 'الأولوية' and new_value != old_value:
                user_message += f"⚡ الأولوية تغيرت إلى *{new_value}*\n"
            elif key == 'تاريخ التحويل' and new_value != old_value:
                user_message += f"📅 تم تحديث تاريخ التحويل إلى *{new_value}*\n"
            elif key == 'سبب التحويل' and new_value != old_value:
                user_message += f"📝 تم تحديث سبب التحويل\n"
            elif key == 'الموافق' and new_value != old_value:
                user_message += f"✅ تمت الموافقة من قبل *{new_value}*\n"
            elif key == 'ملاحظات إضافية' and new_value != old_value:
                user_message += f"💬 تم إضافة ملاحظات جديدة\n"
            elif key == 'آخر إجراء' and new_value != old_value:
                user_message += f"🔄 آخر إجراء: {new_value}\n"

        if user_message == f"✏️ *معاملتك {id} تم تحديثها*\n\n":
            user_message += "تم تحديث بيانات المعاملة.\n"
        user_message += f"\n🔍 لمتابعة كل التغييرات: `/history {id}`"

        if background_loop and bot_app:
            asyncio.run_coroutine_threadsafe(
                notify_user(id, user_message),
                background_loop
            )

        if updates.get('الحالة') == 'مكتملة':
            if hasattr(sheets_client, 'archive_transaction'):
                archive_success = sheets_client.archive_transaction(id)
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
        if not email.endswith('@it.jan.ah'):
            return f"🚫 غير مصرح: البريد الإلكتروني يجب أن ينتهي بـ @it.jan.ah", 403

        token = sheets_client.generate_access_token(transaction_id, email)
        if not token:
            return "حدث خطأ أثناء توليد رابط الدخول", 500

        edit_url = f"{Config.WEB_APP_URL}/transaction/{transaction_id}?token={token}"
        return redirect(edit_url)

    return '''
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>التحقق من البريد الإلكتروني</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #f5f0ff 0%, #f0f2f5 100%); margin: 0; padding: 20px; }
            .card { max-width: 400px; margin: 50px auto; background: white; border-radius: 32px; box-shadow: 0 20px 35px -10px rgba(0,0,0,0.1); overflow: hidden; }
            .header { background: #8b5cf6; color: white; padding: 30px; text-align: center; }
            .header h1 { margin: 0; font-size: 24px; }
            .content { padding: 30px; }
            input { width: 100%; padding: 12px 16px; margin: 8px 0; border: 1px solid #e5e7eb; border-radius: 16px; font-size: 16px; background: #f9fafb; }
            button { background: #8b5cf6; color: white; border: none; padding: 12px; font-size: 16px; border-radius: 40px; width: 100%; cursor: pointer; margin-top: 15px; }
            button:hover { background: #7c3aed; transform: translateY(-2px); }
            .info { background: #f3f4f6; border-radius: 20px; padding: 12px; margin-bottom: 20px; font-size: 13px; text-align: center; color: #4b5563; }
        </style>
    </head>
    <body>
        <div class="card">
            <div class="header">
                <h1>🔐 التحقق من البريد الإلكتروني</h1>
            </div>
            <div class="content">
                <div class="info">💡 أدخل بريدك الجامعي (@it.jan.ah) للوصول إلى صفحة تعديل المعاملة.</div>
                <form method="POST">
                    <input type="email" name="email" placeholder="example@it.jan.ah" required>
                    <button type="submit">تحقق</button>
                </form>
            </div>
        </div>
    </body>
    </html>
    '''

# ------------------ صفحة تعديل المعاملة ------------------
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
        .ios-card { background: rgba(255,255,255,0.95); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.3); border-radius: 24px; box-shadow: 0 8px 20px rgba(0,0,0,0.05); }
        .ios-input { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 16px; padding: 12px 16px; font-size: 16px; width: 100%; transition: all 0.2s; }
        .ios-input:focus { border-color: #007aff; outline: none; box-shadow: 0 0 0 3px rgba(0,122,255,0.1); }
        .ios-select { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 16px; padding: 12px 16px; font-size: 16px; width: 100%; }
        .label-ios { font-size: 13px; font-weight: 600; color: #6b7280; margin-bottom: 4px; display: block; text-transform: uppercase; letter-spacing: 0.5px; }
        .timeline-item { border-right: 2px solid #007aff; position: relative; padding-right: 20px; margin-bottom: 24px; }
        .timeline-dot { width: 12px; height: 12px; background: #007aff; border-radius: 50%; position: absolute; right: -7px; top: 5px; }
        .badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
        .badge-new { background: #e2e3e5; color: #383d41; }
        .badge-processing { background: #fff3cd; color: #856404; }
        .badge-completed { background: #d4edda; color: #155724; }
        .badge-delayed { background: #f8d7da; color: #721c24; }
    </style>
</head>
<body class="bg-gradient-to-b from-gray-50 to-gray-100 p-4">
    <div class="max-w-4xl mx-auto">
        <div class="ios-card rounded-2xl p-4 mb-4 shadow-sm">
            <h1 class="text-xl font-semibold">🔍 تتبع المعاملة <span id="transaction-id" class="text-blue-600"></span></h1>
        </div>

        <div class="ios-card rounded-2xl p-6 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">📋 <span>معلومات أساسية</span></h2>
            <div id="readonly-fields" class="grid grid-cols-1 md:grid-cols-2 gap-5"></div>
        </div>

        <div class="ios-card rounded-2xl p-6 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">✏️ <span>تحديث البيانات</span></h2>
            <form id="editForm" class="space-y-5">
                <div id="editable-fields" class="grid grid-cols-1 md:grid-cols-2 gap-5"></div>
                <button type="submit" class="w-full bg-blue-500 hover:bg-blue-600 text-white font-medium py-3 px-4 rounded-xl transition shadow-sm">💾 حفظ التغييرات</button>
            </form>
        </div>

        <div class="ios-card rounded-2xl p-6 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">📜 <span>سجل الحركات</span></h2>
            <div id="history-timeline" class="space-y-2"></div>
        </div>

        <div id="message" class="fixed bottom-4 left-1/2 transform -translate-x-1/2 bg-gray-800 text-white px-6 py-3 rounded-xl shadow-lg opacity-0 transition-opacity z-50"></div>
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

        Promise.all([
            fetch(`/api/transaction/${id}`).then(r => r.json()),
            fetch('/api/headers').then(r => r.json())
        ]).then(([data, headers]) => {
            const readonlyKeys = [
                'Timestamp', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف',
                'الوظيفة', 'القسم', 'نوع المعاملة', 'المرافقات', 'ID'
            ];
            const excludedKeys = ['LOG_JSON', 'الرابط', 'عدد التعديلات', 'البريد الإلكتروني الموظف'];

            const rc = document.getElementById('readonly-fields');
            rc.innerHTML = '';
            readonlyKeys.forEach(key => {
                if (data[key] !== undefined) {
                    const value = data[key] || '—';
                    let display = value;
                    if (key === 'المرافقات' && value.startsWith('http')) {
                        display = `<a href="${value}" target="_blank" class="text-blue-500 underline">📎 فتح المرفق</a>`;
                    }
                    rc.innerHTML += `
                        <div class="bg-gray-50/80 p-4 rounded-xl">
                            <span class="label-ios">${key}</span>
                            <div class="text-gray-900 mt-1 font-medium">${display}</div>
                        </div>
                    `;
                }
            });

            const editableKeys = headers.filter(key => 
                !readonlyKeys.includes(key) && !excludedKeys.includes(key)
            );
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
                        <select name="${key}" class="ios-select" onchange="updateStatusColor(this)">
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

                const currentValue = data[key] || '';
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
                            <input type="date" name="${key}" value="${currentValue.split('T')[0] || ''}" class="ios-input">
                        </div>
                    `;
                } else {
                    ec.innerHTML += `
                        <div>
                            <label class="label-ios">${key}</label>
                            <input type="text" name="${key}" value="${currentValue}" class="ios-input">
                        </div>
                    `;
                }
            });
        }).catch(() => {
            document.body.innerHTML = '<div class="text-center text-red-500 p-10">❌ المعاملة غير موجودة أو حدث خطأ في تحميل البيانات</div>';
        });

        function updateStatusColor(select) {
            select.classList.remove('badge-new', 'badge-processing', 'badge-completed', 'badge-delayed');
            if (select.value === 'جديد') select.classList.add('badge-new');
            else if (select.value === 'قيد المعالجة') select.classList.add('badge-processing');
            else if (select.value === 'مكتملة') select.classList.add('badge-completed');
            else if (select.value === 'متأخرة') select.classList.add('badge-delayed');
        }

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
                    t.innerHTML = '<p class="text-gray-500 text-center py-8">لا يوجد سجل</p>';
                    return;
                }
                let html = '';
                h.forEach(i => {
                    html += `
                        <div class="timeline-item">
                            <span class="timeline-dot"></span>
                            <div class="timeline-time">${i.time}</div>
                            <div class="timeline-action">${i.action}</div>
                            <div class="timeline-user">بواسطة: ${i.user}</div>
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

@app.route('/transaction/<id>')
def edit_transaction_page(id):
    token = request.args.get('token')
    if not token:
        # إعادة توجيه إلى صفحة التحقق من البريد
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
    <title>المعاملات - لوحة التحكم</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-6xl mx-auto">
        <h1 class="text-2xl font-bold mb-4">📋 جميع المعاملات (المدير)</h1>
        <div class="mb-4">
            <input type="text" id="searchInput" placeholder="🔍 ابحث بـ ID أو الاسم أو الحالة..." 
                   class="w-full p-3 border border-gray-300 rounded-xl text-right">
        </div>
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
    view_link = f"{Config.WEB_APP_URL}/view/{id}"
    qr_base64 = QRGenerator.generate_qr(view_link)
    html = f"""
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QR Code للمعاملة {id}</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; text-align: center; }}
            .card {{ max-width: 500px; margin: 50px auto; background: white; border-radius: 24px; box-shadow: 0 8px 20px rgba(0,0,0,0.1); padding: 30px; }}
            .qr {{ margin: 20px 0; }}
            .instruction {{ background: #f8f9fa; border-radius: 16px; padding: 15px; margin-top: 20px; text-align: right; }}
            .btn {{ display: inline-block; background: #2c3e50; color: white; padding: 12px 24px; text-decoration: none; border-radius: 40px; margin: 10px 5px; transition: 0.3s; }}
            .btn-telegram {{ background: #0088cc; }}
            .btn:hover {{ opacity: 0.9; transform: translateY(-2px); }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>📱 رمز QR للمعاملة</h2>
            <div class="qr">
                <img src="data:image/png;base64,{qr_base64}" alt="QR Code للمعاملة {id}" style="width: 200px; height: 200px;">
            </div>
            <div class="instruction">
                <p><strong>🔹 تعليمات التتبع:</strong></p>
                <p>1️⃣ افتح كاميرا هاتفك وامسح الرمز.</p>
                <p>2️⃣ سيتم نقلك إلى صفحة تفاصيل المعاملة.</p>
                <p>3️⃣ يمكنك متابعة المعاملة عبر البوت:</p>
                <a href="https://t.me/{Config.BOT_USERNAME}?start={id}" class="btn btn-telegram">📱 فتح البوت</a>
                <p style="margin-top: 15px; font-size: 12px; color: #6c757d;">⚠️ احتفظ بهذا الرقم لمتابعة المعاملة: <strong>{id}</strong></p>
            </div>
        </div>
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

# ------------------ صفحات الويب الأخرى ------------------
@app.route('/register', methods=['GET', 'POST'])
def register_transaction():
    if request.method == 'GET':
        return '''
        <!DOCTYPE html>
        <html dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>تسجيل معاملة جديدة</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #f5f0ff 0%, #f0f2f5 100%); margin: 0; padding: 20px; }
                .container { max-width: 700px; margin: 20px auto; background: white; border-radius: 32px; box-shadow: 0 20px 35px -10px rgba(0,0,0,0.1); overflow: hidden; }
                .header { background: #8b5cf6; color: white; padding: 30px; text-align: center; }
                .header h1 { margin: 0; font-size: 28px; }
                .header p { margin: 10px 0 0; opacity: 0.9; }
                .content { padding: 30px; }
                .form-group { margin-bottom: 20px; }
                label { display: block; margin-bottom: 8px; font-weight: 600; color: #1f2937; }
                input, select, textarea { width: 100%; padding: 12px 16px; border: 1px solid #e5e7eb; border-radius: 16px; font-size: 16px; transition: all 0.2s; background: #f9fafb; }
                input:focus, select:focus, textarea:focus { outline: none; border-color: #8b5cf6; box-shadow: 0 0 0 3px rgba(139,92,246,0.1); background: white; }
                button { background: #8b5cf6; color: white; border: none; padding: 14px 24px; font-size: 18px; font-weight: 600; border-radius: 40px; width: 100%; cursor: pointer; transition: 0.2s; margin-top: 10px; }
                button:hover { background: #7c3aed; transform: translateY(-2px); box-shadow: 0 8px 20px rgba(139,92,246,0.3); }
                .required:after { content: " *"; color: #ef4444; }
                .info-box { background: #f3f4f6; border-radius: 20px; padding: 15px; margin-bottom: 20px; font-size: 14px; color: #4b5563; text-align: center; }
                .result { margin-top: 20px; padding: 15px; border-radius: 20px; background: #f9fafb; display: none; }
                .result.success { background: #d1fae5; color: #065f46; display: block; }
                .result.error { background: #fee2e2; color: #991b1b; display: block; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📝 تسجيل معاملة جديدة</h1>
                    <p>املأ البيانات التالية لتسجيل معاملتك</p>
                </div>
                <div class="content">
                    <div class="info-box">
                        💡 بعد إرسال المعاملة، سيتم إنشاء رقم معاملة فريد وستحصل على رابط لمتابعة المعاملة عبر البوت.
                    </div>
                    <form id="transactionForm" enctype="multipart/form-data">
                        <div class="form-group">
                            <label class="required">الاسم الثلاثي</label>
                            <input type="text" id="name" name="name" required placeholder="مثال: أحمد محمد علي">
                        </div>
                        <div class="form-group">
                            <label class="required">رقم الهاتف</label>
                            <input type="text" id="phone" name="phone" required placeholder="07712345678">
                        </div>
                        <div class="form-group">
                            <label class="required">الوظيفة</label>
                            <select id="function" name="function" required>
                                <option value="طالب">طالب</option>
                                <option value="تدريسي">تدريسي</option>
                                <option value="أخرى">أخرى</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="required">القسم</label>
                            <select id="department" name="department" required>
                                <option value="قسم تكنولوجيا المعلومات و الإتصالات">قسم تكنولوجيا المعلومات و الإتصالات</option>
                                <option value="قسم التقنيات الكهربائية">قسم التقنيات الكهربائية</option>
                                <option value="قسم تقنيات المكائن والمعدات">قسم تقنيات المكائن والمعدات</option>
                                <option value="قسم التقنيات الميكانيكية">قسم التقنيات الميكانيكية</option>
                                <option value="قسم التقنيات الإلكترونية">قسم التقنيات الإلكترونية</option>
                                <option value="قسم تقنيات الصناعات الكيمياوية">قسم تقنيات الصناعات الكيمياوية</option>
                                <option value="قسم تقنيات المساحة">قسم تقنيات المساحة</option>
                                <option value="قسم تقنيات الموارد المائية">قسم تقنيات الموارد المائية</option>
                                <option value="قسم تقنيات الأجهزة الطبية">قسم تقنيات الأجهزة الطبية</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>نوع المعاملة</label>
                            <input type="text" id="transaction_type" name="transaction_type" placeholder="مثال: تتبع، استعلام، شكوى، اقتراح">
                        </div>
                        <div class="form-group">
                            <label>المرافقات (نص)</label>
                            <textarea id="attachments_text" name="attachments_text" rows="2" placeholder="أي ملاحظات إضافية..."></textarea>
                        </div>
                        <div class="form-group">
                            <label>رفع ملف (اختياري)</label>
                            <input type="file" id="attachment_file" name="attachment_file" accept="*/*">
                            <small style="color:#6c757d;">يمكنك رفع صورة، PDF، مستند... سيتم رفع الملف إلى Google Drive وسيظهر الرابط في المرافقات.</small>
                        </div>
                        <button type="submit" id="submitBtn">إرسال المعاملة</button>
                    </form>
                    <div id="result" class="result"></div>
                </div>
            </div>
            <script>
                document.getElementById('transactionForm').addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const submitBtn = document.getElementById('submitBtn');
                    const resultDiv = document.getElementById('result');
                    
                    submitBtn.disabled = true;
                    const originalText = submitBtn.textContent;
                    submitBtn.textContent = 'جاري الإرسال...';
                    resultDiv.innerHTML = '<div>جاري التسجيل...</div>';
                    resultDiv.className = 'result';

                    try {
                        const formData = new FormData(e.target);
                        const res = await fetch('/api/submit', {
                            method: 'POST',
                            body: formData
                        });
                        const json = await res.json();
                        if (json.success) {
                            resultDiv.innerHTML = `
                                <div style="text-align:center;">
                                    ✅ تم تسجيل المعاملة بنجاح<br>
                                    🆔  رقم المعاملة مهم لا تشاركه ابداً : <strong style="font-size:1.2em;">${json.id}</strong><br><br>
                                    <a href="${json.view_link}" target="_blank" style="background:#8b5cf6; color:white; padding:8px 16px; border-radius:40px; text-decoration:none; margin:5px; display:inline-block;">🔗 عرض التفاصيل</a>
                                    <a href="${json.deep_link}" target="_blank" style="background:#2c3e50; color:white; padding:8px 16px; border-radius:40px; text-decoration:none; margin:5px; display:inline-block;">📱 فتح البوت</a>
                                    <p style="margin-top:15px; font-size:13px;"> احتفظ برقم المعاملة لمتابعة معاملتك .</p>
                                </div>
                            `;
                            resultDiv.classList.add('success');
                        } else {
                            resultDiv.innerHTML = `❌ فشل التسجيل: ${json.error || 'خطأ غير معروف'}`;
                            resultDiv.classList.add('error');
                            submitBtn.disabled = false;
                            submitBtn.textContent = originalText;
                        }
                    } catch (err) {
                        resultDiv.innerHTML = '❌ خطأ في الاتصال بالخادم';
                        resultDiv.classList.add('error');
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalText;
                    }
                });
            </script>
        </body>
        </html>
        '''
    else:
        return "Use /api/submit", 405

@app.route('/verify', methods=['GET'])
def verify_page():
    name = request.args.get('name', '').strip()
    phone = request.args.get('phone', '').strip()

    if not name or not phone:
        return '''
        <!DOCTYPE html>
        <html dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>التحقق من المعاملة</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #f5f0ff 0%, #f0f2f5 100%); margin: 0; padding: 20px; }
                .card { max-width: 450px; margin: 50px auto; background: white; border-radius: 32px; box-shadow: 0 20px 35px -10px rgba(0,0,0,0.1); overflow: hidden; }
                .header { background: #8b5cf6; color: white; padding: 30px; text-align: center; }
                .header h1 { margin: 0; font-size: 28px; }
                .content { padding: 30px; }
                input { width: 100%; padding: 12px 16px; margin: 8px 0; border: 1px solid #e5e7eb; border-radius: 16px; font-size: 16px; background: #f9fafb; }
                button { background: #8b5cf6; color: white; border: none; padding: 14px; font-size: 18px; border-radius: 40px; width: 100%; cursor: pointer; margin-top: 15px; }
                button:hover { background: #7c3aed; transform: translateY(-2px); }
                .info { background: #f3f4f6; border-radius: 20px; padding: 12px; margin-bottom: 20px; font-size: 13px; text-align: center; color: #4b5563; }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="header">
                    <h1>🔍 التحقق من المعاملة</h1>
                </div>
                <div class="content">
                    <div class="info">💡 أدخل اسمك الثلاثي ورقم هاتفك كما في معاملتك</div>
                    <form method="GET">
                        <input type="text" name="name" placeholder="الاسم الثلاثي" required>
                        <input type="text" name="phone" placeholder="رقم الهاتف" required>
                        <button type="submit">تحقق</button>
                    </form>
                </div>
            </div>
        </body>
        </html>
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
        return f"""
        <!DOCTYPE html>
        <html dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>معاملتك</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #f5f0ff 0%, #f0f2f5 100%); margin: 0; padding: 20px; }}
                .card {{ max-width: 550px; margin: 50px auto; background: white; border-radius: 32px; box-shadow: 0 20px 35px -10px rgba(0,0,0,0.1); overflow: hidden; }}
                .header {{ background: #8b5cf6; color: white; padding: 30px; text-align: center; }}
                .id {{ font-size: 32px; font-weight: bold; color: #8b5cf6; background: #f5f0ff; display: inline-block; padding: 12px 28px; border-radius: 60px; margin: 20px 0; letter-spacing: 1px; }}
                .btn {{ display: inline-block; background: #8b5cf6; color: white; padding: 12px 28px; text-decoration: none; border-radius: 40px; margin: 10px; transition: 0.2s; }}
                .btn-telegram {{ background: #2c3e50; }}
                .btn:hover {{ transform: translateY(-2px); box-shadow: 0 5px 15px rgba(139,92,246,0.3); }}
                .content {{ padding: 30px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="header">
                    <h2>✅ تم العثور على معاملتك</h2>
                </div>
                <div class="content">
                    <p>رقم المعاملة الخاص بك:</p>
                    <div class="id">{transaction_id}</div>
                    <p> احتفظ بهذا الرقم لمتابعة المعاملة </p>
                    <a href="{Config.WEB_APP_URL}/view/{transaction_id}" target="_blank" class="btn">🔗 عرض التفاصيل</a>
                    <a href="https://t.me/{Config.BOT_USERNAME}?start={transaction_id}" target="_blank" class="btn btn-telegram">📱 فتح البوت</a>
                </div>
            </div>
        </body>
        </html>
        """
    else:
        return f"""
        <!DOCTYPE html>
        <html dir="rtl">
        <body style="text-align:center;margin-top:50px;">
            <h2>❌ لم نجد معاملة بهذه البيانات</h2>
            <p>الاسم المدخل: "{name}"</p>
            <p>رقم الهاتف المدخل: "{phone}"</p>
            <p><a href="/verify">🔍 محاولة مرة أخرى</a></p>
        </body>
        </html>
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
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>تفاصيل المعاملة {id}</title>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{ font-family: 'Inter', sans-serif; background: linear-gradient(135deg, #f9f5ff 0%, #f3e8ff 100%); padding: 24px; min-height: 100vh; }}
                .container {{ max-width: 1000px; margin: 0 auto; }}
                .card {{ background: white; border-radius: 32px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.1); overflow: hidden; margin-bottom: 24px; }}
                .card-header {{ background: #8b5cf6; padding: 28px 32px; color: white; }}
                .card-header h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
                .card-header p {{ opacity: 0.9; font-size: 14px; }}
                .card-content {{ padding: 32px; }}
                .info-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; margin-bottom: 32px; }}
                .info-item {{ background: #faf5ff; border-radius: 24px; padding: 20px; transition: all 0.2s; }}
                .info-label {{ font-size: 13px; font-weight: 600; color: #8b5cf6; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
                .info-value {{ font-size: 16px; font-weight: 500; color: #1f2937; word-break: break-word; }}
                .status-badge {{ display: inline-block; padding: 6px 14px; border-radius: 40px; font-size: 13px; font-weight: 600; }}
                .status-new {{ background: #e2e3e5; color: #383d41; }}
                .status-processing {{ background: #fff3cd; color: #856404; }}
                .status-completed {{ background: #d4edda; color: #155724; }}
                .status-delayed {{ background: #f8d7da; color: #721c24; }}
                .timeline {{ position: relative; padding-right: 30px; }}
                .timeline-item {{ position: relative; padding-bottom: 28px; border-right: 2px solid #e9d5ff; margin-right: 12px; }}
                .timeline-dot {{ position: absolute; right: -10px; top: 4px; width: 16px; height: 16px; background: #8b5cf6; border-radius: 50%; box-shadow: 0 0 0 4px #faf5ff; }}
                .timeline-time {{ font-size: 12px; color: #6c757d; margin-bottom: 4px; }}
                .timeline-action {{ font-weight: 600; color: #1f2937; margin-bottom: 4px; }}
                .timeline-user {{ font-size: 12px; color: #9ca3af; }}
                .instructions {{ background: #faf5ff; border-radius: 24px; padding: 20px; margin-top: 24px; text-align: center; }}
                .instructions p {{ margin: 8px 0; color: #4b5563; }}
                .btn {{ display: inline-block; background: #8b5cf6; color: white; padding: 10px 20px; border-radius: 40px; text-decoration: none; margin-top: 12px; transition: 0.2s; }}
                .btn:hover {{ background: #7c3aed; transform: translateY(-2px); }}
                hr {{ margin: 20px 0; border: none; height: 1px; background: #e9d5ff; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="card">
                    <div class="card-header">
                        <h1>🔍 تفاصيل المعاملة</h1>
                        <p>رقم المعاملة: <strong>{id}</strong> | للمتابعة فقط</p>
                    </div>
                    <div class="card-content">
                        <div class="info-grid">
        """
        excluded = ['ID', 'LOG_JSON', 'آخر تعديل بتاريخ', 'آخر تعديل بواسطة', 'الرابط', 'عدد التعديلات', 'البريد الإلكتروني الموظف']
        for key, value in data.items():
            if key not in excluded:
                display_value = value if value else '—'
                if key == 'المرافقات' and value and value.startswith('http'):
                    display_value = f'<a href="{value}" target="_blank" style="color:#8b5cf6; text-decoration:underline;">📎 فتح المرفق</a>'
                if key == 'الحالة':
                    badge_class = "status-new" if value == "جديد" else ("status-processing" if value == "قيد المعالجة" else ("status-completed" if value == "مكتملة" else ("status-delayed" if value == "متأخرة" else "")))
                    display_value = f'<span class="status-badge {badge_class}">{value if value else "—"}</span>'
                html += f"""
                            <div class="info-item">
                                <div class="info-label">{key}</div>
                                <div class="info-value">{display_value}</div>
                            </div>
                """
        html += """
                        </div>

                        <h3 style="font-size: 20px; font-weight: 600; margin-bottom: 20px; display: flex; align-items: center; gap: 8px;">📜 سجل الحركات</h3>
                        <div class="timeline">
        """
        if history:
            for entry in history:
                try:
                    dt = datetime.fromisoformat(entry['time'])
                    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    time_str = entry['time']
                html += f"""
                            <div class="timeline-item">
                                <div class="timeline-dot"></div>
                                <div class="timeline-time">{time_str}</div>
                                <div class="timeline-action">{entry['action']}</div>
                                <div class="timeline-user">بواسطة: {entry['user']}</div>
                            </div>
                """
        else:
            html += '<p style="color:#6c757d;">لا يوجد سجل بعد</p>'
        html += """
                        </div>

                        <div class="instructions">
                            <p>💡 يمكنك متابعة معاملتك عبر البوت:</p>
                            <a href="https://t.me/""" + Config.BOT_USERNAME + f"""?start={id}" class="btn">📱 فتح البوت لمتابعة المعاملة</a>
                            <hr>
                            <p style="font-size:13px;">⚠️ احتفظ برقم المعاملة هذا لمتابعة حالتك. يمكنك أيضاً مسح رمز QR الموجود في البوت.</p>
                        </div>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        return html
    except Exception as e:
        logger.error(f"🔥 خطأ في عرض المعاملة {id}: {e}", exc_info=True)
        return f"حدث خطأ أثناء تحميل الصفحة: {str(e)}", 500

# ------------------ معالجة المعاملات الجديدة ------------------
last_row_count = 0
executor = ThreadPoolExecutor(max_workers=10)

def process_new_transaction(ws, row_number, new_row, transaction_id):
    """معالجة معاملة واحدة في خيط منفصل"""
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

        view_link = f"{Config.WEB_APP_URL}/view/{transaction_id}"
        hyperlink_formula = f'=HYPERLINK("{view_link}", "عرض المعاملة")'
        try:
            headers = ws.row_values(1)
            link_col = headers.index('الرابط') + 1
            ws.update_cell(row_number, link_col, hyperlink_formula)
        except ValueError:
            ws.update_cell(row_number, 21, hyperlink_formula)

        customer_email = new_row.get('البريد الإلكتروني')
        customer_name = new_row.get('اسم صاحب المعاملة الثلاثي')
        if transaction_id and customer_email:
            qr_page_link = f"{Config.WEB_APP_URL}/qr/{transaction_id}"
            try:
                from email_service import EmailService
                success = EmailService.send_customer_email(
                    customer_email, customer_name, transaction_id, qr_page_link
                )
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
            history_ws.append_row([
                timestamp,
                transaction_id,
                "تم إنشاء المعاملة",
                "النظام (API)"
            ])
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
        records = ws.get_all_records()
        current_count = len(records)

        if current_count > last_row_count:
            logger.info(f"📦 تم اكتشاف {current_count - last_row_count} معاملات جديدة")
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

# ------------------ جدولة المهام ------------------
if sheets_client:
    try:
        # استخدم الدالة السريعة للحصول على العدد الفريد
        last_row_count = len(sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER))
    except Exception as e:
        logger.error(f"❌ فشل قراءة العدد الأولي: {e}")
        last_row_count = 0

    scheduler = BackgroundScheduler()
    scheduler.start()
    scheduler.add_job(
        func=check_new_transactions,
        trigger=IntervalTrigger(seconds=30),  # تقليل التكرار لتجنب 429
        id='check_transactions',
        replace_existing=True
    )
    logger.info("🔍 بدأت مراقبة المعاملات الجديدة (كل 30 ثانية)")
    atexit.register(lambda: scheduler.shutdown())
    atexit.register(lambda: executor.shutdown(wait=False))

# ------------------ تشغيل التطبيق ------------------
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)