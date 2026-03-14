import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from config import Config
from sheets import GoogleSheetsClient
from email_service import EmailService
from qr_generator import QRGenerator
import re

logger = logging.getLogger(__name__)

class TransactionBot:
    def __init__(self):
        self.sheets = GoogleSheetsClient()
        self.email = EmailService()
        self.qr = QRGenerator()
        self.app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        self._setup_handlers()

    def _setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("id", self.get_id))
        self.app.add_handler(CommandHandler("history", self.get_history))
        self.app.add_handler(CommandHandler("search", self.search))
        self.app.add_handler(CommandHandler("wake", self.wake))
        self.app.add_handler(CommandHandler("stats", self.stats))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        is_admin = (user_id == Config.ADMIN_CHAT_ID)
        msg = "👋 *مرحباً بك في بوت متابعة المعاملات*\n\n"
        msg += "📌 *الأوامر العامة:*\n"
        msg += "🔹 /id [رقم] - تفاصيل معاملة\n"
        msg += "🔹 /history [رقم] - سجل التتبع\n"
        msg += "🔹 /search [كلمة] - بحث\n"
        msg += "🔹 /wake - تحديث فوري\n"
        if is_admin:
            msg += "\n👑 *أوامر المدير:*\n"
            msg += "🔹 /stats - إحصائيات\n"
        await update.message.reply_text(msg, parse_mode='Markdown')

    async def get_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("الرجاء إدخال رقم المعاملة")
            return
        tx_id = context.args[0]
        result = self.sheets.get_row_by_id(Config.SHEET_MANAGER, tx_id)
        if not result:
            await update.message.reply_text("❌ لم يتم العثور على المعاملة")
            return
        data = result['data']
        delay = data.get('التأخير', 'لا يوجد')
        status = "⏰ متأخرة" if 'يوم' in str(delay) else "✅ في الموعد"
        msg = f"🔍 *معاملة {tx_id}*\n\n"
        msg += f"👤 الاسم: {data.get('اسم صاحب المعاملة الثلاثي', '-')}\n"
        msg += f"📌 النوع: {data.get('نوع المعاملة', '-')}\n"
        msg += f"📊 الحالة: {data.get('الحالة', '-')}\n"
        msg += f"📍 المرحلة: {data.get('المؤسسة الحالية', '-')}\n"
        msg += f"👨‍💼 الموظف: {data.get('الموظف المسؤول', '-')}\n"
        msg += f"📈 التأخير: {status}\n"
        await update.message.reply_text(msg, parse_mode='Markdown')

    async def get_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("الرجاء إدخال رقم المعاملة")
            return
        tx_id = context.args[0]
        records = self.sheets.get_all_records(Config.SHEET_HISTORY)
        events = [r for r in records if str(r.get('ID', '')) == tx_id]
        if not events:
            await update.message.reply_text("لا يوجد سجل لهذه المعاملة")
            return
        msg = f"📜 *سجل {tx_id}*\n\n"
        for e in events[-5:]:
            msg += f"📅 {e.get('تاريخ التتبع', '')}\n"
            msg += f"🔹 {e.get('الإجراء', '')} ({e.get('نوع الإجراء', '')})\n"
            msg += f"👤 {e.get('الموظف المسؤول', '')}\n➖\n"
        await update.message.reply_text(msg, parse_mode='Markdown')

    async def search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("الرجاء إدخال كلمة البحث")
            return
        keyword = context.args[0].lower()
        records = self.sheets.get_all_records(Config.SHEET_MANAGER)
        results = [r for r in records if keyword in str(r.get('ID', '')).lower() or keyword in str(r.get('اسم صاحب المعاملة الثلاثي', '')).lower()]
        if not results:
            await update.message.reply_text("لا توجد نتائج")
            return
        msg = f"🔎 نتائج البحث عن '{keyword}':\n"
        for r in results[:10]:
            msg += f"\n🆔 {r.get('ID', '')} - {r.get('اسم صاحب المعاملة الثلاثي', '')} ({r.get('الحالة', '')})"
        if len(results) > 10:
            msg += f"\n...و {len(results)-10} أخرى"
        await update.message.reply_text(msg)

    async def wake(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("✅ التحديث الفوري مفعل")

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != Config.ADMIN_CHAT_ID:
            await update.message.reply_text("❌ هذا الأمر للمدير فقط")
            return
        records = self.sheets.get_all_records(Config.SHEET_MANAGER)
        total = len(records)
        completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
        pending = sum(1 for r in records if r.get('الحالة') == 'قيد الانتظار')
        delayed = sum(1 for r in records if 'يوم' in str(r.get('التأخير', '')))
        msg = f"📊 *إحصائيات*\n📌 إجمالي: {total}\n✅ مكتملة: {completed}\n⏳ قيد الانتظار: {pending}\n⚠️ متأخرة: {delayed}"
        await update.message.reply_text(msg, parse_mode='Markdown')

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        if re.match(r'^[A-Z0-9-]+$', text):
            context.args = [text]
            await self.get_id(update, context)
        else:
            context.args = [text]
            await self.search(update, context)

    def run(self):
        self.sheets.ensure_sheets_exist()
        logger.info("✅ البوت جاهز مع Webhook")
        self.app.run_webhook(
            listen="0.0.0.0",
            port=8080,
            url_path="webhook",
            webhook_url=f"{Config.WEB_APP_URL}/webhook"
        )