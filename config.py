import os

class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    BOT_USERNAME = os.getenv("BOT_USERNAME", "mtu_jit_bot")
    ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

    # Web App URL
    WEB_APP_URL = os.getenv("WEB_APP_URL", "https://universitybot-production.up.railway.app")

    # Google Sheets
    GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
    SHEET_MANAGER = os.getenv("SHEET_MANAGER", "manager")
    SHEET_HISTORY = os.getenv("SHEET_HISTORY", "history")
    SHEET_QR = os.getenv("SHEET_QR", "qr")

    # AI
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")

    # Other
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"