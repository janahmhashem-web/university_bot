import os

class Config:
    # Telegram
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    BOT_USERNAME = os.getenv("BOT_USERNAME", "mtu_jit_bot")
    ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

    # Web App URL (يستخدم للويب هوك)
    WEB_APP_URL = os.getenv("WEB_APP_URL", "https://universitybot-production.up.railway.app")

    # Google Sheets
    GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

    # Groq AI
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")

    # Brevo SMTP
    BREVO_SMTP_KEY = os.getenv("BREVO_SMTP_KEY")
    EMAIL_USER = os.getenv("EMAIL_USER", "janahmhashem@gmail.com")

    # Debug mode
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"