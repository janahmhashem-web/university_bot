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
            logger.warning("⚠️ GOOGLE_API_KEY غير موجود، الذكاء الاصطناعي معطل")
            self.model = None
        else:
            try:
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel('gemini-2.0-flash')  # تأكد من استخدام اسم صحيح
                self.sheets = GoogleSheetsClient()
                logger.info("✅ تم تهيئة Gemini AI باستخدام المكتبة القديمة")
            except Exception as e:
                logger.error(f"❌ فشل تهيئة Gemini: {e}")
                self.model = None

    async def get_response(self, user_message, user_id, user_name=""):
        if not self.model:
            return "عذراً، خدمة الذكاء الاصطناعي غير متاحة حالياً."

        # يمكن إضافة سياق إذا أردت
        context = ""
        try:
            records = self.sheets.get_all_records(Config.SHEET_MANAGER)
            total = len(records)
            context = f"يوجد حالياً {total} معاملة في النظام."
        except:
            pass

        prompt = f"""أنت مساعد ذكي لنظام إدارة المعاملات.
المستخدم: {user_name} (ID: {user_id})
معلومات عامة: {context}
رسالة المستخدم: {user_message}

أجب بلغة عربية فصيحة ومهذبة. إذا سأل عن معاملة معينة، اطلب رقمها. إذا سأل عن إحصائيات، قدمها من المعلومات المتاحة. كن مفيداً.
"""
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"❌ خطأ في استدعاء Gemini: {e}")
            return "عذراً، حدث خطأ أثناء معالجة طلبك."