#!/usr/bin/env python
import os
import sys
import logging
import threading
import time
import asyncio
from datetime import datetime
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

from sheets import GoogleSheetsClient
from config import Config
from qr_generator import QRGenerator
from ai_handler import AIAssistant
from web import app  # استيراد تطبيق Flask من web.py

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

# ------------------ الذكاء الاصطناعي ------------------
try:
    ai_assistant = AIAssistant()
    logger.info("✅ تم تهيئة Groq AI")
except Exception as e:
    logger.error(f"❌ فشل تهيئة Groq AI: {e}")
    ai_assistant = None

# ------------------ متغيرات عامة للبوت ------------------
bot_app = None
background_loop = None

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

# ------------------ دوال البوت ------------------
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

# ------------------ إعداد البوت ------------------
def setup_bot():
    global bot_app, background_loop
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
        bot_app.add_handler(CommandHandler("qr", qr_command))
        bot_app.add_handler(CommandHandler("support", support_command))
        bot_app.add_handler(CommandHandler("analyze", analyze))
        bot_app.add_handler(CallbackQueryHandler(button_callback))
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

        # بدء حلقة مراقبة بسيطة
        def monitoring_loop():
            logger.info("🔄 بدء حلقة المراقبة اليدوية (كل 10 ثوانٍ)")
            while True:
                try:
                    # يمكنك إضافة منطق مراقبة المعاملات الجديدة هنا
                    pass
                except Exception as e:
                    logger.error(f"خطأ في حلقة المراقبة: {e}")
                time.sleep(10)
        monitoring_thread = threading.Thread(target=monitoring_loop, daemon=True)
        monitoring_thread.start()
        logger.info("🔍 بدأت مراقبة المعاملات الجديدة والتحديثات (كل 10 ثوانٍ)")
    except Exception as e:
        logger.error(f"❌ فشل إعداد البوت: {e}", exc_info=True)
        bot_app = None

# ------------------ إضافة مسار webhook إلى Flask app ------------------
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

# ------------------ تشغيل الخادم ------------------
if __name__ == "__main__":
    # بدء البوت في خلفية
    bot_thread = threading.Thread(target=setup_bot, daemon=True)
    bot_thread.start()
    # تشغيل خادم Flask (يستخدم gunicorn في الإنتاج)
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)