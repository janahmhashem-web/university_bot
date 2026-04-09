import os
import re
import logging
from groq import Groq

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
            intent, params = self._understand_intent(user_message)
            data_context = await self._fetch_data_by_intent(intent, params, user_id)
            response = await self._generate_response(user_message, data_context, user_name)
            return response
        except Exception as e:
            logger.error(f"Groq error: {e}")
            return "عذراً، حدث خطأ في المعالجة."

    def _understand_intent(self, message):
        msg = message.lower()
        params = {}
        intent = "general"

        tid_match = re.search(r'MUT-\d{14}-\d{4}', message)
        if tid_match:
            params['transaction_id'] = tid_match.group(0)
            return "specific_transaction", params

        if any(word in msg for word in ['معاملتي', 'خاصتي', 'تابع معاملتي']):
            return "my_transaction", params

        if any(word in msg for word in ['إحصاء', 'إحصائيات', 'عدد', 'كم', 'stats', 'إجمالي', 'مجموع']):
            intent = "stats"
            if 'مكتملة' in msg or 'completed' in msg:
                params['status'] = 'مكتملة'
            elif 'قيد المعالجة' in msg or 'processing' in msg:
                params['status'] = 'قيد المعالجة'
            elif 'جديد' in msg or 'new' in msg:
                params['status'] = 'جديد'
            elif 'متأخرة' in msg or 'delayed' in msg:
                params['status'] = 'متأخرة'
            return intent, params

        dept_match = re.search(r'قسم\s+([^\s]+(?:\s+[^\s]+)?)', msg)
        if dept_match:
            params['department'] = dept_match.group(1).strip()
            return "department_transactions", params

        emp_match = re.search(r'موظف\s+([^\s]+(?:\s+[^\s]+)?)', msg)
        if emp_match:
            params['employee'] = emp_match.group(1).strip()
            return "employee_transactions", params

        if 'مكتملة' in msg and ('معاملة' in msg or 'المعاملات' in msg):
            params['status'] = 'مكتملة'
            return "status_transactions", params
        if 'قيد المعالجة' in msg:
            params['status'] = 'قيد المعالجة'
            return "status_transactions", params
        if 'جديد' in msg and ('معاملة' in msg or 'المعاملات' in msg):
            params['status'] = 'جديد'
            return "status_transactions", params
        if 'متأخرة' in msg:
            params['status'] = 'متأخرة'
            return "status_transactions", params

        if 'ابحث' in msg or 'بحث' in msg or 'find' in msg:
            search_match = re.search(r'(?:ابحث|بحث)\s+عن\s+(.+)', msg)
            if search_match:
                params['keyword'] = search_match.group(1).strip()
                return "search_transactions", params

        return intent, params

    async def _fetch_data_by_intent(self, intent, params, user_id):
        if not self.sheets_client:
            return "نظام قاعدة البيانات غير متصل حالياً."

        try:
            if intent == "specific_transaction":
                tid = params.get('transaction_id')
                data = self.sheets_client.get_latest_row_by_id_fast("manager", tid)
                if data:
                    return self._format_transaction_context(data)
                return f"لم أجد معاملة بالرقم {tid}."

            if intent == "my_transaction":
                tid = self._get_user_transaction_id(user_id)
                if tid:
                    data = self.sheets_client.get_latest_row_by_id_fast("manager", tid)
                    if data:
                        return self._format_transaction_context(data)
                    return "لم أجد معاملة مرتبطة بحسابك."
                return "لم يتم ربط حسابك بأي معاملة بعد. استخدم رابط البوت لربط حسابك."

            if intent == "stats":
                records = self.sheets_client.get_latest_transactions_fast("manager")
                total = len(records)
                status_filter = params.get('status')
                if status_filter:
                    filtered = [r for r in records if r.get('الحالة') == status_filter]
                    return f"عدد المعاملات ذات الحالة '{status_filter}' هو {len(filtered)} من إجمالي {total} معاملة."
                completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
                pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
                delayed = sum(1 for r in records if r.get('التأخير') == 'نعم')
                return (f"📊 إحصائيات المعاملات:\n"
                        f"- إجمالي المعاملات: {total}\n"
                        f"- مكتملة: {completed}\n"
                        f"- قيد المعالجة/جديد: {pending}\n"
                        f"- متأخرة: {delayed}")

            if intent == "department_transactions":
                dept = params.get('department')
                records = self.sheets_client.filter_transactions("manager", department=dept)
                if not records:
                    return f"لا توجد معاملات في قسم '{dept}'."
                sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} ({r.get('الحالة')})" for r in records[:5]])
                more = f"\n... و{len(records)-5} معاملات أخرى" if len(records) > 5 else ""
                return f"📁 المعاملات في قسم {dept}: ({len(records)} معاملة)\n{sample}{more}"

            if intent == "employee_transactions":
                emp = params.get('employee')
                records = self.sheets_client.filter_transactions("manager", employee=emp)
                if not records:
                    return f"لا توجد معاملات مسندة إلى '{emp}'."
                sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} ({r.get('الحالة')})" for r in records[:5]])
                more = f"\n... و{len(records)-5} معاملات أخرى" if len(records) > 5 else ""
                return f"👤 المعاملات المسندة إلى {emp}: ({len(records)} معاملة)\n{sample}{more}"

            if intent == "status_transactions":
                status = params.get('status')
                records = self.sheets_client.filter_transactions("manager", status=status)
                if not records:
                    return f"لا توجد معاملات بحالة '{status}'."
                sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} (قسم: {r.get('القسم')})" for r in records[:5]])
                more = f"\n... و{len(records)-5} معاملات أخرى" if len(records) > 5 else ""
                return f"📋 المعاملات بحالة {status}: ({len(records)} معاملة)\n{sample}{more}"

            if intent == "search_transactions":
                keyword = params.get('keyword', '').lower()
                records = self.sheets_client.get_latest_transactions_fast("manager")
                found = []
                for r in records:
                    if (keyword in str(r.get('ID', '')).lower() or
                        keyword in str(r.get('اسم صاحب المعاملة الثلاثي', '')).lower() or
                        keyword in str(r.get('القسم', '')).lower()):
                        found.append(r)
                if not found:
                    return f"لم أعثر على معاملات تحتوي على '{keyword}'."
                sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} ({r.get('الحالة')})" for r in found[:5]])
                more = f"\n... و{len(found)-5} معاملات أخرى" if len(found) > 5 else ""
                return f"🔎 نتائج البحث عن '{keyword}': ({len(found)} معاملة)\n{sample}{more}"

            # عام
            records = self.sheets_client.get_latest_transactions_fast("manager")
            total = len(records)
            completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
            pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
            delayed = sum(1 for r in records if r.get('التأخير') == 'نعم')
            recent = sorted(records, key=lambda x: x.get('آخر تعديل بتاريخ', ''), reverse=True)[:3]
            recent_text = "\n".join([f"- {r.get('ID')} - {r.get('اسم صاحب المعاملة الثلاثي')} ({r.get('الحالة')})" for r in recent])
            return (f"📊 نظرة عامة على النظام:\n"
                    f"- إجمالي المعاملات: {total}\n"
                    f"- مكتملة: {completed}\n"
                    f"- قيد المعالجة/جديد: {pending}\n"
                    f"- متأخرة: {delayed}\n\n"
                    f"أحدث المعاملات:\n{recent_text}\n\n"
                    f"يمكنك السؤال عن معاملة محددة برقمها، أو عن إحصائيات قسم معين، أو عن معاملات موظف معين.")
        except Exception as e:
            logger.error(f"خطأ في جلب البيانات: {e}")
            return "حدث خطأ أثناء جلب البيانات من النظام."

    async def _generate_response(self, user_message, data_context, user_name):
        system_prompt = (
            "أنت مساعد ذكي ومفيد للمعاملات الإدارية. لديك إمكانية الوصول إلى بيانات حقيقية عن المعاملات. "
            "استخدم البيانات المقدمة في السياق للإجابة بدقة ووضوح. أجب بالعربية بأسلوب مهذب."
        )
        user_prompt = f"المستخدم: {user_name}\nسؤال المستخدم: {user_message}\n\nالبيانات المتاحة:\n{data_context}\n\nقدم إجابة مفيدة."
        try:
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                model=self.model,
                temperature=0.7,
                max_tokens=800
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq API error: {e}")
            return f"عذراً، حدث خطأ. بناءً على البيانات المتاحة:\n{data_context}"

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
        lines.append(f"**المعاملة رقم {data.get('ID', 'غير معروف')}**")
        lines.append(f"- الاسم: {data.get('اسم صاحب المعاملة الثلاثي', 'غير معروف')}")
        lines.append(f"- القسم: {data.get('القسم', 'غير معروف')}")
        lines.append(f"- الحالة: {data.get('الحالة', 'غير معروف')}")
        lines.append(f"- الموظف المسؤول: {data.get('الموظف المسؤول', 'غير معروف')}")
        if data.get('التأخير') == 'نعم':
            lines.append("- ⚠️ **هذه المعاملة متأخرة**")
        if data.get('تاريخ التحويل'):
            lines.append(f"- تاريخ التحويل: {data.get('تاريخ التحويل')}")
        if data.get('المرافقات'):
            lines.append(f"- المرافقات: {data.get('المرافقات')}")
        return "\n".join(lines)

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
