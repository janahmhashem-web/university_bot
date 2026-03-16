import os
import logging
from config import Config
from sheets import GoogleSheetsClient
from google import genai

logger = logging.getLogger(__name__)

class AIAssistant:
    def __init__(self):
        self.api_key = os.getenv('GOOGLE_API_KEY')
        if not self.api_key:
            logger.warning("⚠️ GOOGLE_API_KEY غير موجود، الذكاء الاصطناعي معطل")
            self.client = None
        else:
            try:
                self.client = genai.Client(api_key=self.api_key)
                self.sheets = GoogleSheetsClient()
                logger.info("✅ تم تهيئة Google Gen AI (المكتبة الجديدة)")
            except Exception as e:
                logger.error(f"❌ فشل تهيئة Gemini: {e}")
                self.client = None

    async def get_response(self, user_message, user_id, user_name=""):
        if not self.client:
            return "عذراً، خدمة الذكاء الاصطناعي غير متاحة حالياً."

        context = ""
        try:
            records = self.sheets.get_all_records(Config.SHEET_MANAGER)
            total = len(records)
            context = f"يوجد حالياً {total} معاملة في النظام."
        except Exception as e:
            logger.warning(f"⚠️ لا يمكن جلب الإحصائيات: {e}")

        prompt = f"""أنت مساعد ذكي لنظام إدارة المعاملات.
المستخدم: {user_name} (ID: {user_id})
معلومات عامة: {context}
رسالة المستخدم: {user_message}

أجب بلغة عربية فصيحة ومهذبة. إذا سأل عن معاملة معينة، اطلب رقمها. إذا سأل عن إحصائيات، قدمها من المعلومات المتاحة. كن مفيداً ولطيفاً.
"""

        try:
            response = self.client.models.generate_content(
                model='gemini-1.5-flash',
                contents=prompt
            )
            return response.text
        except Exception as e:
            logger.error(f"❌ خطأ في استدعاء Gemini: {e}")
            return "عذراً، حدث خطأ أثناء معالجة طلبك. حاول مرة أخرى لاحقاً."