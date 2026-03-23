import threading
import os
import logging

from config import Config
from sheets import GoogleSheetsClient
from ai_handler import AIAssistant
import globals
from bot import setup_bot
from web import app  # app is imported from web, but it's already in globals; however we need to run it

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)

# Initialize shared objects
try:
    globals.sheets_client = GoogleSheetsClient()
except Exception as e:
    logging.error(f"❌ فشل تهيئة Google Sheets: {e}")
    globals.sheets_client = None

globals.ai_assistant = AIAssistant()  # this will use globals.sheets_client internally

# Start the bot in a background thread
bot_thread = threading.Thread(target=setup_bot, daemon=True)
bot_thread.start()

# Run Flask app
port = int(os.environ.get('PORT', 8080))
app.run(host='0.0.0.0', port=port)