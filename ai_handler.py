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
                logger.info("✅ تم تهيئة Google Gen AI")

                # ========== كود مؤقت لجلب النماذج المتاحة ==========
                try:
                    models = self.client.models.list()
                    logger.info("📋 قائمة النماذج المتاحة لحسابك:")
                    for m in models:
                        logger.info(f"   - {m.name}")
                except Exception as e:
                    logger.error(f"❌ فشل جلب النماذج: {e}")
                # ===================================================

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
        except:
            pass

        prompt = f"""أنت مساعد ذكي لنظام إدارة المعاملات.
المستخدم: {user_name} (ID: {user_id})
معلومات عامة: {context}
رسالة المستخدم: {user_message}

أجب بلغة عربية فصيحة ومهذبة. إذا سأل عن معاملة معينة، اطلب رقمها. إذا سأل عن إحصائيات، قدمها من المعلومات المتاحة. كن مفيداً.
"""
        try:
            # ====== اختر أحد النماذج التي ستظهر في السجلات ======
            # مثلاً: 'gemini-2.0-flash-001' أو 'gemini-2.0-flash-lite' إلخ.
            response = self.client.models.generate_content(
                model='gemini-1.5-flash',  # سيتم استبداله لاحقاً
                contents=prompt
            )
            return response.text
        except Exception as e:
            logger.error(f"❌ خطأ في Gemini: {e}")
            return "عذراً، حدث خطأ. حاول مرة أخرى."