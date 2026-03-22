import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    BOT_USERNAME = os.getenv('BOT_USERNAME')
    ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID', '0'))
    SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
    WEB_APP_URL = os.getenv('WEB_APP_URL', 'https://your-app.up.railway.app')

    # Gmail SMTP
    EMAIL_USER = os.getenv('EMAIL_USER')
    EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

    SHEET_MANAGER = "manager"
    SHEET_QR = "QR"
    SHEET_ARCHIVE = "Archive"
    SHEET_HISTORY = "TransactionHistory"