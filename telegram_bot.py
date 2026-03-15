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
    def __init__(self, token):
        self.sheets = GoogleSheetsClient()
        self.email = EmailService()
        self.qr = QRGenerator()
        self.app = Application.builder().token(token).build()
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

    # باقي الدوال (get_id, get_history, search, wake, stats, handle_text) كما هي في النسخة السابقة
    # (مشابهة للتي في الرد السابق، مع تغيير `await` واستخدام `ContextTypes`)