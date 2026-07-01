#!/usr/bin/env python
# main.py - النظام المتكامل لإدارة المعاملات
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
from flask import Flask, request, jsonify, render_template_string, Response, abort, redirect, url_for
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from markupsafe import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import gspread
import jwt
import numpy as np
from cachetools import TTLCache

# استيراد الملفات الخاصة
from config import Config
from sheets import GoogleSheetsClient
from qr_generator import QRGenerator
from ai_handler import AIAssistant

# ================== إعدادات التسجيل ==================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ================== المتغيرات العامة ==================
MAX_WORKERS = 20
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# ================== تهيئة Flask ==================
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', secrets.token_hex(32))

# ================== إضافة Talisman (رؤوس أمان) ==================
Talisman(app, content_security_policy={
    'default-src': "'self'",
    'script-src': ["'self'", "'unsafe-inline'", "https://cdn.tailwindcss.com", "https://cdn.jsdelivr.net"],
    'style-src': ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
    'font-src': ["'self'", "https://fonts.gstatic.com"],
    'img-src': ["'self'", "data:"],
})

# ================== إضافة Limiter (تحديد معدل الطلبات) ==================
limiter = Limiter(app, key_func=get_remote_address, default_limits=["200 per day", "50 per hour"])
app.config['RATELIMIT_HEADERS_ENABLED'] = True

# ================== تهيئة Google Sheets ==================
sheets_client = None
try:
    sheets_client = GoogleSheetsClient()
    logger.info("✅ تم الاتصال بـ Google Sheets")
except Exception as e:
    logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
    sheets_client = None

# ================== تهيئة الذكاء الاصطناعي ==================
ai_assistant = None
try:
    ai_assistant = AIAssistant(sheets_client=sheets_client)
    logger.info("✅ تم تهيئة المساعد الذكي")
except Exception as e:
    logger.error(f"❌ فشل تهيئة AI: {e}")
    ai_assistant = None

# ================== دوال مساعدة ==================
async def notify_user(transaction_id, message):
    """إرسال إشعار للمستخدم عبر التليجرام"""
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
    """ربط حساب التليجرام بمعاملة"""
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

async def get_user_transaction_id(chat_id):
    """جلب رقم المعاملة المرتبط بمستخدم"""
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

