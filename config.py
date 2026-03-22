import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    BOT_USERNAME = os.getenv('BOT_USERNAME')
    ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '0'))

    # Google Sheets
    SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
    SHEET_MANAGER = "manager"
    SHEET_QR = "QR"
    SHEET_ARCHIVE = "Archive"
    SHEET_HISTORY = "TransactionHistory"

    # Web App
    WEB_APP_URL = os.getenv('WEB_APP_URL', 'https://your-app.up.railway.app')

    # Email (Brevo SMTP)
    EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp-relay.brevo.com')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
    EMAIL_USER = os.getenv('EMAIL_USER', os.getenv('BREVO_FROM_EMAIL'))
    EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', os.getenv('BREVO_SMTP_KEY'))