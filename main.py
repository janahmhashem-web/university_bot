import threading
print("✅ main.py imported successfully")
import os
from bot import init_bot
from web import app

print("🚀 calling init_bot")
if __name__ == "__main__":
    bot_thread = threading.Thread(target=init_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)