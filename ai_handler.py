import os
import re
from groq import Groq
import logging

logger = logging.getLogger(__name__)

class AIAssistant:
    def __init__(self, sheets_client=None):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        self.client = Groq(api_key=api_key)
        self.model = "llama-3.1-8b-instant"   # أو أي نموذج مدعوم
        self.sheets_client = sheets_client

    async def get_response(self, user_message, user_id, user_name):
        """رد ذكي مع الاستفادة من سياق المستخدم والمعاملات"""
        try:
            # 1. محاولة استخراج رقم معاملة من الرسالة
            transaction_id = self._extract_transaction_id(user_message)

            # 2. إذا لم نعثر على رقم في الرسالة، نبحث عن معاملة مرتبطة بالمستخدم
            if not transaction_id and self.sheets_client:
                transaction_id = self._get_user_transaction_id(user_id)

            # 3. بناء السياق من بيانات المعاملة إن وجدت
            context = ""
            if transaction_id and self.sheets_client:
                # استخدام الدالة الصحيحة: get_latest_row_by_id_fast
                data = self.sheets_client.get_latest_row_by_id_fast("manager", transaction_id)
                if data:
                    context = self._format_transaction_context(data)
                    # إضافة رابط QR إذا طلب المستخدم QR
                    if "qr" in user_message.lower() or "قريء" in user_message:
                        qr_link = f"{os.getenv('WEB_APP_URL')}/qr/{transaction_id}"
                        context += f"\n\nرابط QR الخاص بمعاملتك: {qr_link}"
                else:
                    context = f"لم أجد معاملة بالرقم {transaction_id} في النظام."

            # 4. إذا لم يكن لدينا معاملة محددة، نقدم إحصائيات عامة
            if not context:
                context = await self._get_general_stats_context()

            # 5. بناء الرسالة للنموذج
            system_prompt = (
                "أنت مساعد ذكي ومفيد. لديك معرفة كاملة ببيانات المعاملات المخزنة في النظام. "
                "إذا سألك المستخدم عن معاملة محددة، استخدم المعلومات المقدمة في السياق للإجابة بدقة. "
                "أجب بالعربية بأسلوب مهذب ومفيد. إذا لم تكن لديك المعلومة، أخبر المستخدم بذلك."
            )

            user_prompt = f"رسالة المستخدم: {user_message}\n\n"
            if context:
                user_prompt += f"السياق (بيانات من النظام):\n{context}\n\n"
            user_prompt += "قدم إجابة مفيدة وواضحة."

            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=self.model,
                temperature=0.7,
                max_tokens=600
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq error: {e}")
            return "عذراً، حدث خطأ في المعالجة."

    def _extract_transaction_id(self, text):
        """استخراج رقم المعاملة من النص"""
        match = re.search(r'MUT-\d{14}-\d{4}', text)
        if match:
            return match.group(0)
        match = re.search(r'\b\d{10,}\b', text)
        if match:
            return match.group(0)
        return None
import os
import re
from groq import Groq
import logging

logger = logging.getLogger(__name__)

