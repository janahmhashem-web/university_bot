#!/usr/bin/env python
import os
os.environ['GUNICORN_TIMEOUT'] = '600'

import logging
import sys
import json
import asyncio
import threading
import time
import random
from concurrent.futures import ThreadPoolExecutor
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
    msg += "🔹 /subscribe [رقم] - متابعة معاملة\n"
    msg += "🔹 /unsubscribe [رقم] - إلغاء متابعة\n"
    msg += "🔹 /status [رقم] - تحليل ذكي للحالة\n"
    if is_admin:
        msg += "\n👑 *أوامر المدير:*\n"
        msg += "🔹 /stats - إحصائيات عامة\n"
    await update.message.reply_text(msg, parse_mode='Markdown')

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

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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
        existing = [r for r in records if str(r.get('user_id')) == str(user_id) and str(r.get('transaction_id')) == transaction_id]
        if existing:
            await update.message.reply_text("✅ أنت بالفعل متابع لهذه المعاملة")
            return
        subs_ws.append_row([user_id, transaction_id])
        await update.message.reply_text(f"✅ تم تفعيل متابعة المعاملة {transaction_id}\nستصلك إشعارات فورية عند أي تحديث.")
    except Exception as e:
        logger.error(f"خطأ في subscribe: {e}")
        await update.message.reply_text("حدث خطأ، حاول مرة أخرى.")

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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
            if str(row.get('user_id')) == str(user_id) and str(row.get('transaction_id')) == transaction_id:
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
    user_id = update.effective_user.id
    if sheets_client:
        subs_ws = sheets_client.get_worksheet(Config.SHEET_SUBSCRIBERS)
        if subs_ws:
            records = subs_ws.get_all_records()
            for r in records:
                if str(r.get('user_id')) == str(user_id):
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

# ------------------ مراقبة المعاملات الجديدة ------------------
last_row_count = 0
last_state = {}
executor = ThreadPoolExecutor(max_workers=10)

def process_transaction(transaction_data):
    """معالجة معاملة واحدة في خيط منفصل"""
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

        customer_email = new_row.get('البريد الإلكتروني')
        customer_name = new_row.get('اسم صاحب المعاملة الثلاثي')
        logger.info(f"📧 قراءة البريد من الشيت: '{customer_email}' للمعاملة {transaction_id}")

        if transaction_id and customer_email:
            qr_page_link = f"{Config.WEB_APP_URL}/qr/{transaction_id}"
            success = EmailService.send_customer_email(
                customer_email, customer_name, transaction_id, qr_page_link
            )
            if success:
                logger.info(f"📧 تم إرسال إيميل للمعاملة {transaction_id}")
            else:
                logger.error(f"❌ فشل إرسال الإيميل للمعاملة {transaction_id}")
        else:
            logger.warning(f"⚠️ لا يمكن إرسال الإيميل: ID={transaction_id}, email={customer_email}")

        history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        if history_ws:
            history_ws.append_row([
                datetime.now().isoformat(),
                transaction_id,
                "تم إنشاء المعاملة",
                "النظام"
            ])

        # تخزين الحالة الأولية للمعاملة لمراقبة التغييرات
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
        records = ws.get_all_records()
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
            logger.info(f"✅ تم تفويض {len(tasks)} معاملات للمعالجة المتوازية")
    except Exception as e:
        logger.error(f"❌ خطأ في دالة المراقبة: {e}", exc_info=True)

def check_transaction_updates():
    """فحص التغييرات في الحقول الرئيسية وإرسال إشعارات للمشتركين"""
    try:
        if not sheets_client or not bot_app or not background_loop:
            return
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return
        records = ws.get_all_records()
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
                    # إرسال إشعار للمشتركين
                    subs_ws = sheets_client.get_worksheet(Config.SHEET_SUBSCRIBERS)
                    if subs_ws:
                        subs = subs_ws.get_all_records()
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
    """فحص دوري للمعاملات المتأخرة أو التي تحتاج تنبيه"""
    try:
        if not sheets_client or not bot_app or not background_loop:
            return
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return
        records = ws.get_all_records()
        for row in records:
            tx_id = row.get('ID')
            delay = row.get('التأخير', '')
            status = row.get('الحالة', '')
            if delay == 'نعم' and status != 'مكتملة':
                msg = f"⚠️ *تنبيه: معاملة {tx_id} متأخرة*\nيرجى مراجعة السبب."
                subs_ws = sheets_client.get_worksheet(Config.SHEET_SUBSCRIBERS)
                if subs_ws:
                    subs = subs_ws.get_all_records()
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

# ------------------ إعداد البوت وحلقة الأحداث ------------------
bot_app = None
background_loop = None
loop_thread = None

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

def init_bot():
    global bot_app, background_loop, loop_thread
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
            await bot_app.initialize()
            logger.info("✅ تم تهيئة البوت في الحلقة الخلفية")

        def start_background_loop():
            global background_loop
            background_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(background_loop)
            background_loop.run_until_complete(init_bot_async())
            background_loop.run_forever()

        loop_thread = threading.Thread(target=start_background_loop, daemon=True)
        loop_thread.start()
        logger.info("⏳ انتظار تهيئة البوت في الخلفية...")
        time.sleep(2)

        # تعيين webhook بعد 5 ثوانٍ
        def delayed_webhook():
            time.sleep(5)
            try:
                set_webhook_sync()
            except Exception as e:
                logger.error(f"خطأ في خيط webhook: {e}")
        threading.Thread(target=delayed_webhook, daemon=True).start()
        logger.info("⏳ سيتم تعيين webhook بعد 5 ثوانٍ...")

        # بدء جدولة المهام
        global scheduler, last_row_count, executor
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
                trigger=IntervalTrigger(seconds=5),
                id='check_transactions',
                replace_existing=True
            )
            scheduler.add_job(
                func=check_transaction_updates,
                trigger=IntervalTrigger(seconds=5),
                id='check_updates',
                replace_existing=True
            )
            scheduler.add_job(
                func=smart_alerts,
                trigger=IntervalTrigger(seconds=3600),
                id='smart_alerts',
                replace_existing=True
            )
            logger.info("🔍 بدأت مراقبة المعاملات الجديدة والتحديثات والتنبيهات")
            atexit.register(lambda: scheduler.shutdown())
    except Exception as e:
        logger.error(f"❌ فشل إعداد البوت: {e}")
        bot_app = None