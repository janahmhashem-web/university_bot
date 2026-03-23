import os
from groq import Groq
import logging

logger = logging.getLogger(__name__)

class AIAssistant:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        self.client = Groq(api_key=api_key)
        self.model = "mixtral-8x7b-32768"

    async def get_response(self, user_message, user_id, user_name):
        try:
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "أنت مساعد ذكي ومفيد. أجب بالعربية."},
                    {"role": "user", "content": user_message}
                ],
                model=self.model,
                temperature=0.7,
                max_tokens=500
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq error: {e}")
            return "عذراً، حدث خطأ في المعالجة."