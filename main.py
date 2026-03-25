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
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, render_template_string, Response, abort
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import atexit
import requests
from datetime import datetime

from sheets import GoogleSheetsClient
from config import Config
from qr_generator import QRGenerator
from ai_handler import AIAssistant

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
try:
    ai_assistant = AIAssistant(sheets_client=sheets_client)  # تمرير sheets_client للذكاء
    logger.info("✅ تم تهيئة Groq AI")
except Exception as e:
    logger.error(f"❌ فشل تهيئة Groq AI: {e}")
    ai_assistant = None

# ------------------ دوال مساعدة عامة ------------------
async def notify_user(transaction_id, message):
    """إرسال إشعار للمستخدم المرتبط بالمعاملة عبر البوت"""
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
    """حفظ chat_id في ورقة users"""
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
    """إرجاع قائمة منسقة بجميع المعاملات"""
    if not sheets_client:
        return "⚠️ النظام غير متصل بقاعدة البيانات."
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
    if not records:
        return "لا توجد معاملات حتى الآن."
    result = "📋 *قائمة جميع المعاملات:*\n"
    for r in records:
        result += f"• `{r.get('ID', '')}` - {r.get('اسم صاحب المعاملة الثلاثي', '')} - {r.get('الحالة', '')}\n"
    return result

def get_transactions_by_status(status):
    """إرجاع المعاملات حسب حالة محددة"""
    if not sheets_client:
        return "⚠️ النظام غير متصل بقاعدة البيانات."
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
    filtered = [r for r in records if r.get('الحالة') == status]
    if not filtered:
        return f"لا توجد معاملات بالحالة '{status}'."
    result = f"📋 *المعاملات بحالة {status}:*\n"
    for r in filtered:
        result += f"• `{r.get('ID', '')}` - {r.get('اسم صاحب المعاملة الثلاثي', '')}\n"
    return result