# ================== دوال تنبيه المدير ==================
async def send_delayed_alert_to_admin():
    """إرسال تقرير المعاملات المتأخرة للمدير"""
    if not sheets_client or not bot_app or not background_loop:
        return
    try:
        delayed = sheets_client.get_delayed_transactions()
        if not delayed:
            return
        report = "🚨 *تقرير المعاملات المتأخرة*\n\n"
        report += f"📊 عدد المعاملات المتأخرة: {len(delayed)}\n\n"
        for idx, trans in enumerate(delayed[:10], 1):
            report += (
                f"*{idx}.* 🆔 `{trans.get('ID', 'غير معروف')}`\n"
                f"   👤 {trans.get('اسم صاحب المعاملة الثلاثي', 'غير معروف')}\n"
                f"   📂 {trans.get('القسم', 'غير معروف')}\n"
                f"   👨‍💼 المسؤول: {trans.get('الموظف المسؤول', 'غير معروف')}\n"
                f"   📅 آخر تعديل: {trans.get('آخر تعديل بتاريخ', 'غير معروف')}\n\n"
            )
        if len(delayed) > 10:
            report += f"\n... و {len(delayed) - 10} معاملات أخرى متأخرة."
        report += "\n💡 يُرجى التواصل مع المسؤولين لمعالجة هذه المعاملات."
        keyboard = [
            [InlineKeyboardButton("📋 عرض كل المتأخرة", callback_data="admin_view_delayed")],
            [InlineKeyboardButton("📊 عرض الإحصائيات", callback_data="cmd_advanced_stats")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await bot_app.bot.send_message(
            chat_id=Config.ADMIN_CHAT_ID,
            text=report,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        logger.info(f"✅ تم إرسال تنبيه المتأخرات للمدير ({len(delayed)} معاملة)")
    except Exception as e:
        logger.error(f"❌ فشل إرسال تنبيه المتأخرات: {e}")

async def notify_admin_unauthorized(email, action, details, ip_address=None, reason=None):
    """إشعار فوري للمدير عند محاولة غير مصرح بها"""
    if not bot_app or not background_loop or not Config.ADMIN_CHAT_ID:
        return
    try:
        warning_msg = (
            f"🚨 *تنبيه أمني عاجل!*\n\n"
            f"📌 تم رصد محاولة غير مصرح بها:\n"
            f"• البريد الإلكتروني: `{email if email else 'غير معروف'}`\n"
            f"• الإجراء: {action}\n"
            f"• التفاصيل: {details}\n"
            f"• الـ IP: {ip_address if ip_address else 'غير معروف'}\n"
            f"• السبب: {reason if reason else 'صلاحية غير كافية'}\n"
            f"• الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"⚠️ *يرجى التحقق فوراً من سجلات النشاط.*"
        )
        keyboard = [
            [InlineKeyboardButton("📋 عرض محاولات مشبوهة", callback_data="admin_view_unauthorized")],
            [InlineKeyboardButton("👥 إدارة الموظفين", callback_data="cmd_manage_employees")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await bot_app.bot.send_message(
            chat_id=Config.ADMIN_CHAT_ID,
            text=warning_msg,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        logger.warning(f"🔔 تم إرسال تنبيه أمني للمدير: {email} - {action}")
    except Exception as e:
        logger.error(f"فشل إرسال التنبيه الأمني: {e}")

async def auto_train_ai_model():
    """تدريب نموذج الذكاء الاصطناعي تلقائياً"""
    if not ai_assistant:
        return
    try:
        success = ai_assistant.train_model_from_feedback()
        if success:
            logger.info("✅ تم تدريب نموذج AI تلقائياً من بيانات التغذية الراجعة")
    except Exception as e:
        logger.error(f"❌ فشل التدريب التلقائي: {e}")

# ================== دوال البوت ==================
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

    user_keyboard = [
        [InlineKeyboardButton("🔍 تفاصيل معاملتي", callback_data="my_id")],
        [InlineKeyboardButton("📜 تتبع حالة معاملتي", callback_data="track_my")],
        [InlineKeyboardButton("📱 تعليمات QR", callback_data="cmd_qr")],
        [InlineKeyboardButton("📞 تواصل مع فريق العمل", callback_data="cmd_support")],
        [InlineKeyboardButton("🤖 أسأل المساعد", callback_data="cmd_ai_chat")],
    ]
    admin_keyboard = [
        [InlineKeyboardButton("🛡️ الإحصائيات الأمنية", callback_data="cmd_security_stats")],
        [InlineKeyboardButton("👥 إدارة الموظفين", callback_data="cmd_manage_employees")],
        [InlineKeyboardButton("🔍 بحث متقدم (مدير)", callback_data="cmd_admin_search")],
        [InlineKeyboardButton("📊 إحصائيات متقدمة", callback_data="cmd_advanced_stats")],
        [InlineKeyboardButton("📋 آخر 10 معاملات", callback_data="cmd_recent")],
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

async def track_transaction_status(update: Update, context: ContextTypes.DEFAULT_TYPE, transaction_id=None):
    """عرض سجل التتبع الكامل لمعاملة معينة"""
    if not sheets_client:
        await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
        return
    if not transaction_id:
        user_id = update.effective_user.id
        transaction_id = await get_user_transaction_id(user_id)
        if not transaction_id:
            await update.message.reply_text("⚠️ لم يتم ربط حسابك بأي معاملة. استخدم الأمر /start <ID> للربط.")
            return
    ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
    if not ws:
        await update.message.reply_text("❌ لا يوجد سجل تاريخ.")
        return
    records = ws.get_all_records()
    history = [r for r in records if str(r.get('ID')) == str(transaction_id)]
    data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
    if not data:
        await update.message.reply_text(f"❌ المعاملة {transaction_id} غير موجودة.")
        return
    msg = f"📊 *تتبع حالة المعاملة*\n"
    msg += f"🆔 `{transaction_id}`\n"
    msg += f"👤 {data.get('اسم صاحب المعاملة الثلاثي', 'غير معروف')}\n"
    msg += f"📂 {data.get('القسم', 'غير معروف')}\n"
    msg += f"📌 الحالة الحالية: *{data.get('الحالة', 'غير معروف')}*\n"
    msg += f"👨‍💼 المسؤول: {data.get('الموظف المسؤول', 'غير معروف')}\n"
    if data.get('التأخير') == 'نعم':
        msg += "⚠️ *هذه المعاملة متأخرة!*\n"
    msg += "\n📜 *سجل التتبع:*\n"
    if history:
        history.sort(key=lambda x: x.get('timestamp', ''))
        for entry in history:
            msg += f"• `{entry.get('timestamp', '')}` - {entry.get('action', '')} (بواسطة: {entry.get('user', '')})\n"
    else:
        msg += "❌ لا يوجد سجل تتبع لهذه المعاملة بعد."
    keyboard = [[InlineKeyboardButton("📞 تواصل مع فريق العمل", callback_data="cmd_support")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "مستخدم"
    username = update.effective_user.username or "لا يوجد"
    tid = await get_user_transaction_id(user_id)
    tid_text = f"🆔 {tid}" if tid else "❌ غير مرتبط بأي معاملة"
    if Config.ADMIN_CHAT_ID:
        await context.bot.send_message(
            chat_id=Config.ADMIN_CHAT_ID,
            text=(
                f"📩 *طلب دعم جديد*\n"
                f"👤 {user_name}\n"
                f"🆔 المستخدم: `{user_id}`\n"
                f"📛 المعرف: @{username}\n"
                f"📌 المعاملة: {tid_text}\n"
                f"📝 الرجاء الرد على المستخدم مباشرة عبر البوت."
            ),
            parse_mode='Markdown'
        )
    await update.message.reply_text(
        "📨 *تم إرسال طلبك إلى فريق الدعم بنجاح!*\n\n"
        "✅ سيقوم أحد المختصين بالتواصل معك خلال 24 ساعة.\n"
        "📌 يرجى الاحتفاظ برقم معاملتك لتسهيل المتابعة.\n\n"
        "شكراً لتواصلك معنا 🌟",
        parse_mode='Markdown'
    )

async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)
    transaction_id = None
    employee_email = None
    if sheets_client:
        try:
            ws = sheets_client.get_worksheet(Config.SHEET_USERS)
            if ws:
                records = ws.get_all_records()
                for row in records:
                    if str(row.get('chat_id')) == str(user_id):
                        transaction_id = row.get('transaction_id')
                        if transaction_id:
                            data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
                            if data:
                                employee_email = data.get('البريد الإلكتروني الموظف', '')
                        break
        except Exception as e:
            logger.error(f"خطأ في جلب معاملة المستخدم: {e}")
    if not transaction_id and not is_admin:
        await update.message.reply_text(
            "📌 *لم يتم ربط حسابك بأي معاملة بعد.*\n\n"
            "لربط حسابك بمعاملة، استخدم الرابط التالي:\n"
            f"`https://t.me/{Config.BOT_USERNAME}?start=رقم_المعاملة`\n\n"
            "🔒 *ملاحظة:* يجب أن تكون موظفاً معتمداً للتعامل مع QR.",
            parse_mode='Markdown'
        )
        return
    if not is_admin and transaction_id:
        if not employee_email:
            await update.message.reply_text(
                "⚠️ لم يتم تسجيل بريدك الإلكتروني كموظف معتمد.\n"
                "يرجى التواصل مع المدير لإضافة بريدك إلى قائمة الموظفين."
            )
            return
        if not sheets_client.is_qr_authorized(employee_email, required_role='viewer'):
            await update.message.reply_text(
                "🚫 ليس لديك صلاحية لعرض رمز QR.\n"
                "يرجى التواصل مع المدير لترقية صلاحياتك."
            )
            return
    expiry_days = 7 if is_admin else 1
    base_url = request.host_url.rstrip('/')
    if transaction_id:
        email_for_token = employee_email if employee_email else f"admin_{user_id}@system.com"
        token = sheets_client.generate_access_token(transaction_id, email_for_token, expiry_days=expiry_days)
        edit_link = f"{base_url}/transaction/{transaction_id}?token={token}"
        qr_base64 = QRGenerator.generate_qr(edit_link)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=base64.b64decode(qr_base64),
            caption=(
                f"📱 *رمز QR للوصول إلى المعاملة*\n\n"
                f"🆔 {transaction_id}\n"
                f"🔒 صلاحية الرابط: {expiry_days} يوم\n"
                f"👤 الموظف: {email_for_token}\n\n"
                f"⚠️ *تنبيه أمني:* لا تشارك هذا الرمز مع أي شخص غير مصرح له.\n"
                f"🔗 {edit_link}"
            ),
            parse_mode='Markdown'
        )
        if sheets_client:
            sheets_client.log_employee_activity(
                email=email_for_token,
                action='generate_qr',
                details=f'تم توليد QR للمعاملة {transaction_id}',
                success=True,
                ip_address=''
            )
    else:
        await update.message.reply_text("❌ لم أتمكن من العثور على معاملة مرتبطة بحسابك.")

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
            await track_transaction_status(update, context, text)
        elif awaiting == 'ai_chat':
            if ai_assistant:
                response = await ai_assistant.get_response(
                    user_message=text,
                    user_id=user_id,
                    user_name=update.effective_user.first_name or "مستخدم",
                    is_admin=is_admin
                )
                if "عطل تقني" in response or "تواصل مع فريق العمل" in response:
                    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📞 تواصل مع فريق العمل", callback_data="cmd_support")]])
                    await update.message.reply_text(response, parse_mode='Markdown', reply_markup=reply_markup)
                else:
                    await update.message.reply_text(response, parse_mode='Markdown')
            else:
                await update.message.reply_text("عذراً، المساعد الذكي غير متاح حالياً.")
        return
    
    # الأوامر النصية المختصرة
    cmd_map = {
        'qr': ['qr', 'رمز', 'QR'],
        'id': ['id', 'رقم المعاملة'],
        'history': ['history', 'سجل', 'تتبع'],
        'stats': ['stats', 'إحصائيات'],
        'support': ['support', 'دعم', 'مساعدة'],
        'start': ['start', 'الاوامر', 'help']
    }
    executed = False
    lower_text = text.lower()
    for cmd, keywords in cmd_map.items():
        if any(kw in lower_text for kw in keywords):
            if cmd == 'qr':
                await qr_command(update, context)
            elif cmd == 'id':
                context.user_data['awaiting'] = 'id'
                await update.message.reply_text("📌 أرسل رقم المعاملة (ID):", parse_mode='Markdown')
            elif cmd == 'history':
                context.user_data['awaiting'] = 'history'
                await update.message.reply_text("📌 أرسل رقم المعاملة (ID) لمعرفة سجل التتبع:", parse_mode='Markdown')
            elif cmd == 'stats':
                await stats(update, context)
            elif cmd == 'support':
                await support_command(update, context)
            elif cmd == 'start':
                await start(update, context)
            executed = True
            break
    if executed:
        return
    if ai_assistant:
        response = await ai_assistant.get_response(
            user_message=text,
            user_id=user_id,
            user_name=update.effective_user.first_name or "مستخدم",
            is_admin=is_admin
        )
        if "عطل تقني" in response or "تواصل مع فريق العمل" in response:
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("📞 تواصل مع فريق العمل", callback_data="cmd_support")]])
            await update.message.reply_text(response, parse_mode='Markdown', reply_markup=reply_markup)
        else:
            await update.message.reply_text(response, parse_mode='Markdown')
    else:
        await update.message.reply_text("عذراً، المساعد الذكي غير متاح حالياً.")

# ================== معالج الأزرار ==================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)

    if data == "my_id":
        tid = await get_user_transaction_id(user_id)
        if tid:
            context.args = [tid]
            await get_id(update, context)
        else:
            await query.edit_message_text("⚠️ لم يتم ربط حسابك بأي معاملة بعد.")
    elif data == "track_my":
        tid = await get_user_transaction_id(user_id)
        if tid:
            await track_transaction_status(update, context, tid)
            await query.message.delete()
        else:
            await query.edit_message_text("⚠️ لم يتم ربط حسابك بأي معاملة بعد.")
    elif data == "cmd_ai_chat":
        context.user_data['awaiting'] = 'ai_chat'
        await query.edit_message_text("🤖 *المساعد الذكي*\nأرسل سؤالك عن المعاملات، وسأجيب بذكاء.", parse_mode='Markdown')
    elif data == "cmd_qr":
        await qr_command(update, context)
        await query.message.delete()
    elif data == "cmd_support":
        await support_command(update, context)
        await query.message.delete()
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
    elif data == "cmd_recent":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        recent = sheets_client.get_recent_transactions(10)
        if not recent:
            await query.edit_message_text("لا توجد معاملات.")
            return
        msg = "📋 *آخر 10 معاملات*\n"
        for r in recent:
            msg += f"• `{r.get('ID', '')}` - {r.get('اسم صاحب المعاملة الثلاثي', '')} - {r.get('الحالة', '')}\n"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data == "cmd_admin_search":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        base_url = request.host_url.rstrip('/')
        search_url = f"{base_url}/admin/search?token={Config.ADMIN_SECRET}"
        await query.edit_message_text(
            f"🔍 *رابط البحث المتقدم للمدير:*\n\n"
            f"[اضغط هنا للدخول إلى لوحة البحث]({search_url})\n\n"
            f"🔒 *ملاحظة أمنية:* هذا الرابط يحتوي على مفتاح سري، لا تشاركه مع أي شخص.",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    elif data == "cmd_manage_employees":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        text = (
            "👥 *إدارة الموظفين المعتمدين*\n\n"
            "استخدم الأوامر التالية:\n"
            "• `/add_employee <بريد> <اسم> <دور>` - إضافة موظف\n"
            "• `/list_employees` - عرض جميع الموظفين\n"
            "• `/update_role <بريد> <دور>` - تحديث الدور\n"
            "• `/delete_employee <بريد>` - حذف موظف\n\n"
            "الأدوار المتاحة:\n"
            "• `admin` - صلاحية كاملة (مدير)\n"
            "• `qr_operator` - إنشاء وإدارة QR\n"
            "• `viewer` - عرض QR فقط"
        )
        await query.edit_message_text(text, parse_mode='Markdown')
    elif data == "cmd_security_stats":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        employees = sheets_client.get_all_employees()
        total_employees = len(employees)
        all_activities = sheets_client.get_employee_activity(limit=1000)
        total_activities = len(all_activities)
        failed_activities = sum(1 for a in all_activities if a.get('success') in ['0', 0])
        recent_failed = sheets_client.get_unauthorized_attempts(limit=5)
        msg = (
            "🛡️ *لوحة الأمان - إحصائيات عامة*\n\n"
            f"👥 عدد الموظفين المسجلين: {total_employees}\n"
            f"📊 إجمالي العمليات: {total_activities}\n"
            f"✅ العمليات الناجحة: {total_activities - failed_activities}\n"
            f"❌ العمليات الفاشلة: {failed_activities}\n"
            f"⚠️ المحاولات المشبوهة الأخيرة: {len(recent_failed)}\n\n"
            f"🔒 حالة النظام: {'🟢 آمن' if failed_activities < 10 else '🟡 يحتاج مراجعة'}\n"
            f"📅 آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        keyboard = [
            [InlineKeyboardButton("📋 عرض المحاولات المشبوهة", callback_data="admin_view_unauthorized")],
            [InlineKeyboardButton("👥 إدارة الموظفين", callback_data="cmd_manage_employees")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(msg, parse_mode='Markdown', reply_markup=reply_markup)
    elif data == "admin_view_unauthorized":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        attempts = sheets_client.get_unauthorized_attempts(limit=20)
        if not attempts:
            await query.edit_message_text("🛡️ لا توجد محاولات مشبوهة مسجلة.")
            return
        msg = "🚨 *المحاولات غير المصرح بها (الأخيرة)*\n\n"
        for att in attempts[:15]:
            timestamp = att.get('timestamp', '')[:16]
            email = att.get('email', 'غير معروف')
            action = att.get('action', '')
            details = att.get('details', '')
            ip = att.get('ip', '')
            msg += f"• {timestamp} | `{email}` | {action}\n"
            if details:
                msg += f"   📝 {details[:60]}\n"
            if ip:
                msg += f"   🌐 IP: {ip}\n"
        if len(attempts) > 15:
            msg += f"\n... و {len(attempts)-15} محاولات أخرى."
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data == "admin_view_delayed":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        delayed = sheets_client.get_delayed_transactions()
        if not delayed:
            await query.edit_message_text("✅ لا توجد معاملات متأخرة حالياً.")
            return
        msg = "🚨 *المعاملات المتأخرة*\n\n"
        for idx, trans in enumerate(delayed[:20], 1):
            msg += f"{idx}. 🆔 `{trans.get('ID')}` - {trans.get('اسم صاحب المعاملة الثلاثي')} - {trans.get('القسم')}\n"
        await query.edit_message_text(msg, parse_mode='Markdown')
    elif data.startswith("history_"):
        transaction_id = data.split("_", 1)[1]
        await track_transaction_status(update, context, transaction_id)
        await query.message.delete()
    elif data == "cmd_admin_manage":
        if not is_admin:
            await query.edit_message_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        admin_actions = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ تعيين مسؤول", callback_data="admin_assign")],
            [InlineKeyboardButton("🔄 تغيير حالة", callback_data="admin_status")],
            [InlineKeyboardButton("📊 تقرير كامل", callback_data="cmd_advanced_stats")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="start_back")]
        ])
        await query.edit_message_text("⚙️ *لوحة إدارة المعاملات*\nاختر إجراء:", parse_mode='Markdown', reply_markup=admin_actions)
    elif data == "admin_assign":
        context.user_data['awaiting'] = 'admin_assign'
        await query.edit_message_text("📌 أرسل: `assign MUT-xxxx اسم الموظف`", parse_mode='Markdown')
    elif data == "admin_status":
        context.user_data['awaiting'] = 'admin_status'
        await query.edit_message_text("📌 أرسل: `set_status MUT-xxxx الحالة`\nالحالات: جديد, قيد المعالجة, مكتملة, متأخرة", parse_mode='Markdown')
    elif data == "start_back":
        await start(update, context)
    else:
        await query.edit_message_text("⚠️ أمر غير معروف.")

# ================== إعداد البوت ==================
bot_app = None
background_loop = None
loop_thread = None

if Config.TELEGRAM_BOT_TOKEN:
    try:
        bot_app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start))
        bot_app.add_handler(CommandHandler("id", get_id))
        bot_app.add_handler(CommandHandler("stats", stats))
        bot_app.add_handler(CommandHandler("qr", qr_command))
        bot_app.add_handler(CommandHandler("support", support_command))
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

# ================== Webhook ==================
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

# ================== نقاط API ==================
@app.route('/api/submit', methods=['POST'])
@limiter.limit("5 per minute")
def api_submit():
    global sheets_client
    if sheets_client is None:
        try:
            sheets_client = GoogleSheetsClient()
            global ai_assistant
            if not ai_assistant and sheets_client:
                ai_assistant = AIAssistant(sheets_client=sheets_client)
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
        employee_email = request.form.get('employee_email', '').strip()
        client_ip = request.remote_addr
        
        if not name or not phone:
            return jsonify({'success': False, 'error': 'الاسم والهاتف مطلوبان'}), 400
        
        if employee_email:
            if not sheets_client.is_qr_authorized(employee_email, required_role='qr_operator'):
                sheets_client.log_employee_activity(
                    email=employee_email,
                    action='submit_transaction_failed',
                    details=f'محاولة إنشاء معاملة بدون صلاحية',
                    success=False,
                    ip_address=client_ip
                )
                asyncio.run_coroutine_threadsafe(
                    notify_admin_unauthorized(
                        email=employee_email,
                        action='محاولة إنشاء معاملة',
                        details=f'حاول إنشاء معاملة باسم {name} ولكن ليس لديه صلاحية',
                        ip_address=client_ip,
                        reason='صلاحية غير كافية'
                    ),
                    background_loop
                )
                return jsonify({'success': False, 'error': '🚫 غير مصرح لك بإنشاء معاملات جديدة. تواصل مع المدير.'}), 403
            sheets_client.log_employee_activity(
                email=employee_email,
                action='create_transaction',
                details=f'تم إنشاء معاملة باسم {name} (القسم: {department})',
                success=True,
                ip_address=client_ip
            )
        
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
                new_row[idx] = attachments_text
            elif header == 'ID':
                new_row[idx] = transaction_id
            elif header == 'الرابط':
                new_row[idx] = hyperlink_formula
            elif header == 'البريد الإلكتروني الموظف' and employee_email:
                new_row[idx] = employee_email
        sheets_client.safe_append_row(ws, new_row, batch=True)
        logger.info(f"✅ تم إنشاء المعاملة {transaction_id}")
        sheets_client.add_history_entry(transaction_id, "تم إنشاء المعاملة", "API")
        
        token = sheets_client.generate_access_token(transaction_id, employee_email or "user@system.com")
        edit_link_with_token = f"{base_url}/transaction/{transaction_id}?token={token}"
        return jsonify({
            'success': True,
            'id': transaction_id,
            'edit_link': edit_link_with_token,
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
@limiter.limit("10 per minute")
def api_transaction(id):
    if not sheets_client:
        return jsonify({'success': False, 'message': 'غير متصل'}), 500
    if request.method == 'GET':
        data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, id)
        if not data:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(data)
    
    # POST - تحديث المعاملة
    if not sheets_client.is_transaction_editable(id):
        return jsonify({
            'success': False,
            'message': 'هذه المعاملة مكتملة أو مؤرشفة، لا يمكن تعديلها.'
        }), 403
    
    if not hasattr(app, 'last_update_cache'):
        app.last_update_cache = {}
    updates = request.json
    cache_key = f"{id}_{hash(frozenset(updates.items()))}"
    last_time = app.last_update_cache.get(cache_key)
    if last_time and (datetime.now() - last_time).seconds < 3:
        logger.warning(f"Duplicate update attempt for {id}, ignored")
        return jsonify({'success': True, 'message': 'تم الحفظ مسبقاً (تم تجاهل التكرار)'})
    app.last_update_cache[cache_key] = datetime.now()
    for k in list(app.last_update_cache.keys()):
        if (datetime.now() - app.last_update_cache[k]).seconds > 60:
            del app.last_update_cache[k]
    
    old_data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, id)
    if not old_data:
        return jsonify({'success': False, 'message': 'المعاملة غير موجودة'}), 404
    
    ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
    headers = ws.row_values(1)
    new_row = [''] * len(headers)
    employee_name = updates.get('الموظف المسؤول', old_data.get('الموظف المسؤول', 'غير معروف'))
    now = datetime.now()
    
    # حساب التغييرات
    changes = []
    for key, new_value in updates.items():
        old_value = old_data.get(key, '')
        if str(new_value) != str(old_value):
            changes.append({
                'field': key,
                'old': old_value,
                'new': new_value
            })
    
    # تسجيل في سجل التدقيق
    for change in changes:
        sheets_client.log_audit_change(
            transaction_id=id,
            field_name=change['field'],
            old_value=change['old'],
            new_value=change['new'],
            changed_by=employee_name,
            ip_address=request.remote_addr
        )
    
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
    
    if changes:
        sheets_client.add_history_entry(id, f"تحديث: {', '.join([c['field'] for c in changes[:3]])}", employee_name)
    
    # إشعار للمدير
    if background_loop and bot_app and Config.ADMIN_CHAT_ID:
        if changes:
            change_text = "\n".join([f"   • {c['field']}: `{c['old']}` ➜ `{c['new']}`" for c in changes[:5]])
            if len(changes) > 5:
                change_text += f"\n   ... و {len(changes)-5} تغييرات أخرى"
            admin_msg = (
                f"🔔 *تحديث مباشر على معاملة*\n"
                f"🆔 {id}\n"
                f"👤 {old_data.get('اسم صاحب المعاملة الثلاثي', 'غير معروف')}\n"
                f"✏️ تم التعديل بواسطة: {employee_name}\n"
                f"📝 التغييرات:\n{change_text}"
            )
            asyncio.run_coroutine_threadsafe(
                bot_app.bot.send_message(
                    chat_id=Config.ADMIN_CHAT_ID,
                    text=admin_msg,
                    parse_mode='Markdown'
                ),
                background_loop
            )
    
    user_message = f"✏️ *معاملتك {id} تم تحديثها*\n\n" + "\n".join([f"• {c['field']}: {c['new']}" for c in changes[:5]])
    if background_loop and bot_app:
        asyncio.run_coroutine_threadsafe(notify_user(id, user_message), background_loop)
    
    # أرشفة تلقائية إذا أصبحت مكتملة
    if updates.get('الحالة') == 'مكتملة':
        sheets_client.archive_completed_transaction(id)
    
    return jsonify({'success': True, 'message': 'تم الحفظ بنجاح'})

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

# ================== صفحات الويب ==================
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
            return f"🚫 غير مصرح: البريد الإلكتروني {escape(email)} غير مسجل في النظام.", 403
        token = sheets_client.generate_access_token(transaction_id, email)
        if not token:
            return "حدث خطأ أثناء توليد رابط الدخول", 500
        base_url = request.host_url.rstrip('/')
        edit_url = f"{base_url}/transaction/{transaction_id}?token={token}"
        return redirect(edit_url)
    return render_template_string('''
    <!DOCTYPE html>
    <html dir="rtl"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>التحقق من البريد الإلكتروني</title>
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
    ''')

@app.route('/quick-transaction', methods=['GET', 'POST'])
def quick_transaction():
    if request.method == 'GET':
        employees = []
        if sheets_client:
            try:
                emp_ws = sheets_client.get_worksheet('employees')
                if emp_ws:
                    emp_records = emp_ws.get_all_records()
                    employees = [{'name': r.get('name', ''), 'email': r.get('email', ''), 'department': r.get('department', '')} 
                                for r in emp_records if r.get('name')]
            except:
                pass
        return render_template_string('''
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>إضافة معاملة سريعة</title>
        <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet">
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
        *{font-family:'Cairo',sans-serif;}body{background:linear-gradient(145deg,#0f0c29,#302b63,#24243e);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;margin:0;}
        .glass-card{background:rgba(255,255,255,0.95);backdrop-filter:blur(20px);border-radius:48px;padding:40px;max-width:600px;width:100%;box-shadow:0 30px 60px rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.2);transition:transform 0.3s ease;}
        .glass-card:hover{transform:translateY(-5px);}
        .input-group{margin-bottom:20px;}
        .input-group label{display:block;font-weight:600;margin-bottom:8px;color:#1f2937;font-size:0.95rem;}
        .input-group input,.input-group select,.input-group textarea{width:100%;padding:14px 18px;border:2px solid #e5e7eb;border-radius:20px;font-size:16px;transition:0.3s;background:#f9fafb;}
        .input-group input:focus,.input-group select:focus,.input-group textarea:focus{border-color:#8b5cf6;outline:none;box-shadow:0 0 0 4px rgba(139,92,246,0.15);background:white;}
        .btn-submit{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;border:none;padding:16px 32px;border-radius:40px;font-size:18px;font-weight:700;width:100%;cursor:pointer;transition:0.3s;box-shadow:0 10px 20px rgba(102,126,234,0.3);}
        .btn-submit:hover{transform:translateY(-3px);box-shadow:0 15px 30px rgba(102,126,234,0.5);}
        .btn-submit:disabled{opacity:0.6;cursor:not-allowed;transform:none;}
        .quick-employee{background:#f3f4f6;padding:10px 16px;border-radius:16px;cursor:pointer;transition:0.2s;margin:4px 0;border:1px solid transparent;}
        .quick-employee:hover{background:#e5e7eb;transform:translateX(-6px);border-color:#8b5cf6;}
        .employee-list{max-height:150px;overflow-y:auto;margin-bottom:16px;}
        .result-box{padding:16px 20px;border-radius:20px;margin-top:16px;display:none;font-weight:600;}
        .result-box.success{background:#d1fae5;color:#065f46;display:block;border-right:4px solid #059669;}
        .result-box.error{background:#fee2e2;color:#991b1b;display:block;border-right:4px solid #dc2626;}
        .title-gradient{background:linear-gradient(135deg,#667eea,#764ba2);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
        @media(max-width:640px){.glass-card{padding:24px;border-radius:32px;}}
        </style>
        </head>
        <body>
        <div class="glass-card">
        <div class="text-center mb-6"><h1 class="text-3xl font-extrabold title-gradient">📝 معاملة سريعة</h1><p class="text-gray-500 text-sm mt-1">أدخل بياناتك وسيتم إنشاء المعاملة فوراً</p></div>
        <div class="mb-4"><label class="text-sm font-semibold text-gray-600 block mb-2">👥 اختر من الموظفين المسجلين</label>
        <div class="employee-list" id="employeeList">
        {% for emp in employees %}
        <div class="quick-employee" onclick="fillEmployee('{{ emp.name }}','{{ emp.department }}','{{ emp.email }}')">
        <strong>{{ emp.name }}</strong> <span class="text-gray-500 text-sm">- {{ emp.department }}</span>
        </div>
        {% endfor %}
        </div></div>
        <form id="quickForm" onsubmit="submitTransaction(event)">
        <div class="input-group"><label>👤 الاسم الثلاثي *</label><input type="text" id="name" name="name" required placeholder="أدخل اسمك الثلاثي"></div>
        <div class="input-group"><label>📞 رقم الهاتف *</label><input type="text" id="phone" name="phone" required placeholder="أدخل رقم هاتفك"></div>
        <div class="input-group"><label>🏢 القسم *</label><select id="department" name="department" required>
        <option value="">اختر القسم</option>
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
        <div class="input-group"><label>💼 الوظيفة</label><select id="function" name="function"><option value="طالب">طالب</option><option value="تدريسي">تدريسي</option><option value="موظف">موظف</option><option value="أخرى">أخرى</option></select></div>
        <div class="input-group"><label>📌 نوع المعاملة</label><input type="text" id="transaction_type" name="transaction_type" placeholder="مثال: تتبع، استعلام، شكوى"></div>
        <div class="input-group"><label>📝 ملاحظات إضافية</label><textarea id="notes" name="attachments_text" rows="2" placeholder="أي ملاحظات..." class="w-full p-3 border rounded-xl"></textarea></div>
        <button type="submit" id="submitBtn" class="btn-submit">🚀 إرسال المعاملة</button>
        </form>
        <div id="result" class="result-box"></div>
        <p class="text-xs text-gray-400 text-center mt-4">🔒 سيتم إرسال رابط المعاملة إلى بريدك الإلكتروني (اختياري)</p>
        </div>
        <script>
        function fillEmployee(name,department,email){document.getElementById('name').value=name;document.getElementById('department').value=department;}
        async function submitTransaction(e){e.preventDefault();const btn=document.getElementById('submitBtn');const resultDiv=document.getElementById('result');btn.disabled=true;btn.textContent='⏳ جاري الإرسال...';resultDiv.className='result-box';resultDiv.textContent='';const formData=new FormData(e.target);try{const res=await fetch('/api/submit',{method:'POST',body:formData});const json=await res.json();if(json.success){resultDiv.className='result-box success';resultDiv.innerHTML=`✅ تم إنشاء المعاملة بنجاح!<br>🆔 <strong>${json.id}</strong><br><a href="${json.edit_link}" target="_blank" class="text-purple-600 underline font-bold">🔗 عرض التفاصيل</a> &nbsp;|&nbsp; <a href="${json.deep_link}" target="_blank" class="text-blue-600 underline font-bold">📱 فتح البوت</a>`;}else{resultDiv.className='result-box error';resultDiv.textContent='❌ فشل الإرسال: '+(json.error||'خطأ غير معروف');}}catch(err){resultDiv.className='result-box error';resultDiv.textContent='❌ خطأ في الاتصال بالخادم';}finally{btn.disabled=false;btn.textContent='🚀 إرسال المعاملة';}}
        </script>
        </body></html>
        ''', employees=employees)

@app.route('/view/<id>')
def view_transaction_page(id):
    try:
        if not sheets_client:
            return "⚠️ النظام غير متصل بقاعدة البيانات", 500
        data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, id)
        if not data:
            return f"❌ المعاملة {id} غير موجودة", 404
        is_admin = request.args.get('admin') == 'true'
        history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        history = []
        if history_ws:
            records = history_ws.get_all_records()
            history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
                       for r in records if str(r.get('ID')) == id]
            history.sort(key=lambda x: x['time'], reverse=False)
        excluded_fields = ['ID', 'LOG_JSON', 'آخر تعديل بتاريخ', 'آخر تعديل بواسطة', 'الرابط', 'عدد التعديلات', 'البريد الإلكتروني الموظف']
        if not is_admin:
            excluded_fields.append('التأخير')
        return render_template_string('''
        <!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>تفاصيل المعاملة {{ id }}</title>
        <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet"><script src="https://cdn.tailwindcss.com"></script>
        <style>*{font-family:'Cairo',sans-serif;}body{background:linear-gradient(145deg,#f0f4ff 0%,#e8edf5 100%);min-height:100vh;padding:24px;}.container{max-width:1100px;margin:0 auto;}.card{background:white;border-radius:32px;box-shadow:0 20px 40px rgba(0,0,0,0.06);overflow:hidden;margin-bottom:24px;}.card-header{background:linear-gradient(135deg,#667eea,#764ba2);padding:28px 32px;color:white;}.card-header h1{font-size:28px;font-weight:800;margin:0;}.card-body{padding:32px;}.info-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;}.info-item{background:#faf5ff;border-radius:20px;padding:18px 22px;border:1px solid #f3e8ff;transition:0.2s;}.info-item:hover{transform:translateY(-3px);box-shadow:0 6px 12px rgba(0,0,0,0.05);}.info-label{font-size:13px;font-weight:700;color:#8b5cf6;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;}.info-value{font-size:16px;font-weight:600;color:#1f2937;word-break:break-word;}.status-badge{display:inline-block;padding:6px 16px;border-radius:40px;font-size:14px;font-weight:700;}.status-new{background:#e5e7eb;color:#1f2937;}.status-processing{background:#fef3c7;color:#b45309;}.status-completed{background:#d1fae5;color:#065f46;}.status-delayed{background:#fee2e2;color:#991b1b;}.timeline{position:relative;padding-right:30px;}.timeline-item{position:relative;padding-bottom:28px;border-right:2px solid #e5e7eb;margin-right:12px;}.timeline-item:last-child{border-right:none;}.timeline-dot{position:absolute;right:-9px;top:5px;width:14px;height:14px;background:#8b5cf6;border-radius:50%;box-shadow:0 0 0 4px rgba(139,92,246,0.2);}.timeline-time{font-size:12px;color:#6c757d;margin-bottom:4px;direction:ltr;text-align:right;}.timeline-action{font-weight:700;color:#1f2937;margin-bottom:4px;}.timeline-user{font-size:12px;color:#9ca3af;}.instructions{background:#faf5ff;border-radius:24px;padding:24px;text-align:center;margin-top:24px;border:1px solid #f3e8ff;}.btn{display:inline-block;background:#8b5cf6;color:white;padding:12px 28px;border-radius:40px;text-decoration:none;font-weight:600;transition:0.3s;}.btn:hover{transform:translateY(-2px);box-shadow:0 8px 16px rgba(139,92,246,0.3);}.btn-telegram{background:#2c3e50;}.btn-telegram:hover{background:#1a2632;}@media(max-width:640px){.card-header{padding:20px;}.card-body{padding:20px;}.info-grid{grid-template-columns:1fr;}}
        </style>
        </head><body><div class="container"><div class="card"><div class="card-header"><h1>🔍 تفاصيل المعاملة</h1><p class="opacity-90">رقم المعاملة: <strong>{{ id }}</strong> | للمتابعة فقط</p></div><div class="card-body"><div class="info-grid">
        {% for key, value in data.items() %}{% if key not in excluded_fields %}<div class="info-item"><div class="info-label">{{ key }}</div><div class="info-value">{% if key == 'الحالة' %}{% set status = value %}<span class="status-badge {% if status == 'جديد' %}status-new{% elif status == 'قيد المعالجة' %}status-processing{% elif status == 'مكتملة' %}status-completed{% elif status == 'متأخرة' %}status-delayed{% endif %}">{{ status if status else '—' }}</span>{% elif key == 'المرافقات' and value and value.startswith('http') %}<a href="{{ value }}" target="_blank" class="text-purple-600 underline">📎 فتح المرفق</a>{% else %}{{ value if value else '—' }}{% endif %}</div></div>{% endif %}{% endfor %}
        </div><h3 class="text-xl font-bold mt-8 mb-4 text-purple-700">📜 سجل الحركات</h3><div class="timeline">{% if history %}{% for entry in history %}<div class="timeline-item"><div class="timeline-dot"></div><div class="timeline-time">{{ entry.time }}</div><div class="timeline-action">{{ entry.action }}</div><div class="timeline-user">بواسطة: {{ entry.user }}</div></div>{% endfor %}{% else %}<p class="text-gray-500">لا يوجد سجل بعد</p>{% endif %}</div><div class="instructions"><p class="text-gray-700">💡 يمكنك متابعة معاملتك عبر البوت:</p><a href="https://t.me/{{ bot_username }}?start={{ id }}" target="_blank" class="btn btn-telegram">📱 فتح البوت</a><hr class="my-4 border-gray-200"><p class="text-xs text-gray-400">⚠️ احتفظ برقم المعاملة هذا لمتابعة حالتك.</p></div></div></div></div></body></html>
        ''', id=id, data=data, history=history, excluded_fields=excluded_fields, bot_username=Config.BOT_USERNAME)
    except Exception as e:
        logger.error(f"🔥 خطأ في عرض المعاملة {id}: {e}", exc_info=True)
        return f"حدث خطأ أثناء تحميل الصفحة: {str(e)}", 500

