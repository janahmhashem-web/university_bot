#!/usr/bin/env python
import logging
import sys
import os
import asyncio
import threading
import time
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests

from config import Config
from wap import app, sheets_client, ai_assistant, notify_user, save_user_chat, background_loop, bot_app
from wap import bot_app as wap_bot_app  # سيتم تعيينه لاحقاً
from wap import background_loop as wap_background_loop

# ------------------ إعداد التسجيل ------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

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

    # أزرار عادية تظهر أسفل شريط الكتابة
    keyboard = [
        [KeyboardButton("/id")],
        [KeyboardButton("/history")],
        [KeyboardButton("/qr")],
        [KeyboardButton("/support")],
        [KeyboardButton("/analyze")],
    ]
    if is_admin:
        keyboard.append([KeyboardButton("/stats")])

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    msg = "👋 *مرحباً بك في بوت متابعة المعاملات*\n\n"
    msg += "يمكنك استخدام الأزرار أدناه لتنفيذ الأوامر مباشرة:\n"
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=reply_markup)

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

# ------------------ إعداد البوت وحلقة الأحداث ------------------
bot_app = None
background_loop = None

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

        # تعيين المتغيرات في wap.py
        import wap
        wap.bot_app = bot_app
        wap.background_loop = background_loop
    except Exception as e:
        logger.error(f"❌ فشل إعداد البوت: {e}")
        bot_app = None

# ------------------ Webhook (يتم استيراده من wap.py) ------------------
# نقطة /webhook موجودة بالفعل في wap.py، لكننا نحتاج إلى استدعاء set_webhook_sync بعد بدء التطبيق.
# نقوم بذلك في wap.py عبر الدالة delayed_webhook.
# سيبقى webhook في wap.py ويعمل بشكل طبيعي.

# ------------------ تشغيل التطبيق ------------------
if __name__ == "__main__":
    # عند التشغيل المباشر، نقوم بتشغيل Flask app
    from wap import app
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)