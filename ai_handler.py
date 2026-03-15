import google.generativeai as genai
import os
import logging
from config import Config
from sheets import GoogleSheetsClient

logger = logging.getLogger(__name__)

class AIAssistant:
    def __init__(self):
        self.api_key = os.getenv('GOOGLE_API_KEY')
        if not self.api_key:
            logger.warning("⚠️ GOOGLE_API_KEY غير موجود، الذكاء الاصطناعي معطل")
            self.model = None
        else:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel('gemini-1.5-flash')
            self.sheets = GoogleSheetsClient()
            logger.info("✅ تم تهيئة Gemini AI")
    
    async def get_response(self, user_message, user_id, user_name=""):
        if not self.model:
            return "عذراً، خدمة الذكاء الاصطناعي غير متاحة حالياً."
        
        # بناء سياق من قاعدة البيانات (اختياري)
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
        
        أجب بلغة عربية فصيحة ومهذبة. إذا سأل عن معاملة معينة، اطلب منه رقم المعاملة. إذا سأل عن إحصائيات، قدمها من المعلومات المتاحة. كن مفيداً ولطيفاً.
        """
        
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"خطأ في استدعاء Gemini: {e}")
            return "عذراً، حدث خطأ أثناء معالجة طلبك."