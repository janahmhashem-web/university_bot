import threading
import os
from bot import init_bot
from web import app

if __name__ == "__main__":
    # تشغيل البوت في خلفية
    bot_thread = threading.Thread(target=init_bot, daemon=True)
    bot_thread.start()

    # تشغيل خادم Flask
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)