import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    BOT_USERNAME = os.getenv('BOT_USERNAME', 'mtu_jit_bot')
    ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '0'))
    ADMIN_SECRET = os.getenv('ADMIN_SECRET')
    WEB_APP_URL = os.getenv('WEB_APP_URL', 'https://your-app.up.railway.app')
    SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
    GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON')
    SHEET_MANAGER = os.getenv('SHEET_MANAGER', 'manager')
    SHEET_HISTORY = os.getenv('SHEET_HISTORY', 'history')
    SHEET_QR = os.getenv('SHEET_QR', 'qr')
    SHEET_USERS = os.getenv('SHEET_USERS', 'users')
    SHEET_ACCESS_TOKENS = os.getenv('SHEET_ACCESS_TOKENS', 'access_tokens')
    SHEET_ARCHIVE_MANAGER = os.getenv('SHEET_ARCHIVE_MANAGER', 'archive_manager')
    SHEET_ARCHIVE_HISTORY = os.getenv('SHEET_ARCHIVE_HISTORY', 'archive_history')
    SHEET_ALLOWED_EMAILS = os.getenv('SHEET_ALLOWED_EMAILS', 'allowed_emails')
    GROQ_API_KEY = os.getenv('GROQ_API_KEY')
    EMAIL_USER = os.getenv('EMAIL_USER')
    EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
    BREVO_SMTP_KEY = os.getenv('BREVO_SMTP_KEY')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

    @classmethod
    def validate(cls):
        required_vars = ['TELEGRAM_BOT_TOKEN', 'ADMIN_CHAT_ID', 'SPREADSHEET_ID', 'GOOGLE_CREDENTIALS_JSON']
        missing = [var for var in required_vars if not getattr(cls, var)]
        if missing:
            raise ValueError(f"المتغيرات البيئية التالية مفقودة: {', '.join(missing)}")
        return True