def get_transactions_with_errors():
    """إرجاع المعاملات التي تحتوي على أخطاء (الحالة 'خطأ' أو ملاحظات تحتوي 'خطأ')"""
    if not sheets_client:
        return "⚠️ النظام غير متصل بقاعدة البيانات."
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
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
            row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
            if row_info:
                save_user_chat(transaction_id, user_id)
                await update.message.reply_text(
                    f"✅ تم ربط حسابك بالمعاملة\n\n"
                    f"🆔 {transaction_id}\n"
                    f"يمكنك متابعة معاملتك من هنا."
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
    msg = "👋 *مرحباً بك في بوت متابعة المعاملات*\n\n"
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
        await query.edit_message_text(
            "📱 *كيفية استخدام رمز QR لتتبع المعاملة*\n\n"
            "1️⃣ قم بطباعة رمز QR الموجود في صفحة المعاملة.\n"
            "2️⃣ الصق الورقة مع المعاملة في مكان واضح.\n"
            "3️⃣ عند مسح الرمز، ستظهر صفحة التتبع.\n"
            "4️⃣ يمكن لأي شخص لديه الرابط متابعة المعاملة.\n\n"
            "💡 *نصيحة:* احتفظ بالورقة في ملف المعاملة لتسهيل التتبع.",
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
        records = sheets_client.get_all_records(Config.SHEET_MANAGER)
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
            row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
            if row_info:
                data = row_info['data']
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

async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "📱 *كيفية استخدام رمز QR لتتبع المعاملة*\n\n"
    msg += "1️⃣ قم بطباعة رمز QR الموجود في صفحة المعاملة.\n"
    msg += "2️⃣ الصق الورقة مع المعاملة في مكان واضح.\n"
    msg += "3️⃣ عند مسح الرمز، ستظهر صفحة التتبع.\n"
    msg += "4️⃣ يمكن لأي شخص لديه الرابط متابعة المعاملة.\n\n"
    msg += "🔗 رابط QR الخاص بمعاملتك: `/qr [رقم المعاملة]` (إذا كنت قد ربطت حسابك).\n\n"
    msg += "💡 *نصيحة:* احتفظ بالورقة في ملف المعاملة لتسهيل التتبع."
    await update.message.reply_text(msg, parse_mode='Markdown')

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
    """تحليل معاملة باستخدام الذكاء الاصطناعي"""
    try:
        if not sheets_client:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
            return
        if not context.args:
            await update.message.reply_text("الرجاء إدخال رقم المعاملة: /analyze MUT-123456")
            return
        transaction_id = context.args[0]
        row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
        if not row_info:
            await update.message.reply_text(f"❌ لا توجد معاملة بالرقم {transaction_id}")
            return
        transaction_data = row_info['data']

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

        timestamp = datetime.now().isoformat()

        if not name or not phone:
            return jsonify({'success': False, 'error': 'الاسم والهاتف مطلوبان'}), 400

        if not sheets_client:
            return jsonify({'success': False, 'error': 'النظام غير متصل بقاعدة البيانات'}), 500

        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return jsonify({'success': False, 'error': 'ورقة manager غير موجودة'}), 500

        now = datetime.now()
        date_str = now.strftime("%Y%m%d%H%M%S")
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
        logger.info(f"✅ تمت كتابة المعاملة {transaction_id} في ورقة manager")

        qr_ws = sheets_client.get_worksheet(Config.SHEET_QR)
        if qr_ws:
            view_link = f"{Config.WEB_APP_URL}/view/{transaction_id}"
            qr_page_link = f"{Config.WEB_APP_URL}/qr/{transaction_id}"
            qr_image_url = f"{Config.WEB_APP_URL}/qr_image/{transaction_id}"
            qr_ws.append_row([
                name,
                transaction_id,
                view_link,
                qr_image_url,
                qr_page_link,
                edit_link
            ])
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
        old_data = row_info['data']
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        headers = ws.row_values(1)

        for key, value in updates.items():
            if key in headers:
                col = headers.index(key) + 1
                ws.update_cell(row, col, value)

        employee_name = updates.get('الموظف المسؤول', old_data.get('الموظف المسؤول', 'غير معروف'))
        now = datetime.now().isoformat()

        try:
            col_last_modified_by = headers.index('آخر تعديل بواسطة') + 1
        except ValueError:
            col_last_modified_by = None
        try:
            col_last_modified_date = headers.index('آخر تعديل بتاريخ') + 1
        except ValueError:
            col_last_modified_date = None
        try:
            col_modification_count = headers.index('عدد التعديلات') + 1
        except ValueError:
            col_modification_count = None

        if col_last_modified_by:
            ws.update_cell(row, col_last_modified_by, employee_name)
        if col_last_modified_date:
            ws.update_cell(row, col_last_modified_date, now)
        if col_modification_count:
            try:
                current_count = int(ws.cell(row, col_modification_count).value or 0)
            except:
                current_count = 0
            ws.update_cell(row, col_modification_count, current_count + 1)

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

# ------------------ صفحات الويب ------------------
@app.route('/register', methods=['GET', 'POST'])
def register_transaction():
    if request.method == 'GET':
        return ''' <!DOCTYPE html> ... (كود HTML الكامل للصفحة) ... '''
    else:
        return "Use /api/submit", 405

@app.route('/verify', methods=['GET'])
def verify_page():
    # ... (كود صفحة التحقق الكامل) ...
    pass

@app.route('/view/<id>')
def view_transaction_page(id):
    # ... (كود صفحة عرض المعاملة) ...
    pass

@app.route('/transaction/<id>')
def edit_transaction_page(id):
    return render_template_string(EDIT_HTML)

@app.route('/')
def index():
    token = request.args.get('token')
    if not token or token != Config.ADMIN_SECRET:
        abort(403)
    return render_template_string(INDEX_HTML)

@app.route('/qr/<id>')
def qr_page(id):
    # ... (كود صفحة QR) ...
    pass

@app.route('/qr_image/<id>')
def qr_image(id):
    # ... (كود صورة QR) ...
    pass

# ------------------ معالجة المعاملات الجديدة ------------------
last_row_count = 0
executor = ThreadPoolExecutor(max_workers=10)

def process_new_transaction(ws, row_number, new_row, transaction_id):
    # ... (كما هو) ...
    pass

def check_new_transactions():
    # ... (كما هو) ...
    pass

# ------------------ جدولة المهام ------------------
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
    logger.info("🔍 بدأت مراقبة المعاملات الجديدة (كل 10 ثوانٍ)")
    atexit.register(lambda: scheduler.shutdown())
    atexit.register(lambda: executor.shutdown(wait=False))

# ------------------ تشغيل التطبيق ------------------
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)