# ================== صفحة تعديل المعاملة ==================
@app.route('/transaction/<id>')
def edit_transaction_page(id):
    token = request.args.get('token')
    if not token:
        return redirect(url_for('verify_email_page', transaction_id=id))
    if not sheets_client or not sheets_client.verify_access_token(token, id):
        abort(403, description="رمز الوصول غير صالح أو منتهي الصلاحية.")
    data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, id)
    if not data:
        abort(404, description="المعاملة غير موجودة")
    if not sheets_client.is_transaction_editable(id):
        return render_template_string('''
        <!DOCTYPE html><html dir="rtl"><head><meta charset="UTF-8"><title>المعاملة غير قابلة للتعديل</title>
        <style>body{font-family:sans-serif;text-align:center;padding:50px;background:#f8f9fa;}</style>
        </head><body><h2 style="color:#dc2626;">🔒 هذه المعاملة مكتملة أو مؤرشفة</h2><p>لا يمكن تعديل المعاملات المكتملة أو المؤرشفة.</p><a href="/view/{{ id }}" class="btn">← عرض التفاصيل</a></body></html>
        ''', id=id)
    safe_data = {k: escape(str(v)) if v else '' for k, v in data.items()}
    excluded_keys = ['ID', 'Timestamp', 'الرابط', 'عدد التعديلات', 'التأخير']
    return render_template_string('''
    <!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>تعديل المعاملة</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet"><script src="https://cdn.tailwindcss.com"></script>
    <style>*{font-family:'Cairo',sans-serif;}body{background:linear-gradient(145deg,#f0f4ff 0%,#e8edf5 100%);min-height:100vh;padding:24px;}.container{max-width:1100px;margin:0 auto;}.glass-card{background:rgba(255,255,255,0.95);backdrop-filter:blur(10px);border-radius:32px;padding:28px;box-shadow:0 20px 40px rgba(0,0,0,0.06);border:1px solid rgba(255,255,255,0.5);transition:0.3s;margin-bottom:24px;}.status-badge{display:inline-block;padding:6px 16px;border-radius:40px;font-size:13px;font-weight:700;}.status-new{background:#e5e7eb;color:#1f2937;}.status-processing{background:#fef3c7;color:#b45309;}.status-completed{background:#d1fae5;color:#065f46;}.status-delayed{background:#fee2e2;color:#991b1b;}.info-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;}.info-card{background:#faf5ff;border-radius:20px;padding:18px 22px;border:1px solid #f3e8ff;transition:0.2s;}.info-card:hover{transform:translateY(-3px);box-shadow:0 6px 12px rgba(0,0,0,0.05);}.label{font-size:12px;font-weight:700;color:#8b5cf6;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;display:block;}.value{font-size:16px;font-weight:600;color:#1f2937;word-break:break-word;}input,select,textarea{background:#f9fafb;border:2px solid #e5e7eb;border-radius:16px;padding:12px 16px;font-size:15px;width:100%;transition:0.3s;font-family:'Cairo',sans-serif;}input:focus,select:focus,textarea:focus{border-color:#8b5cf6;outline:none;box-shadow:0 0 0 4px rgba(139,92,246,0.1);background:white;}.btn-save{background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;padding:16px 32px;border-radius:40px;font-size:18px;font-weight:700;width:100%;cursor:pointer;transition:0.3s;box-shadow:0 10px 20px rgba(102,126,234,0.3);}.btn-save:hover:not(:disabled){transform:translateY(-3px);box-shadow:0 15px 30px rgba(102,126,234,0.5);}.btn-save:disabled{opacity:0.6;cursor:not-allowed;transform:none;}.timeline{position:relative;padding-right:30px;}.timeline-item{position:relative;padding-bottom:28px;border-right:2px solid #e5e7eb;margin-right:12px;}.timeline-item:last-child{border-right:none;}.timeline-dot{position:absolute;right:-9px;top:5px;width:14px;height:14px;background:#8b5cf6;border-radius:50%;box-shadow:0 0 0 4px rgba(139,92,246,0.2);}.timeline-time{font-size:12px;color:#6c757d;margin-bottom:4px;direction:ltr;text-align:right;}.timeline-action{font-weight:700;color:#1f2937;margin-bottom:4px;}.timeline-user{font-size:12px;color:#9ca3af;}.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1f2937;color:white;padding:12px 24px;border-radius:40px;font-size:14px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);opacity:0;transition:opacity 0.2s;pointer-events:none;z-index:50;}.btn-compare{background:#6366f1;color:white;padding:8px 20px;border-radius:40px;text-decoration:none;font-size:14px;font-weight:600;transition:0.3s;display:inline-block;}.btn-compare:hover{transform:translateY(-2px);box-shadow:0 6px 12px rgba(99,102,241,0.3);}@media(max-width:640px){.info-grid{grid-template-columns:1fr;}.glass-card{padding:20px;}}
    </style>
    </head><body><div class="container"><div class="glass-card text-center md:text-right"><h1 class="text-2xl font-extrabold bg-gradient-to-r from-purple-600 to-blue-600 bg-clip-text text-transparent">📝 تعديل المعاملة</h1><p class="text-gray-500 text-sm mt-1">رقم المعاملة: <span id="transaction-id" class="font-mono text-purple-600">{{ id }}</span></p></div>
    <div class="glass-card"><h2 class="text-lg font-bold flex items-center gap-2 mb-5 text-purple-700">📋 <span>معلومات أساسية</span></h2><div id="readonly-fields" class="info-grid"></div></div>
    <div class="glass-card"><h2 class="text-lg font-bold flex items-center gap-2 mb-5 text-purple-700">✏️ <span>تحديث البيانات</span></h2><form id="editForm" class="space-y-5"><div id="editable-fields" class="info-grid"></div><button type="submit" id="saveBtn" class="btn-save">💾 حفظ التغييرات</button></form></div>
    <div class="glass-card"><div class="flex justify-between items-center mb-5"><h2 class="text-lg font-bold flex items-center gap-2 text-purple-700">📜 <span>سجل الحركات</span></h2><div class="flex gap-2 flex-wrap"><a href="/transaction/{{ id }}/compare?token={{ request.args.get('token') }}" target="_blank" class="btn-compare">📊 مقارنة الإصدارات</a><button onclick="refreshHistory()" class="bg-purple-100 text-purple-700 px-4 py-2 rounded-full text-sm font-medium hover:bg-purple-200 transition">🔄 تحديث</button></div></div><div id="history-timeline" class="timeline"></div></div></div>
    <div id="message" class="toast"></div>
    <script>
    const id = window.location.pathname.split('/').pop();
    const token = new URLSearchParams(window.location.search).get('token');
    document.getElementById('transaction-id').innerText = id;
    let isSubmitting=false;
    function showMessage(text,isError=false){const m=document.getElementById('message');m.innerText=text;m.style.background=isError?'#dc2626':'#1f2937';m.style.opacity='1';setTimeout(()=>m.style.opacity='0',3000);}
    function formatDateTime(d){if(!d)return '—';try{let t=new Date(d);if(isNaN(t.getTime()))t=new Date(d.replace(' ','T'));if(isNaN(t.getTime()))return d;return t.toLocaleString('en-GB',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}).replace(',','');}catch(e){return d;}}
    Promise.all([fetch(`/api/transaction/${id}`).then(r=>r.json()),fetch('/api/headers').then(r=>r.json())]).then(([data,headers])=>{
    const readonlyKeys=['Timestamp','اسم صاحب المعاملة الثلاثي','رقم الهاتف','الوظيفة','القسم','نوع المعاملة','المرافقات','ID'];
    const excludedKeys=['LOG_JSON','الرابط','عدد التعديلات','البريد الإلكتروني الموظف','التأخير'];
    const rc=document.getElementById('readonly-fields');rc.innerHTML='';
    readonlyKeys.forEach(key=>{if(data[key]!==undefined){let value=data[key]||'—';let display=value;if(key==='المرافقات'&&value.startsWith('http')){display=`<a href="${value}" target="_blank" class="text-blue-600 underline">📎 فتح المرفق</a>`;}else if(key==='Timestamp'){display=formatDateTime(value);}rc.innerHTML+=`<div class="info-card"><div class="label">${key}</div><div class="value">${display}</div></div>`;}});
    const editableKeys=headers.filter(key=>!readonlyKeys.includes(key)&&!excludedKeys.includes(key));
    const ec=document.getElementById('editable-fields');ec.innerHTML='';
    editableKeys.forEach(key=>{let inputType='text';let options='';if(key.includes('تاريخ')){inputType='date';}else if(key==='الحالة'){inputType='select';options=`<select name="${key}" class="w-full p-3 border rounded-xl bg-gray-50 focus:border-purple-500"><option value="جديد" ${data[key]==='جديد'?'selected':''}>جديد</option><option value="قيد المعالجة" ${data[key]==='قيد المعالجة'?'selected':''}>قيد المعالجة</option><option value="مكتملة" ${data[key]==='مكتملة'?'selected':''}>مكتملة</option><option value="متأخرة" ${data[key]==='متأخرة'?'selected':''}>متأخرة</option></select>`;}else if(key==='الأولوية'){inputType='select';options=`<select name="${key}" class="w-full p-3 border rounded-xl bg-gray-50"><option value="عادية" ${data[key]!=='مستعجلة'?'selected':''}>عادية</option><option value="مستعجلة" ${data[key]==='مستعجلة'?'selected':''}>مستعجلة</option></select>`;}
    const currentValue=data[key]||'';if(inputType==='select'){ec.innerHTML+=`<div><div class="label">${key}</div>${options}</div>`;}else if(inputType==='date'){let val=currentValue.split('T')[0]||'';ec.innerHTML+=`<div><div class="label">${key}</div><input type="date" name="${key}" value="${val}" class="w-full p-3 border rounded-xl"></div>`;}else{ec.innerHTML+=`<div><div class="label">${key}</div><input type="text" name="${key}" value="${currentValue}" class="w-full p-3 border rounded-xl"></div>`;}});
    }).catch(()=>{document.body.innerHTML='<div class="text-center text-red-500 p-10">❌ المعاملة غير موجودة أو حدث خطأ</div>';});
    document.getElementById('editForm').addEventListener('submit',async(e)=>{e.preventDefault();if(isSubmitting){showMessage('جاري الحفظ بالفعل...',false);return;}isSubmitting=true;const btn=document.getElementById('saveBtn');const orig=btn.innerText;btn.disabled=true;btn.innerText='جاري الحفظ...';const fd=new FormData(e.target);const updates=Object.fromEntries(fd.entries());try{const res=await fetch(`/api/transaction/${id}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(updates)});const result=await res.json();if(result.success){showMessage('✅ تم الحفظ بنجاح');loadHistory();}else{showMessage('❌ فشل الحفظ: '+(result.message||'خطأ'),true);}}catch(err){showMessage('❌ خطأ في الاتصال',true);}finally{isSubmitting=false;btn.disabled=false;btn.innerText=orig;}});
    function loadHistory(){fetch(`/api/history/${id}`).then(r=>r.json()).then(h=>{const t=document.getElementById('history-timeline');if(h.length===0){t.innerHTML='<p class="text-gray-500 text-center py-8">لا يوجد سجل</p>';return;}let html='';h.forEach(i=>{let timeFormatted=formatDateTime(i.time);html+=`<div class="timeline-item"><span class="timeline-dot"></span><div class="timeline-time">${timeFormatted}</div><div class="timeline-action">${i.action}</div><div class="timeline-user">بواسطة: ${i.user}</div></div>`;});t.innerHTML=html;});}
    function refreshHistory(){const btn=event.target;btn.innerHTML='⏳ جاري التحديث...';btn.disabled=true;loadHistory();setTimeout(()=>{btn.innerHTML='🔄 تحديث';btn.disabled=false;showMessage('✅ تم تحديث سجل التتبع');},1000);}
    loadHistory();
    </script></body></html>
    ''', data=safe_data, id=id, bot_username=Config.BOT_USERNAME, excluded_keys=excluded_keys)

