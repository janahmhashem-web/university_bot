import os
import logging
from config import Config
from sheets import GoogleSheetsClient
import google.generativeai as genai

logger = logging.getLogger(__name__)

class AIAssistant:
    def __init__(self):
        self.api_key = os.getenv('GOOGLE_API_KEY')
        if not self.api_key:
            logger.warning("⚠️ GOOGLE_API_KEY غير موجود")
            self.model = None
        else:
            try:
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel('gemini-2.0-flash')  # اختر نموذجاً من القائمة
                self.sheets = GoogleSheetsClient()
                logger.info("✅ تم تهيئة Gemini AI (بالمكتبة القديمة)")
            except Exception as e:
                logger.error(f"❌ فشل التهيئة: {e}")
                self.model = None

    async def get_response(self, user_message, user_id, user_name=""):
        if not self.model:
            return "الذكاء الاصطناعي غير متاح."
        try:
            response = self.model.generate_content(user_message)
            return response.text
        except Exception as e:
            logger.error(f"خطأ: {e}")
            return "حدث خطأ."