class AIAssistant:
    def __init__(self, sheets_client=None):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        self.client = Groq(api_key=api_key)
        self.model = "llama-3.1-8b-instant"
        self.sheets_client = sheets_client

    async def get_response(self, user_message, user_id, user_name):
        try:
            # استخراج رقم معاملة من الرسالة
            transaction_id = self._extract_transaction_id(user_message)
            if not transaction_id and self.sheets_client:
                transaction_id = self._get_user_transaction_id(user_id)

            context = ""
            if transaction_id and self.sheets_client:
                data = self.sheets_client.get_latest_row_by_id_fast("manager", transaction_id)
                if data:
                    context = self._format_transaction_context(data)
                    if "qr" in user_message.lower() or "قريء" in user_message:
                        qr_link = f"{os.getenv('WEB_APP_URL')}/qr/{transaction_id}"
                        context += f"\n\nرابط QR الخاص بمعاملتك: {qr_link}"
                else:
                    context = f"لم أجد معاملة بالرقم {transaction_id} في النظام."

            if not context:
                context = await self._get_general_stats_context()

            system_prompt = (
                "أنت مساعد ذكي ومفيد. لديك معرفة كاملة ببيانات المعاملات المخزنة في النظام. "
                "إذا سألك المستخدم عن معاملة محددة، استخدم المعلومات المقدمة في السياق للإجابة بدقة. "
                "أجب بالعربية بأسلوب مهذب ومفيد. إذا لم تكن لديك المعلومة، أخبر المستخدم بذلك."
            )
            user_prompt = f"رسالة المستخدم: {user_message}\n\n"
            if context:
                user_prompt += f"السياق (بيانات من النظام):\n{context}\n\n"
            user_prompt += "قدم إجابة مفيدة وواضحة."

            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=self.model,
                temperature=0.7,
                max_tokens=600
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq error: {e}")
            return "عذراً، حدث خطأ في المعالجة."

    def _extract_transaction_id(self, text):
        match = re.search(r'MUT-\d{14}-\d{4}', text)
        if match:
            return match.group(0)
        match = re.search(r'\b\d{10,}\b', text)
        if match:
            return match.group(0)
        return None

    def _get_user_transaction_id(self, chat_id):
        if not self.sheets_client:
            return None
        try:
            ws = self.sheets_client.get_worksheet("users")
            if not ws:
                return None
            records = ws.get_all_records()
            for row in records:
                if str(row.get('chat_id')) == str(chat_id):
                    return row.get('transaction_id')
        except Exception as e:
            logger.error(f"خطأ في استرجاع معاملة المستخدم: {e}")
        return None

    def _format_transaction_context(self, data):
        lines = []
        lines.append(f"المعاملة رقم {data.get('ID', 'غير معروف')}:")
        lines.append(f"الاسم: {data.get('اسم صاحب المعاملة الثلاثي', 'غير معروف')}")
        lines.append(f"القسم: {data.get('القسم', 'غير معروف')}")
        lines.append(f"الحالة: {data.get('الحالة', 'غير معروف')}")
        lines.append(f"الموظف المسؤول: {data.get('الموظف المسؤول', 'غير معروف')}")
        if data.get('التأخير') == 'نعم':
            lines.append("⚠️ هذه المعاملة متأخرة.")
        lines.append(f"تاريخ التحويل: {data.get('تاريخ التحويل', 'غير معروف')}")
        lines.append(f"المرافقات: {data.get('المرافقات', 'لا يوجد')}")
        return "\n".join(lines)

    async def _get_general_stats_context(self):
        if not self.sheets_client:
            return ""
        try:
            records = self.sheets_client.get_all_records("manager")
            total = len(records)
            completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
            pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
            delayed = sum(1 for r in records if r.get('التأخير') == 'نعم')
            return (
                f"إجمالي المعاملات في النظام: {total}\n"
                f"المعاملات المكتملة: {completed}\n"
                f"المعاملات قيد المعالجة: {pending}\n"
                f"المعاملات المتأخرة: {delayed}\n"
            )
        except Exception as e:
            logger.error(f"خطأ في جلب الإحصائيات: {e}")
            return ""

    async def analyze_transaction(self, transaction_data, history):
        try:
            summary = f"رقم المعاملة: {transaction_data.get('ID', 'غير معروف')}\n"
            summary += f"الاسم: {transaction_data.get('اسم صاحب المعاملة الثلاثي', 'غير معروف')}\n"
            summary += f"القسم: {transaction_data.get('القسم', 'غير معروف')}\n"
            summary += f"الحالة: {transaction_data.get('الحالة', 'غير معروف')}\n"
            summary += f"التأخير: {transaction_data.get('التأخير', 'غير معروف')}\n"
            summary += f"تاريخ التحويل: {transaction_data.get('تاريخ التحويل', 'غير معروف')}\n"
            summary += f"الموظف المسؤول: {transaction_data.get('الموظف المسؤول', 'غير معروف')}\n\n"

            timeline = "سجل التتبع:\n"
            for entry in history:
                timeline += f"- {entry['time']}: {entry['action']} (بواسطة: {entry['user']})\n"

            prompt = f"""
            أنت مساعد متخصص في تحليل المعاملات الإدارية. بناءً على المعلومات التالية، قدم:
            1. ملخصاً مختصراً للمعاملة.
            2. تقييماً للوضع الحالي (هل هناك تأخير، هل الإجراءات مناسبة).
            3. توصيات أو اقتراحات للمتابعة (إن وجدت).
            كن دقيقاً وموجزاً.

            معلومات المعاملة:
            {summary}

            {timeline}
            """
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "أنت محلل معاملات خبير."},
                    {"role": "user", "content": prompt}
                ],
                model=self.model,
                temperature=0.5,
                max_tokens=800
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"خطأ في تحليل المعاملة: {e}")
            return "عذراً، حدث خطأ أثناء تحليل المعاملة."
    def _get_user_transaction_id(self, chat_id):
        """استرجاع رقم المعاملة المرتبطة بالمستخدم من ورقة users"""
        if not self.sheets_client:
            return None
        try:
            ws = self.sheets_client.get_worksheet("users")
            if not ws:
                return None
            records = ws.get_all_records()
            for row in records:
                if str(row.get('chat_id')) == str(chat_id):
                    return row.get('transaction_id')
        except Exception as e:
            logger.error(f"خطأ في استرجاع معاملة المستخدم: {e}")
        return None

    def _format_transaction_context(self, data):
        """تنسيق بيانات المعاملة للسياق"""
        lines = []
        lines.append(f"المعاملة رقم {data.get('ID', 'غير معروف')}:")
        lines.append(f"الاسم: {data.get('اسم صاحب المعاملة الثلاثي', 'غير معروف')}")
        lines.append(f"القسم: {data.get('القسم', 'غير معروف')}")
        lines.append(f"الحالة: {data.get('الحالة', 'غير معروف')}")
        lines.append(f"الموظف المسؤول: {data.get('الموظف المسؤول', 'غير معروف')}")
        if data.get('التأخير') == 'نعم':
            lines.append("⚠️ هذه المعاملة متأخرة.")
        lines.append(f"تاريخ التحويل: {data.get('تاريخ التحويل', 'غير معروف')}")
        lines.append(f"المرافقات: {data.get('المرافقات', 'لا يوجد')}")
        return "\n".join(lines)

    async def _get_general_stats_context(self):
        """إحصائيات عامة للمعاملات"""
        if not self.sheets_client:
            return ""
        try:
            records = self.sheets_client.get_all_records("manager")
            total = len(records)
            completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
            pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
            delayed = sum(1 for r in records if r.get('التأخير') == 'نعم')
            return (
                f"إجمالي المعاملات في النظام: {total}\n"
                f"المعاملات المكتملة: {completed}\n"
                f"المعاملات قيد المعالجة: {pending}\n"
                f"المعاملات المتأخرة: {delayed}\n"
            )
        except Exception as e:
            logger.error(f"خطأ في جلب الإحصائيات: {e}")
            return ""

    async def analyze_transaction(self, transaction_data, history):
        """تحليل معاملة (كما هو)"""
        try:
            summary = f"رقم المعاملة: {transaction_data.get('ID', 'غير معروف')}\n"
            summary += f"الاسم: {transaction_data.get('اسم صاحب المعاملة الثلاثي', 'غير معروف')}\n"
            summary += f"القسم: {transaction_data.get('القسم', 'غير معروف')}\n"
            summary += f"الحالة: {transaction_data.get('الحالة', 'غير معروف')}\n"
            summary += f"التأخير: {transaction_data.get('التأخير', 'غير معروف')}\n"
            summary += f"تاريخ التحويل: {transaction_data.get('تاريخ التحويل', 'غير معروف')}\n"
            summary += f"الموظف المسؤول: {transaction_data.get('الموظف المسؤول', 'غير معروف')}\n\n"

            timeline = "سجل التتبع:\n"
            for entry in history:
                timeline += f"- {entry['time']}: {entry['action']} (بواسطة: {entry['user']})\n"

            prompt = f"""
            أنت مساعد متخصص في تحليل المعاملات الإدارية. بناءً على المعلومات التالية، قدم:
            1. ملخصاً مختصراً للمعاملة.
            2. تقييماً للوضع الحالي (هل هناك تأخير، هل الإجراءات مناسبة).
            3. توصيات أو اقتراحات للمتابعة (إن وجدت).
            كن دقيقاً وموجزاً.

            معلومات المعاملة:
            {summary}

            {timeline}
            """
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "أنت محلل معاملات خبير."},
                    {"role": "user", "content": prompt}
                ],
                model=self.model,
                temperature=0.5,
                max_tokens=800
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"خطأ في تحليل المعاملة: {e}")
            return "عذراً، حدث خطأ أثناء تحليل المعاملة."