# ================== صفحة مقارنة الإصدارات ==================
@app.route('/transaction/<id>/compare')
def compare_versions(id):
    token = request.args.get('token')
    if not token or not sheets_client or not sheets_client.verify_access_token(token, id):
        abort(403)
    versions = sheets_client.get_audit_log_grouped(id)
    if not versions:
        return render_template_string('''
        <!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8"><title>مقارنة الإصدارات</title>
        <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap" rel="stylesheet">
        <style>*{font-family:'Cairo',sans-serif;}body{background:#f8fafc;display:flex;justify-content:center;align-items:center;min-height:100vh;padding:20px;}.card{background:white;border-radius:32px;padding:40px;max-width:600px;box-shadow:0 20px 40px rgba(0,0,0,0.06);text-align:center;}h2{color:#1f2937;}a{color:#8b5cf6;text-decoration:none;font-weight:600;}a:hover{text-decoration:underline;}
        </style></head><body><div class="card"><h2>📋 لا توجد تغييرات مسجلة لهذه المعاملة</h2><a href="/transaction/{{ id }}?token={{ token }}">← العودة للمعاملة</a></div></body></html>
        ''', id=id, token=token)
    return render_template_string('''
    <!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>مقارنة إصدارات - المعاملة {{ id }}</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet"><script src="https://cdn.tailwindcss.com"></script>
    <style>*{font-family:'Cairo',sans-serif;}body{background:#f0f4ff;padding:24px;}.container{max-width:900px;margin:0 auto;}.header-card{background:white;border-radius:32px;padding:24px 32px;margin-bottom:24px;box-shadow:0 8px 20px rgba(0,0,0,0.04);}.version-card{background:white;border-radius:24px;padding:24px;margin-bottom:16px;box-shadow:0 4px 12px rgba(0,0,0,0.04);border-right:4px solid #8b5cf6;transition:0.2s;}.version-card:hover{transform:translateX(-4px);}.change-item{background:#f9fafb;padding:10px 14px;border-radius:12px;margin:6px 0;display:flex;flex-wrap:wrap;align-items:center;gap:8px;}.old-value{color:#dc2626;text-decoration:line-through;font-weight:600;}.new-value{color:#16a34a;font-weight:700;}.field-name{font-weight:700;color:#4b5563;min-width:120px;}.btn-back{background:#8b5cf6;color:white;padding:12px 28px;border-radius:40px;text-decoration:none;font-weight:600;display:inline-block;transition:0.3s;}.btn-back:hover{transform:translateY(-2px);box-shadow:0 8px 16px rgba(139,92,246,0.3);}.badge-time{background:#f3e8ff;color:#6d28d9;padding:4px 14px;border-radius:40px;font-size:14px;font-weight:600;}@media(max-width:640px){.header-card{padding:16px;}.version-card{padding:16px;}.change-item{flex-direction:column;align-items:flex-start;}}
    </style></head><body><div class="container"><div class="header-card"><h1 class="text-2xl font-extrabold text-purple-700">📊 مقارنة إصدارات المعاملة</h1><p class="text-gray-500">🆔 {{ id }}</p></div>
    {% for version in versions %}<div class="version-card"><div class="flex justify-between items-start mb-4"><span class="badge-time">📅 {{ version.timestamp[:16] }}</span><span class="text-sm text-gray-500">👤 {{ version.changed_by }}</span></div><div class="space-y-2">{% for change in version.changes %}<div class="change-item"><span class="field-name">{{ change.field }}</span><span class="old-value">{{ change.old if change.old else '—' }}</span><span>➜</span><span class="new-value">{{ change.new if change.new else '—' }}</span></div>{% endfor %}</div></div>{% endfor %}
    <div class="text-center mt-8"><a href="/transaction/{{ id }}?token={{ token }}" class="btn-back">← العودة للمعاملة</a></div></div></body></html>
    ''', id=id, versions=versions, token=token)

