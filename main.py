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
from flask import Flask, request, jsonify, render_template_string, Response, abort
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
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
    ai_assistant = AIAssistant()
    logger.info("✅ تم تهيئة Groq AI")
except Exception as e:
    logger.error(f"❌ فشل تهيئة Groq AI: {e}")
    ai_assistant = None

# ------------------ دوال مساعدة ------------------
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

    msg = "👋 *مرحباً بك في بوت متابعة المعاملات*\n\n"
    msg += "📌 *للاستفادة من البوت:*\n"
    msg += "🔹 `/id [رقم]` - تفاصيل معاملة\n"
    msg += "🔹 `/history [رقم]` - سجل تتبع كامل للمعاملة (من البداية إلى النهاية)\n"
    msg += "🔹 `/qr` - تعليمات حول طباعة رمز QR لتتبع المعاملة\n"
    msg += "🔹 `/support` - للتواصل مع فريق الدعم\n"
    msg += "🔹 `/wake` - للتأكد من أن البوت يعمل\n"
    if is_admin:
        msg += "\n👑 *أوامر المدير:*\n"
        msg += "🔹 `/stats` - إحصائيات عامة\n"
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
    if not ai_assistant:
        await update.message.reply_text("عذراً، خدمة الذكاء الاصطناعي غير متاحة حالياً.")
        return
    user_message = update.message.text
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or ""
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

# ------------------ مراقبة المعاملات الجديدة ------------------
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

                # التأكد من وجود ID في العمود H (8)
                transaction_id = new_row.get('ID')
                if not transaction_id:
                    now = datetime.now()
                    date_str = now.strftime("%Y%m%d%H%M%S")
                    random_part = random.randint(1000, 9999)
                    transaction_id = f"MUT-{date_str}-{random_part}"
                    try:
                        ws.update_cell(row_number, 8, transaction_id)
                        logger.info(f"🆔 تم توليد ID {transaction_id} للصف {row_number}")
                    except Exception as e:
                        logger.error(f"❌ فشل كتابة ID للصف {row_number}: {e}")
                        continue

                # كتابة رابط التعديل في العمود U (21)
                edit_link = f"{Config.WEB_APP_URL}/transaction/{transaction_id}"
                hyperlink_formula = f'=HYPERLINK("{edit_link}", "تعديل المعاملة")'
                try:
                    ws.update_cell(row_number, 21, hyperlink_formula)
                    logger.info(f"🔗 تم كتابة رابط التعديل في العمود U للصف {row_number}")
                except Exception as e:
                    logger.error(f"❌ فشل كتابة الرابط للصف {row_number}: {e}")

                # إدراج صف في شيت QR
                qr_ws = sheets_client.get_worksheet(Config.SHEET_QR)
                if qr_ws:
                    name = new_row.get('اسم صاحب المعاملة الثلاثي', '')
                    email = new_row.get('البريد الإلكتروني', '')
                    view_link = f"{Config.WEB_APP_URL}/view/{transaction_id}"
                    qr_page_link = f"{Config.WEB_APP_URL}/qr/{transaction_id}"
                    qr_image_url = f"{Config.WEB_APP_URL}/qr_image/{transaction_id}"

                    qr_ws.append_row([
                        name,
                        email,
                        transaction_id,
                        view_link,
                        qr_image_url,
                        qr_page_link,
                        edit_link
                    ])
                    new_row_num = len(qr_ws.get_all_values())
                    qr_ws.update_cell(new_row_num, 4, f'=HYPERLINK("{view_link}", "عرض المعاملة")')
                    qr_ws.update_cell(new_row_num, 5, f'=IMAGE("{qr_image_url}")')
                    qr_ws.update_cell(new_row_num, 6, f'=HYPERLINK("{qr_page_link}", "عرض QR كبير")')
                    qr_ws.update_cell(new_row_num, 7, f'=HYPERLINK("{edit_link}", "تعديل المعاملة")')
                    logger.info(f"📸 تم إدراج بيانات QR للمعاملة {transaction_id}")

                # تسجيل في TransactionHistory
                sheets_client.add_history_entry(transaction_id, "تم إنشاء المعاملة", "النظام")

                # إرسال إشعار للمستخدم
                if background_loop and bot_app:
                    message = f"🎉 *تم إنشاء معاملة جديدة*\n\n"
                    message += f"🆔 رقم المعاملة: `{transaction_id}`\n"
                    message += f"🔗 [رابط المتابعة]({view_link})\n\n"
                    message += f"📌 يمكنك متابعة كل تحديثات معاملتك عبر هذا البوت.\n"
                    message += f"🔍 لعرض سجل التتبع الكامل، استخدم الأمر: `/history {transaction_id}`"
                    asyncio.run_coroutine_threadsafe(
                        notify_user(transaction_id, message),
                        background_loop
                    )

            last_row_count = current_count
    except Exception as e:
        logger.error(f"❌ خطأ في دالة المراقبة: {e}", exc_info=True)

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

