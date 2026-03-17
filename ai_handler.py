import os
import logging
from config import Config
from sheets import GoogleSheetsClient
from openai import OpenAI

logger = logging.getLogger(__name__)

class AIAssistant:
    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv('GROQ_API_KEY'),
            base_url="https://api.groq.com/openai/v1"
        )
        self.sheets = GoogleSheetsClient()
        logger.info("✅ تم تهيئة Groq AI عبر OpenAI library")

    async def get_response(self, user_message, user_id, user_name=""):
        try:
            # بناء السياق من قاعدة البيانات
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
            completion = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1000
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ خطأ في Groq عبر OpenAI: {e}")
            return "عذراً، حدث خطأ. حاول مرة أخرى."