# ================== صفحة بحث المدير ==================
@app.route('/admin/search', methods=['GET'])
@limiter.limit("10 per minute")
def admin_search():
    token = request.args.get('token')
    if not token or token != Config.ADMIN_SECRET:
        abort(403, description="غير مصرح لك بالدخول إلى لوحة المدير.")
    query = request.args.get('query', '').strip()
    results = []
    if query and sheets_client:
        results = sheets_client.get_transactions_by_name(query)
        results.sort(key=lambda x: x.get('آخر تعديل بتاريخ', ''), reverse=True)
    return render_template_string('''
    <!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8"><title>🔍 بحث متقدم - لوحة المدير</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet"><script src="https://cdn.tailwindcss.com"></script>
    <style>*{font-family:'Cairo',sans-serif;}body{background:linear-gradient(145deg,#0f0c29,#302b63,#24243e);min-height:100vh;padding:24px;}.glass{background:rgba(255,255,255,0.95);backdrop-filter:blur(10px);border-radius:32px;padding:32px;box-shadow:0 20px 40px rgba(0,0,0,0.2);}.search-input{border:2px solid #e5e7eb;border-radius:50px;padding:14px 24px;width:100%;font-size:16px;transition:0.3s;}.search-input:focus{border-color:#6366f1;box-shadow:0 0 0 4px rgba(99,102,241,0.2);outline:none;}.btn-search{background:linear-gradient(135deg,#6366f1,#8b5cf6);color:white;border:none;padding:14px 40px;border-radius:50px;font-weight:700;cursor:pointer;transition:0.3s;}.btn-search:hover{transform:translateY(-2px);box-shadow:0 10px 20px rgba(99,102,241,0.4);}.result-card{background:white;border-radius:20px;padding:20px;margin-bottom:16px;border-right:4px solid #6366f1;transition:0.2s;}.result-card:hover{transform:translateX(-4px);box-shadow:0 4px 12px rgba(0,0,0,0.1);}.badge{display:inline-block;padding:4px 12px;border-radius:40px;font-size:12px;font-weight:700;}.badge-new{background:#e5e7eb;color:#1f2937;}.badge-processing{background:#fef3c7;color:#b45309;}.badge-completed{background:#d1fae5;color:#065f46;}.badge-delayed{background:#fee2e2;color:#991b1b;}.header-gradient{background:linear-gradient(135deg,#6366f1,#8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
    </style></head><body><div class="max-w-6xl mx-auto"><div class="glass mb-8 text-center"><h1 class="text-4xl font-extrabold header-gradient">🔍 بحث متقدم في المعاملات</h1><p class="text-gray-500 mt-2">لوحة تحكم المدير - ابحث باسم، قسم، أو رقم معاملة</p><a href="/" class="text-purple-600 text-sm underline mt-2 inline-block">← العودة للرئيسية</a></div><div class="glass mb-8"><form method="GET" class="flex flex-col md:flex-row gap-4"><input type="text" name="query" placeholder="👤 ابحث باسم صاحب المعاملة..." class="search-input" value="{{ query }}" required><input type="hidden" name="token" value="{{ token }}"><button type="submit" class="btn-search">🔍 بحث</button></form></div>{% if results is defined %}<div class="glass"><div class="flex justify-between items-center mb-6"><h2 class="text-xl font-bold text-gray-800">📋 نتائج البحث</h2><span class="bg-purple-100 text-purple-800 px-4 py-1 rounded-full text-sm font-bold">{{ results|length }} معاملة</span></div>{% if results %}{% for r in results %}<div class="result-card"><div class="flex flex-wrap justify-between items-start gap-4"><div><div class="flex items-center gap-3 mb-2 flex-wrap"><span class="font-mono text-purple-600 font-bold">{{ r.get('ID','—') }}</span>{% set status = r.get('الحالة','') %}<span class="badge {% if status == 'جديد' %}badge-new{% elif status == 'قيد المعالجة' %}badge-processing{% elif status == 'مكتملة' %}badge-completed{% elif status == 'متأخرة' %}badge-delayed{% endif %}">{{ status if status else 'غير محدد' }}</span>{% if r.get('التأخير') == 'نعم' %}<span class="badge badge-delayed">⚠️ متأخرة</span>{% endif %}</div><p class="text-lg font-bold text-gray-800">{{ r.get('اسم صاحب المعاملة الثلاثي','غير معروف') }}</p><div class="flex flex-wrap gap-x-6 gap-y-1 text-sm text-gray-600 mt-1"><span>📂 {{ r.get('القسم','—') }}</span><span>👨‍💼 {{ r.get('الموظف المسؤول','غير معروف') }}</span><span>📅 {{ r.get('آخر تعديل بتاريخ','—') }}</span></div></div><div class="flex gap-2 flex-wrap"><a href="/transaction/{{ r.get('ID') }}?token={{ token }}" target="_blank" class="bg-indigo-100 text-indigo-700 px-4 py-2 rounded-full text-sm font-medium hover:bg-indigo-200 transition">✏️ عرض</a><a href="https://t.me/{{ bot_username }}?start={{ r.get('ID') }}" target="_blank" class="bg-blue-100 text-blue-700 px-4 py-2 rounded-full text-sm font-medium hover:bg-blue-200 transition">📱 بوت</a></div></div></div>{% endfor %}{% else %}<div class="text-center py-12 text-gray-500"><p class="text-3xl mb-4">🔍</p><p>لا توجد معاملات تطابق البحث.</p></div>{% endif %}</div>{% endif %}</div></body></html>
    ''', query=query, results=results, token=token, bot_username=Config.BOT_USERNAME)