# ------------------ نقاط نهاية API ------------------
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

        # تطبيق التحديثات
        for key, value in updates.items():
            if key in headers:
                col = headers.index(key) + 1
                ws.update_cell(row, col, value)

        # تحديث أعمدة التتبع
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

        # تسجيل في TransactionHistory
        changes = ', '.join(updates.keys())
        sheets_client.add_history_entry(id, f"تم تحديث الحقول: {changes}", employee_name)

        # بناء رسالة ذكية للمستخدم
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

# ------------------ صفحة التحقق (للمستخدم) ------------------
@app.route('/verify', methods=['GET'])
def verify_page():
    name = request.args.get('name', '').strip()
    phone = request.args.get('phone', '').strip()

    # إذا لم يصل اسم وهاتف، اعرض النموذج
    if not name or not phone:
        return '''
        <!DOCTYPE html>
        <html dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>التحقق من المعاملة</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; text-align: center; }
                .card { max-width: 400px; margin: 50px auto; background: white; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); padding: 30px; }
                input { width: 100%; padding: 10px; margin: 8px 0; border: 1px solid #ccc; border-radius: 8px; box-sizing: border-box; }
                button { background: #2c3e50; color: white; padding: 12px 24px; border: none; border-radius: 8px; cursor: pointer; width: 100%; font-size: 16px; }
                button:hover { opacity: 0.9; }
            </style>
        </head>
        <body>
            <div class="card">
                <h2>🔍 التحقق من المعاملة</h2>
                <form method="GET">
                    <input type="text" name="name" placeholder="الاسم الثلاثي" required>
                    <input type="text" name="phone" placeholder="رقم الهاتف" required>
                    <button type="submit">تحقق</button>
                </form>
            </div>
        </body>
        </html>
        '''

    # البحث عن المعاملة
    if not sheets_client:
        return "<html dir='rtl'><body><h2>⚠️ النظام غير متصل بقاعدة البيانات</h2></body></html>"

    ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
    if not ws:
        return "<html dir='rtl'><body><h2>⚠️ ورقة manager غير موجودة</h2></body></html>"

    records = ws.get_all_records()
    found = False
    transaction_id = None

    # طباعة للتصحيح في سجلات Railway
    logger.info(f"🔍 البحث عن: الاسم='{name}', الهاتف='{phone}'")
    logger.info(f"📊 عدد السجلات في الشيت: {len(records)}")

    for idx, row in enumerate(records):
        row_name = str(row.get('اسم صاحب المعاملة الثلاثي', '')).strip()
        row_phone = str(row.get('رقم الهاتف', '')).strip()
        logger.info(f"📋 صف {idx+2}: الاسم='{row_name}', الهاتف='{row_phone}'")
        
        if row_name == name and row_phone == phone:
            transaction_id = row.get('ID')
            if transaction_id:
                found = True
                logger.info(f"✅ تم العثور على المعاملة: {transaction_id}")
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
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; text-align: center; }}
                .card {{ max-width: 500px; margin: 50px auto; background: white; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); padding: 30px; }}
                .id {{ font-size: 28px; font-weight: bold; color: #e67e22; background: #fef5e8; display: inline-block; padding: 8px 20px; border-radius: 40px; margin: 15px 0; letter-spacing: 1px; }}
                .btn {{ display: inline-block; background: #2c3e50; color: white; padding: 12px 24px; text-decoration: none; border-radius: 8px; margin: 10px 5px; transition: 0.3s; }}
                .btn-telegram {{ background: #0088cc; }}
                .btn:hover {{ opacity: 0.9; transform: translateY(-2px); }}
            </style>
        </head>
        <body>
            <div class="card">
                <h2>✅ تم العثور على معاملتك</h2>
                <p>رقم المعاملة الخاص بك:</p>
                <div class="id">{transaction_id}</div>
                <p>احتفظ بهذا الرقم لمتابعة المعاملة.</p>
                <a href="{Config.WEB_APP_URL}/view/{transaction_id}" target="_blank" class="btn">🔗 عرض التفاصيل</a>
                <a href="https://t.me/{Config.BOT_USERNAME}?start={transaction_id}" target="_blank" class="btn btn-telegram">📱 فتح البوت لمتابعة المعاملة</a>
            </div>
        </body>
        </html>
        """
    else:
        # عرض رسالة خطأ مع تفاصيل
        return f"""
        <!DOCTYPE html>
        <html dir="rtl">
        <body style="text-align:center;margin-top:50px;">
            <h2>❌ لم نجد معاملة بهذه البيانات</h2>
            <p>الاسم المدخل: "{name}"</p>
            <p>رقم الهاتف المدخل: "{phone}"</p>
            <p>الرجاء التأكد من صحة البيانات وخلوها من الأخطاء الإملائية أو المسافات.</p>
            <p><a href="/verify">🔍 محاولة مرة أخرى</a></p>
        </body>
        </html>
        """

# ------------------ صفحة عرض المعاملة (للقراءة فقط) ------------------
@app.route('/view/<id>')
def view_transaction_page(id):
    try:
        if not sheets_client:
            logger.error("❌ sheets_client غير متصل")
            return "⚠️ النظام غير متصل بقاعدة البيانات", 500

        row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, id)
        if not row_info:
            logger.warning(f"❌ المعاملة {id} غير موجودة")
            return f"❌ المعاملة {id} غير موجودة", 404

        data = row_info['data']
        logger.info(f"✅ تم جلب بيانات المعاملة {id}")

        # جلب سجل التتبع
        history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        history = []
        if history_ws:
            records = history_ws.get_all_records()
            history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
                       for r in records if str(r.get('ID')) == id]
            history.sort(key=lambda x: x['time'], reverse=False)
            logger.info(f"✅ تم جلب {len(history)} سجل تتبع للمعاملة {id}")

        # بناء HTML بتصميم عصري
        html = f"""
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>تفاصيل المعاملة {id}</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
                * {{ font-family: 'Inter', sans-serif; }}
                .ios-card {{ background: rgba(255,255,255,0.95); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.3); border-radius: 24px; box-shadow: 0 8px 20px rgba(0,0,0,0.05); }}
                .label-ios {{ font-size: 13px; font-weight: 600; color: #6b7280; margin-bottom: 4px; display: block; text-transform: uppercase; letter-spacing: 0.5px; }}
                .timeline-item {{ border-right: 2px solid #007aff; position: relative; padding-right: 20px; margin-bottom: 24px; }}
                .timeline-dot {{ width: 12px; height: 12px; background: #007aff; border-radius: 50%; position: absolute; right: -7px; top: 5px; }}
                .timeline-time {{ font-size: 12px; color: #6c757d; margin-bottom: 4px; }}
                .timeline-action {{ font-weight: 600; color: #1f2937; margin-bottom: 4px; }}
                .timeline-user {{ font-size: 12px; color: #9ca3af; }}
                .badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }}
                .badge-new {{ background: #e2e3e5; color: #383d41; }}
                .badge-processing {{ background: #fff3cd; color: #856404; }}
                .badge-completed {{ background: #d4edda; color: #155724; }}
                .badge-delayed {{ background: #f8d7da; color: #721c24; }}
            </style>
        </head>
        <body class="bg-gradient-to-b from-gray-50 to-gray-100 p-4">
            <div class="max-w-4xl mx-auto">
                <div class="ios-card rounded-2xl p-4 mb-4 shadow-sm flex justify-between items-center">
                    <h1 class="text-xl font-semibold">🔍 تفاصيل المعاملة <span class="text-blue-600">{id}</span></h1>
                    <span class="text-gray-500 text-sm bg-white/50 px-3 py-1 rounded-full">(للمتابعة فقط)</span>
                </div>

                <div class="ios-card rounded-2xl p-6 mb-4 shadow-sm">
                    <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">📋 <span>معلومات المعاملة</span></h2>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
        """
        excluded = ['ID', 'LOG_JSON', 'آخر تعديل بتاريخ', 'آخر تعديل بواسطة', 'الرابط', 'عدد التعديلات', 'البريد الإلكتروني الموظف']
        for key, value in data.items():
            if key not in excluded:
                display_value = value if value else '—'
                if key == 'المرافقات' and value and value.startswith('http'):
                    display_value = f'<a href="{value}" target="_blank" class="text-blue-500 underline">📎 فتح المرفق</a>'
                if key == 'الحالة':
                    badge_class = "badge-new" if value == "جديد" else ("badge-processing" if value == "قيد المعالجة" else ("badge-completed" if value == "مكتملة" else ("badge-delayed" if value == "متأخرة" else "")))
                    display_value = f'<span class="badge {badge_class}">{value if value else "—"}</span>'
                html += f"""
                        <div class="bg-gray-50/80 p-4 rounded-xl">
                            <span class="label-ios">{key}</span>
                            <div class="text-gray-900 mt-1 font-medium">{display_value}</div>
                        </div>
                """
        html += """
                    </div>
                </div>

                <div class="ios-card rounded-2xl p-6 mb-4 shadow-sm">
                    <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">📜 <span>سجل الحركات</span></h2>
                    <div class="space-y-2">
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
                            <span class="timeline-dot"></span>
                            <div class="timeline-time">{time_str}</div>
                            <div class="timeline-action">{entry['action']}</div>
                            <div class="timeline-user">بواسطة: {entry['user']}</div>
                        </div>
                """
        else:
            html += '<p class="text-gray-500 text-center py-8">لا يوجد سجل بعد</p>'
        html += """
                    </div>
                </div>
                <div class="text-center text-gray-400 text-sm mt-6">
                    يمكنك متابعة معاملتك عبر البوت: <a href="https://t.me/""" + Config.BOT_USERNAME + f"""?start={id}" class="text-blue-500 underline">@{Config.BOT_USERNAME}</a>
                </div>
            </div>
        </body>
        </html>
        """
        return html
    except Exception as e:
        logger.error(f"🔥 خطأ في عرض المعاملة {id}: {e}", exc_info=True)
        return f"حدث خطأ أثناء تحميل الصفحة: {str(e)}", 500

# ------------------ صفحة تعديل المعاملة (للموظف) ------------------
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
                'Timestamp', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف', 'البريد الإلكتروني',
                'القسم', 'نوع المعاملة', 'المرافقات', 'ID'
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
    return render_template_string(EDIT_HTML)

# ------------------ صفحة المدير (محمية) ------------------
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
                    发展
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

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)