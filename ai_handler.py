import os
import re
import logging
from datetime import datetime
from collections import defaultdict
from groq import Groq
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
import numpy as np
from cachetools import TTLCache
import time
import random

logger = logging.getLogger(__name__)

class AIAssistant:
    def __init__(self, sheets_client=None):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        self.client = Groq(api_key=api_key)
        self.model = "llama-3.1-8b-instant"
        self.sheets_client = sheets_client
        self.max_history = 5
        self.conversation_cache = TTLCache(maxsize=200, ttl=600)
        self.user_rate_limit = defaultdict(list)
        self.rate_limit_requests = 10
        self.rate_limit_period = 60

        self.classifier = None
        self.user_preferences = defaultdict(dict)
        self.intent_labels = ['stats', 'specific_transaction', 'general', 'admin_change', 'unknown']
        self._init_ml_model()
        self._init_sheets()

    def _init_sheets(self):
        if not self.sheets_client:
            return
        try:
            for sheet_name, headers in [
                ("ml_training_data", ["text", "label", "timestamp"]),
                ("ml_feedback", ["timestamp", "user_id", "user_message", "ai_response", "helpful", "processed"]),
                ("user_preferences", ["user_id", "preference", "value", "updated_at"])
            ]:
                ws = self.sheets_client.get_worksheet(sheet_name)
                if not ws:
                    ws = self.sheets_client.spreadsheet.add_worksheet(title=sheet_name, rows=1, cols=len(headers))
                    for col, header in enumerate(headers, 1):
                        ws.update_cell(1, col, header)
        except Exception as e:
            logger.error(f"خطأ في إنشاء أوراق التعلم الآلي: {e}")

    def _init_ml_model(self):
        try:
            if self.sheets_client:
                training_data = self._load_training_data()
                if training_data:
                    self._train_model(training_data)
                    logger.info(f"✅ تم تدريب النموذج على {len(training_data)} عينة")
                else:
                    self.classifier = Pipeline([
                        ('tfidf', TfidfVectorizer(max_features=1000)),
                        ('clf', MultinomialNB())
                    ])
            else:
                self.classifier = None
        except Exception as e:
            logger.error(f"خطأ في تهيئة النموذج: {e}")
            self.classifier = None

    def _load_training_data(self):
        try:
            ws = self.sheets_client.get_worksheet("ml_training_data")
            if not ws:
                return []
            records = ws.get_all_records()
            return [(r['text'], r['label']) for r in records if r.get('text') and r.get('label')]
        except Exception as e:
            logger.error(f"فشل تحميل بيانات التدريب: {e}")
            return []

    def _train_model(self, training_data):
        if not training_data:
            return
        texts, labels = zip(*training_data)
        self.classifier.fit(texts, labels)
        logger.info(f"✅ النموذج مدرب على {len(texts)} عينة")

    def predict_intent(self, message):
        if self.classifier is None:
            return 'general'
        try:
            pred = self.classifier.predict([message])[0]
            if isinstance(pred, (int, np.integer)):
                return self.intent_labels[pred] if pred < len(self.intent_labels) else 'general'
            return pred
        except Exception as e:
            logger.error(f"خطأ في توقع النية: {e}")
            return 'general'

    def record_feedback(self, user_id, user_message, ai_response, helpful=True):
        if not self.sheets_client:
            return
        try:
            ws = self.sheets_client.get_worksheet("ml_feedback")
            if not ws:
                return
            now = datetime.now().isoformat()
            ws.append_row([now, user_id, user_message, ai_response, '1' if helpful else '0', '0'])
        except Exception as e:
            logger.error(f"فشل تسجيل التقييم: {e}")

    def update_user_preference(self, user_id, preference, value):
        self.user_preferences[user_id][preference] = value
        self._save_user_preferences()

    def _save_user_preferences(self):
        if not self.sheets_client:
            return
        try:
            ws = self.sheets_client.get_worksheet("user_preferences")
            if not ws:
                return
            ws.clear()
            ws.append_row(['user_id', 'preference', 'value', 'updated_at'])
            now = datetime.now().isoformat()
            for uid, prefs in self.user_preferences.items():
                for pref, val in prefs.items():
                    ws.append_row([uid, pref, val, now])
        except Exception as e:
            logger.error(f"فشل حفظ تفضيلات المستخدم: {e}")

    def get_user_preference(self, user_id, preference, default=None):
        return self.user_preferences.get(user_id, {}).get(preference, default)

    def _get_or_create_session(self, user_id):
        if user_id not in self.conversation_cache:
            self.conversation_cache[user_id] = {
                'last_transaction_id': None,
                'last_transaction_data': None,
                'last_intent': None,
                'history': []
            }
        return self.conversation_cache[user_id]

    def _add_to_history(self, user_id, user_message, ai_response):
        session = self._get_or_create_session(user_id)
        session['history'].append({'role': 'user', 'content': user_message})
        session['history'].append({'role': 'assistant', 'content': ai_response})
        if len(session['history']) > 10:
            session['history'] = session['history'][-10:]

    def _get_conversation_history(self, user_id, limit=5):
        session = self._get_or_create_session(user_id)
        return session.get('history', [])[-limit*2:]

    def _check_rate_limit(self, user_id):
        now = time.time()
        timestamps = self.user_rate_limit[user_id]
        timestamps = [t for t in timestamps if now - t < self.rate_limit_period]
        self.user_rate_limit[user_id] = timestamps
        if len(timestamps) >= self.rate_limit_requests:
            return False
        timestamps.append(now)
        return True

    def _find_transaction_by_name(self, name):
        if not self.sheets_client:
            return None
        try:
            records = self.sheets_client.get_latest_transactions_fast("manager")
            name_clean = name.strip().lower()
            for r in records:
                if r.get('اسم صاحب المعاملة الثلاثي', '').strip().lower() == name_clean:
                    return r
            for r in records:
                if name_clean in r.get('اسم صاحب المعاملة الثلاثي', '').strip().lower():
                    return r
            return None
        except Exception as e:
            logger.error(f"خطأ في البحث باسم المعاملة: {e}")
            return None

    def _extract_transaction_id_or_name(self, message):
        tid_match = re.search(r'MUT-\d{14}-\d{4}', message)
        if tid_match:
            return tid_match.group(0), None
        name_match = re.search(r'معاملة\s+([^\s]+(?:\s+[^\s]+){0,4})', message)
        if name_match:
            return None, name_match.group(1).strip()
        return None, None

    def _understand_intent(self, message, is_admin=False):
        msg = message.lower()
        params = {}
        intent = "general"

        if self.classifier:
            ml_intent = self.predict_intent(message)
            if ml_intent in self.intent_labels:
                if ml_intent == "admin_change" and is_admin:
                    match = re.search(r'(MUT-\d{14}-\d{4})', message)
                    if match:
                        params['transaction_id'] = match.group(0)
                    status_match = re.search(r'(مكتملة|قيد المعالجة|جديد|متأخرة)', msg)
                    if status_match:
                        params['new_status'] = status_match.group(0)
                    return "admin_change_status", params
                elif ml_intent == "specific_transaction":
                    tid = re.search(r'MUT-\d{14}-\d{4}', message)
                    if tid:
                        params['transaction_id'] = tid.group(0)
                        return "specific_transaction", params
                elif ml_intent == "stats":
                    return "stats", params

        if any(word in msg for word in ['الحالة', 'حالة']):
            return "ask_status", params
        if any(word in msg for word in ['المسؤول', 'موظف']):
            return "ask_employee", params
        if any(word in msg for word in ['متأخرة', 'تأخير']):
            return "ask_delayed", params

        if is_admin:
            if 'غير حالة' in msg:
                match = re.search(r'غير حالة\s+(\S+)\s+إلى\s+(\S+)', msg)
                if match:
                    params['transaction_id'] = match.group(1)
                    params['new_status'] = match.group(2)
                    return "admin_change_status", params
            if 'عين مسؤول' in msg:
                match = re.search(r'(?:عين مسؤول|اسند إلى)\s+(\S+)\s+(.+)', msg)
                if match:
                    params['transaction_id'] = match.group(1)
                    params['employee'] = match.group(2).strip()
                    return "admin_assign_employee", params

        tid_match = re.search(r'MUT-\d{14}-\d{4}', message)
        if tid_match:
            params['transaction_id'] = tid_match.group(0)
            return "specific_transaction", params

        if any(word in msg for word in ['معاملتي', 'خاصتي']):
            return "my_transaction", params

        if any(word in msg for word in ['إحصاء', 'إحصائيات', 'عدد', 'كم', 'stats']):
            intent = "stats"
            if 'مكتملة' in msg:
                params['status'] = 'مكتملة'
            elif 'قيد المعالجة' in msg:
                params['status'] = 'قيد المعالجة'
            elif 'جديد' in msg:
                params['status'] = 'جديد'
            elif 'متأخرة' in msg:
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

        return intent, params

    async def _fetch_data_by_intent(self, intent, params, user_id, is_admin=False):
        if not self.sheets_client:
            return "نظام قاعدة البيانات غير متصل حالياً."

        session = self._get_or_create_session(user_id)
        transaction_id = params.get('transaction_id')
        transaction_name = params.get('transaction_name')

        if not transaction_id and not transaction_name:
            if session.get('last_transaction_id'):
                transaction_id = session['last_transaction_id']
            else:
                return "لم أحدد أي معاملة. يرجى ذكر رقم المعاملة أو الاسم الكامل."

        if transaction_name and not transaction_id:
            data = self._find_transaction_by_name(transaction_name)
            if not data:
                return f"لم أجد معاملة باسم '{transaction_name}'."
            transaction_id = data.get('ID')
            session['last_transaction_data'] = data
            session['last_transaction_id'] = transaction_id
        elif transaction_id:
            data = self.sheets_client.get_latest_row_by_id_fast("manager", transaction_id)
            if data:
                session['last_transaction_data'] = data
                session['last_transaction_id'] = transaction_id
            else:
                return f"لم أجد معاملة بالرقم {transaction_id}."

        data = session.get('last_transaction_data')
        if not data:
            return "حدث خطأ في استرجاع بيانات المعاملة."

        context = {
            "id": data.get('ID', 'غير معروف'),
            "name": data.get('اسم صاحب المعاملة الثلاثي', 'غير معروف'),
            "department": data.get('القسم', 'غير معروف'),
            "status": data.get('الحالة', 'غير معروف'),
            "employee": data.get('الموظف المسؤول', 'غير معروف'),
            "current_location": data.get('المؤسسة الحالية') or data.get('مرحلة') or 'غير معروف',
            "transfer_date": data.get('تاريخ التحويل', 'غير معروف'),
            "reason": data.get('سبب التحويل', 'غير معروف'),
            "last_action": data.get('آخر إجراء', 'غير معروف'),
            "last_modified": data.get('آخر تعديل بتاريخ', 'غير معروف'),
            "is_delayed": 'نعم' if data.get('التأخير') == 'نعم' else 'لا',
            "priority": data.get('الأولوية', 'عادية'),
            "attachments": data.get('المرافقات', ''),
            "notes": data.get('ملاحظات إضافية', '')
        }

        if intent in ["specific_transaction", "my_transaction"]:
            return self._format_transaction_context(data)
        if intent == "ask_status":
            return f"حالة معاملة {data.get('اسم صاحب المعاملة الثلاثي')} (رقم {transaction_id}) هي **{data.get('الحالة', 'غير معروف')}**."
        if intent == "ask_employee":
            return f"المسؤول عن معاملة {data.get('اسم صاحب المعاملة الثلاثي')} هو **{data.get('الموظف المسؤول', 'غير معروف')}**."
        if intent == "ask_delayed":
            if data.get('التأخير') == 'نعم':
                return f"⚠️ معاملة {data.get('اسم صاحب المعاملة الثلاثي')} **متأخرة** حالياً."
            else:
                return f"✅ معاملة {data.get('اسم صاحب المعاملة الثلاثي')} **غير متأخرة**."

        if intent == "admin_change_status" and is_admin:
            tid = transaction_id
            new_status = params.get('new_status')
            if not new_status:
                return "لم أفهم الحالة الجديدة."
            valid = ['جديد', 'قيد المعالجة', 'مكتملة', 'متأخرة']
            if new_status not in valid:
                return f"الحالة `{new_status}` غير صالحة."
            success = self.sheets_client.update_transaction_field(tid, 'الحالة', new_status)
            if success:
                self.sheets_client.add_history_entry(tid, f"تغيير الحالة إلى {new_status} (عن طريق AI)", "AI")
                return f"✅ تم تغيير حالة المعاملة {tid} إلى **{new_status}**."
            else:
                return f"❌ فشل تغيير حالة المعاملة {tid}."

        if intent == "admin_assign_employee" and is_admin:
            tid = transaction_id
            emp = params.get('employee')
            if not emp:
                return "لم أفهم اسم الموظف."
            success = self.sheets_client.update_transaction_field(tid, 'الموظف المسؤول', emp)
            if success:
                self.sheets_client.add_history_entry(tid, f"تعيين {emp} كمسؤول (عن طريق AI)", "AI")
                return f"✅ تم تعيين **{emp}** كمسؤول عن المعاملة {tid}."
            else:
                return f"❌ فشل التعيين."

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
            return (f"📊 إحصائيات المعاملات:\n- إجمالي: {total}\n- مكتملة: {completed}\n- قيد المعالجة/جديد: {pending}\n- متأخرة: {delayed}")

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
                return f"لا توجد معاملات للموظف '{emp}'."
            sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} ({r.get('الحالة')})" for r in records[:5]])
            more = f"\n... و{len(records)-5} معاملات أخرى" if len(records) > 5 else ""
            return f"👤 معاملات الموظف {emp}: ({len(records)} معاملة)\n{sample}{more}"

        if intent == "status_transactions":
            status = params.get('status')
            records = self.sheets_client.filter_transactions("manager", status=status)
            if not records:
                return f"لا توجد معاملات بحالة '{status}'."
            sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} (قسم: {r.get('القسم')})" for r in records[:5]])
            more = f"\n... و{len(records)-5} معاملات أخرى" if len(records) > 5 else ""
            return f"📋 المعاملات بحالة {status}: ({len(records)} معاملة)\n{sample}{more}"

        return self._format_transaction_context(data)

    def _format_transaction_context(self, data):
        if not data:
            return "المعاملة غير موجودة."
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

    def _build_system_prompt(self, is_admin):
        base = (
            "أنت مساعد ذكي ومحترف متخصص في متابعة المعاملات الإدارية.\n"
            "أجب بالعربية الفصحى بأسلوب مهذب ودقيق.\n"
            "استخدم البيانات الحقيقية فقط، ولا تختلق معلومات.\n"
            "إذا لم تعرف الإجابة، قل 'ليس لدي معلومات كافية' واقترح التواصل مع الدعم.\n"
            "استخدم Markdown للتنظيم (عناوين بـ **، نقاط بـ •، تأكيد بـ ✅ أو ⚠️)."
        )
        if is_admin:
            base += "\nأنت تتحدث مع المدير. يمكنك تنفيذ تغييرات مثل تغيير الحالة أو تعيين مسؤول."
        else:
            base += "\nأنت تتحدث مع مستخدم عادي. ساعده في متابعة معاملته."
        return base

    def _build_user_prompt(self, user_message, data_context, history_context, user_name):
        prompt = f"المستخدم: {user_name}\n"
        if history_context:
            prompt += "\nآخر محادثة:\n"
            for msg in history_context:
                role = "المستخدم" if msg['role'] == 'user' else "المساعد"
                prompt += f"{role}: {msg['content']}\n"
        prompt += f"\nسؤال المستخدم الحالي: {user_message}\n\n"
        if data_context:
            prompt += f"بيانات من النظام:\n{data_context}\n"
        return prompt

    async def _generate_response(self, user_message, data_context, user_name, is_admin=False, history_context=None):
        system_prompt = self._build_system_prompt(is_admin)
        user_prompt = self._build_user_prompt(user_message, data_context, history_context, user_name)
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
            return f"عذراً، حدث خطأ أثناء توليد الرد. لكن بناءً على البيانات المتاحة:\n{data_context}"

    async def get_response(self, user_message, user_id, user_name, is_admin=False):
        if not self._check_rate_limit(user_id):
            return "⚠️ عذراً، تجاوزت عدد الطلبات المسموح بها في الدقيقة. يرجى الانتظار قليلاً."

        try:
            tid, name = self._extract_transaction_id_or_name(user_message)
            params = {}
            if tid:
                params['transaction_id'] = tid
            if name:
                params['transaction_name'] = name

            intent, extra_params = self._understand_intent(user_message, is_admin)
            params.update(extra_params)

            data_context = await self._fetch_data_by_intent(intent, params, user_id, is_admin)
            if isinstance(data_context, str):
                return data_context

            history_context = self._get_conversation_history(user_id, limit=self.max_history)
            response = await self._generate_response(user_message, data_context, user_name, is_admin, history_context)

            self._add_to_history(user_id, user_message, response)
            return response
        except Exception as e:
            logger.error(f"خطأ في get_response: {e}", exc_info=True)
            return "⚠️ حدث عطل تقني مفاجئ. يرجى استخدام زر 'تواصل مع فريق العمل' لإبلاغنا."

    def train_model_from_feedback(self):
        try:
            if not self.sheets_client:
                return False
            training_data = self._load_training_data()
            if training_data:
                self._train_model(training_data)
                return True
            return False
        except Exception as e:
            logger.error(f"فشل تدريب النموذج: {e}")
            return False

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
            أنت محلل معاملات خبير. بناءً على المعلومات التالية، قدم:
            1. ملخصاً مختصراً للمعاملة.
            2. تقييماً للوضع الحالي (هل هناك تأخير، هل الإجراءات مناسبة).
            3. توصيات أو اقتراحات للمتابعة.

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