@app.route('/')
def index():
    token = request.args.get('token')
    if not token or token != Config.ADMIN_SECRET:
        abort(403)
    return render_template_string('''
    <!DOCTYPE html><html dir="rtl" lang="ar"><head><meta charset="UTF-8"><title>لوحة التحكم - المعاملات</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet"><script src="https://cdn.tailwindcss.com"></script>
    <style>*{font-family:'Cairo',sans-serif;}body{background:linear-gradient(145deg,#f0f4ff 0%,#e8edf5 100%);min-height:100vh;padding:24px;}.glass{background:rgba(255,255,255,0.95);backdrop-filter:blur(10px);border-radius:32px;padding:32px;box-shadow:0 20px 40px rgba(0,0,0,0.06);}.status-badge{display:inline-block;padding:4px 12px;border-radius:40px;font-size:12px;font-weight:700;}.status-new{background:#e5e7eb;color:#1f2937;}.status-processing{background:#fef3c7;color:#b45309;}.status-completed{background:#d1fae5;color:#065f46;}.status-delayed{background:#fee2e2;color:#991b1b;}table{border-collapse:separate;border-spacing:0 8px;width:100%;}td,th{padding:12px 16px;}tr{background:white;border-radius:16px;transition:0.2s;}tr:hover{transform:translateY(-2px);box-shadow:0 8px 20px rgba(0,0,0,0.06);}.btn-edit{background:linear-gradient(135deg,#8b5cf6,#6366f1);padding:6px 14px;border-radius:40px;color:white;font-size:13px;font-weight:600;text-decoration:none;transition:0.2s;}.btn-edit:hover{transform:translateY(-1px);box-shadow:0 4px 10px rgba(139,92,246,0.4);}.header-gradient{background:linear-gradient(135deg,#6366f1,#8b5cf6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
    </style></head><body><div class="max-w-6xl mx-auto"><div class="glass mb-6"><h1 class="text-3xl font-extrabold header-gradient">📋 جميع المعاملات</h1><p class="text-gray-500 mt-1">لوحة تحكم المدير</p></div><div class="mb-4"><input type="text" id="searchInput" placeholder="🔍 ابحث بـ ID أو الاسم أو الحالة..." class="w-full p-3 border-0 rounded-2xl shadow-md focus:ring-2 focus:ring-purple-400 bg-white/90 backdrop-blur"></div><div class="overflow-x-auto"><table><thead><tr class="bg-white/80 backdrop-blur shadow-sm rounded-2xl"><th class="text-right px-4 py-3 text-purple-800">ID</th><th class="text-right px-4 py-3 text-purple-800">الاسم</th><th class="text-right px-4 py-3 text-purple-800">الحالة</th><th class="text-right px-4 py-3 text-purple-800">الموظف</th><th class="text-right px-4 py-3 text-purple-800">القسم</th><th class="text-right px-4 py-3 text-purple-800">آخر تعديل</th><th class="text-right px-4 py-3 text-purple-800"></th></tr></thead><tbody id="transactions"></tbody></table></div></div><script>
    function getStatusClass(s){if(s==='جديد')return 'status-new';if(s==='قيد المعالجة')return 'status-processing';if(s==='مكتملة')return 'status-completed';if(s==='متأخرة')return 'status-delayed';return '';}
    fetch('/api/transactions').then(r=>r.json()).then(data=>{const tbody=document.getElementById('transactions');data.forEach(t=>{const cls=getStatusClass(t.status);const row=`<tr class="shadow-sm"><td class="rounded-r-2xl font-mono text-sm">${t.id}</td><td>${t.name||'—'}</td><td><span class="status-badge ${cls}">${t.status||'—'}</span></td><td>${t.employee||'—'}</td><td>${t.department||'—'}</td><td class="text-left" dir="ltr">${t.last_modified||'—'}</td><td class="rounded-l-2xl"><a href="/transaction/${t.id}" class="btn-edit inline-block">✏️ تعديل</a></td></tr>`;tbody.innerHTML+=row;});});
    document.getElementById('searchInput').addEventListener('keyup',function(){let filter=this.value.toLowerCase();let rows=document.querySelectorAll('#transactions tr');rows.forEach(row=>{let text=row.innerText.toLowerCase();row.style.display=text.includes(filter)?'':'none';});});
    </script></body></html>
    ''')

