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
        logger.info("✅ تم تهيئة Groq AI مع دعم البيانات الكاملة")

    async def get_response(self, user_message, user_id, user_name="", transaction_id=None):
        try:
            context = ""
            transaction_data = None

            if transaction_id:
                row_info = self.sheets.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
                if row_info:
                    transaction_data = row_info['data']
                    context = "\n".join([f"{k}: {v}" for k, v in transaction_data.items() if v])
            else:
                subs_ws = self.sheets.get_worksheet(Config.SHEET_SUBSCRIBERS)
                if subs_ws:
                    records = subs_ws.get_all_records()
                    user_tx = [r for r in records if str(r.get('user_id')) == str(user_id)]
                    if user_tx:
                        transaction_id = user_tx[0].get('transaction_id')
                        if transaction_id:
                            row_info = self.sheets.get_row_by_id(Config.SHEET_MANAGER, transaction_id)
                            if row_info:
                                transaction_data = row_info['data']
                                context = "\n".join([f"{k}: {v}" for k, v in transaction_data.items() if v])

            try:
                records = self.sheets.get_all_records(Config.SHEET_MANAGER)
                total = len(records)
                context += f"\nإجمالي المعاملات في النظام: {total}"
            except:
                pass

            prompt = f"""أنت مساعد ذكي لنظام إدارة المعاملات. أنت ملم بكل تفاصيل المعاملات.
المستخدم: {user_name} (ID: {user_id})
المعلومات المتاحة:
{context}

رسالة المستخدم: {user_message}

أجب بلغة عربية فصيحة ومهذبة، وقدم تحليلاً ذكياً إذا طُلب منك تقييم حالة معاملة. استخدم البيانات المتاحة لتكون دقيقاً.
"""
            completion = self.client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=1000
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ خطأ في Groq: {e}")
            return "عذراً، حدث خطأ. حاول مرة أخرى."