# ================== صفحات QR ==================
@app.route('/qr/<id>')
def qr_page(id):
    base_url = request.host_url.rstrip('/')
    token = sheets_client.get_direct_token(id) if sheets_client else None
    edit_link = f"{base_url}/transaction/{id}?token={token}" if token else f"{base_url}/verify-email?transaction_id={id}"
    qr_base64 = QRGenerator.generate_qr(edit_link)
    return f'''
    <!DOCTYPE html><html dir="rtl"><head><meta charset="UTF-8"><title>QR Code للمعاملة {id}</title>
    <style>body{{font-family:sans-serif;background:#f0f2f5;text-align:center;padding:20px;}}.card{{max-width:500px;margin:50px auto;background:white;border-radius:24px;padding:30px;}}.qr{{margin:20px 0;}}</style>
    </head><body><div class="card"><h2>📱 رمز QR للمعاملة</h2><div class="qr"><img src="data:image/png;base64,{qr_base64}" width="200"></div>
    <p><strong>🔹 تعليمات التتبع:</strong><br>1️⃣ امسح الرمز<br>2️⃣ سيتم نقلك إلى صفحة التعديل<br>3️⃣ يمكنك متابعة المعاملة عبر البوت:</p>
    <a href="https://t.me/{Config.BOT_USERNAME}?start={id}" style="background:#0088cc;color:white;padding:10px 20px;border-radius:40px;text-decoration:none;">📱 فتح البوت</a>
    <p style="margin-top:15px;font-size:12px;">⚠️ احتفظ برقم المعاملة: <strong>{id}</strong></p></div></body></html>
    '''

@app.route('/qr_image/<id>')
def qr_image(id):
    base_url = request.host_url.rstrip('/')
    token = sheets_client.get_direct_token(id) if sheets_client else None
    edit_link = f"{base_url}/transaction/{id}?token={token}" if token else f"{base_url}/verify-email?transaction_id={id}"
    qr_base64 = QRGenerator.generate_qr(edit_link)
    return Response(base64.b64decode(qr_base64), mimetype='image/png')

# ================== معالجات الأخطاء ==================
@app.errorhandler(404)
def page_not_found(e):
    return render_template_string('''
    <!DOCTYPE html><html dir="rtl"><head><meta charset="UTF-8"><title>الصفحة غير موجودة</title>
    <style>body{font-family:sans-serif;text-align:center;padding:50px;background:#f8f9fa;}</style>
    </head><body><h1 style="color:#dc2626;">⚠️ الصفحة غير موجودة (404)</h1>
    <p>قد يكون الرابط غير صحيح أو انتهت صلاحيته.</p>
    <a href="https://t.me/{{ bot_username }}" style="display:inline-block;background:#2563eb;color:white;padding:12px 24px;border-radius:40px;text-decoration:none;margin-top:20px;">📞 تواصل مع فريق العمل عبر البوت</a></body></html>
    ''', bot_username=Config.BOT_USERNAME), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template_string('''
    <!DOCTYPE html><html dir="rtl"><head><meta charset="UTF-8"><title>غير مصرح</title>
    <style>body{font-family:sans-serif;text-align:center;padding:50px;background:#f8f9fa;}</style>
    </head><body><h1 style="color:#dc2626;">⛔ غير مصرح بالدخول (403)</h1>
    <p>رمز الوصول غير صالح أو منتهي الصلاحية.</p>
    <a href="https://t.me/{{ bot_username }}" style="display:inline-block;background:#2563eb;color:white;padding:12px 24px;border-radius:40px;text-decoration:none;margin-top:20px;">📞 تواصل مع فريق العمل للحصول على رابط جديد</a></body></html>
    ''', bot_username=Config.BOT_USERNAME), 403

# ================== مراقبة المعاملات الجديدة والجدولة ==================
last_row_count = 0
_last_row_lock = threading.Lock()

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
        with _last_row_lock:
            if current_count > last_row_count:
                logger.info(f"📦 تم اكتشاف {current_count - last_row_count} معاملات جديدة")
                last_row_count = current_count
    except Exception as e:
        logger.error(f"❌ خطأ في دالة المراقبة: {e}")

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
    scheduler.add_job(
        func=lambda: asyncio.run_coroutine_threadsafe(send_delayed_alert_to_admin(), background_loop) if background_loop else None,
        trigger=IntervalTrigger(hours=6),
        id='delayed_alert',
        replace_existing=True
    )
    if ai_assistant:
        scheduler.add_job(
            func=lambda: asyncio.run_coroutine_threadsafe(auto_train_ai_model(), background_loop) if background_loop else None,
            trigger=IntervalTrigger(days=1),
            id='auto_train_ai',
            replace_existing=True
        )
        logger.info("🧠 تم تفعيل جدولة تدريب AI التلقائي (يومياً)")
    logger.info("🔍 بدأت مراقبة المعاملات الجديدة (كل 30 ثانية)")
    logger.info("🔔 تم تفعيل جدولة تنبيه المتأخرات (كل 6 ساعات)")
    atexit.register(lambda: scheduler.shutdown())
    atexit.register(lambda: executor.shutdown(wait=False))

# ================== تشغيل التطبيق ==================
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=Config.DEBUG)
