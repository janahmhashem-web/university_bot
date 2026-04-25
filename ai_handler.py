import os
import re
import logging
import pickle
import numpy as np
from datetime import datetime
from collections import defaultdict
from groq import Groq
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline

logger = logging.getLogger(__name__)

class AIAssistant:
    """مساعد ذكي متكامل مع قدرات تعلم آلي وذاكرة سياقية"""
    
    def __init__(self, sheets_client=None):
        # Groq API
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        self.client = Groq(api_key=api_key)
        self.model = "llama-3.1-8b-instant"
        
        # Google Sheets client (لتخزين التعلم والذاكرة)
        self.sheets_client = sheets_client
        
        # إعدادات الذاكرة
        self.max_history = 5   # عدد الرسائل السابقة التي نتذكرها
        
        # مكونات التعلم الآلي
        self.classifier = None
        self.user_preferences = defaultdict(dict)  # {user_id: {'style': 'concise', ...}}
        self.intent_labels = ['stats', 'specific_transaction', 'general', 'admin_change', 'unknown']
        
        # تهيئة النموذج من البيانات المخزنة
        self._init_ml_model()
        
        # التأكد من وجود الأوراق اللازمة
        self._init_sheets()

    # ================== التهيئة وإنشاء الأوراق ==================
    def _init_sheets(self):
        if not self.sheets_client:
            return
        try:
            # الورقة الخاصة ببيانات التدريب على التصنيف
            ws = self.sheets_client.get_worksheet("ml_training_data")
            if not ws:
                ws = self.sheets_client.spreadsheet.add_worksheet(title="ml_training_data", rows=1, cols=3)
                ws.append_row(['text', 'label', 'timestamp'])
            # الورقة الخاصة بتقييمات المستخدمين
            ws = self.sheets_client.get_worksheet("ml_feedback")
            if not ws:
                ws = self.sheets_client.spreadsheet.add_worksheet(title="ml_feedback", rows=1, cols=6)
                ws.append_row(['timestamp', 'user_id', 'user_message', 'ai_response', 'helpful', 'processed'])
            # الورقة الخاصة بتفضيلات المستخدمين
            ws = self.sheets_client.get_worksheet("user_preferences")
            if not ws:
                ws = self.sheets_client.spreadsheet.add_worksheet(title="user_preferences", rows=1, cols=4)
                ws.append_row(['user_id', 'preference', 'value', 'updated_at'])
            # الورقة الخاصة بسجل المحادثات
            ws = self.sheets_client.get_worksheet("chat_history")
            if not ws:
                ws = self.sheets_client.spreadsheet.add_worksheet(title="chat_history", rows=1, cols=6)
                ws.append_row(['timestamp', 'user_id', 'user_name', 'user_message', 'ai_response', 'is_admin'])
        except Exception as e:
            logger.error(f"خطأ في إنشاء أوراق التعلم الآلي: {e}")

    def _init_ml_model(self):
        """تحميل بيانات التدريب وبناء النموذج"""
        try:
            if self.sheets_client:
                training_data = self._load_training_data()
                if training_data:
                    self._train_model(training_data)
                    logger.info(f"✅ تم تدريب النموذج على {len(training_data)} عينة")
                else:
                    # نموذج فارغ (سيُدرب لاحقاً)
                    self.classifier = Pipeline([
                        ('tfidf', TfidfVectorizer(max_features=1000)),
                        ('clf', MultinomialNB())
                    ])
                    logger.info("🆕 نموذج جديد (سيُدرب عند توفر بيانات)")
            else:
                self.classifier = None
        except Exception as e:
            logger.error(f"خطأ في تهيئة النموذج: {e}")
            self.classifier = None

    def _load_training_data(self):
        """تحميل البيانات المصنفة من Google Sheets"""
        try:
            ws = self.sheets_client.get_worksheet("ml_training_data")
            if not ws:
                return []
            records = ws.get_all_records()
            if not records or 'text' not in records[0] or 'label' not in records[0]:
                return []
            return [(r['text'], r['label']) for r in records if r.get('text') and r.get('label')]
        except Exception as e:
            logger.error(f"فشل تحميل بيانات التدريب: {e}")
            return []

    def _train_model(self, training_data):
        """تدريب نموذج تصنيف النية"""
        if not training_data:
            return
        texts, labels = zip(*training_data)
        self.classifier.fit(texts, labels)
        logger.info(f"✅ النموذج مدرب على {len(texts)} عينة")

    # ================== دوال التعلم الآلي ==================
    def predict_intent(self, message):
        """توقع نية الرسالة باستخدام النموذج المدرب"""
        if self.classifier is None:
            return 'general'
        try:
            pred = self.classifier.predict([message])[0]
            # إذا كان الناتج رقمياً (فهرس)
            if isinstance(pred, (int, np.integer)):
                return self.intent_labels[pred] if pred < len(self.intent_labels) else 'general'
            return pred
        except Exception as e:
            logger.error(f"خطأ في توقع النية: {e}")
            return 'general'

    def record_feedback(self, user_id, user_message, ai_response, helpful=True):
        """تسجيل تقييم المستخدم للرد (مفيد/غير مفيد) لتحسين النموذج"""
        if not self.sheets_client:
            return
        try:
            ws = self.sheets_client.get_worksheet("ml_feedback")
            if not ws:
                return
            now = datetime.now().isoformat()
            self.sheets_client.safe_append_row(ws, [now, user_id, user_message, ai_response, '1' if helpful else '0', '0'], batch=True)
            logger.debug(f"تم تسجيل تقييم المستخدم {user_id} (مفيد: {helpful})")
        except Exception as e:
            logger.error(f"فشل تسجيل التقييم: {e}")

    def update_user_preference(self, user_id, preference, value):
        """تحديث تفضيلات المستخدم (مثل نمط الرد، اللغة، إلخ)"""
        self.user_preferences[user_id][preference] = value
        self._save_user_preferences()

    def _save_user_preferences(self):
        """حفظ التفضيلات في Google Sheets"""
        if not self.sheets_client:
            return
        try:
            ws = self.sheets_client.get_worksheet("user_preferences")
            if not ws:
                return
            # تنظيف المحتوى القديم (اختياري: تحديث الصفوف بدلاً من مسح الكل)
            all_records = ws.get_all_values()
            if len(all_records) > 1:
                for i in range(len(all_records)-1, 0, -1):
                    ws.delete_row(i+1)
            now = datetime.now().isoformat()
            for uid, prefs in self.user_preferences.items():
                for pref, val in prefs.items():
                    ws.append_row([uid, pref, val, now])
        except Exception as e:
            logger.error(f"فشل حفظ تفضيلات المستخدم: {e}")

    def get_user_preference(self, user_id, preference, default=None):
        return self.user_preferences.get(user_id, {}).get(preference, default)

    # ================== دوال الذاكرة والسياق ==================
    def _get_conversation_history(self, user_id, limit=5):
        """استرجاع آخر محادثات المستخدم من Google Sheets"""
        if not self.sheets_client:
            return []
        try:
            ws = self.sheets_client.get_worksheet("chat_history")
            if not ws:
                return []
            records = ws.get_all_records()
            user_records = [r for r in records if str(r.get('user_id')) == str(user_id)]
            user_records.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            last_records = user_records[:limit][::-1]  # الأقدم أولاً
            history = []
            for rec in last_records:
                history.append({"role": "user", "content": rec.get('user_message', '')})
                history.append({"role": "assistant", "content": rec.get('ai_response', '')})
            return history
        except Exception as e:
            logger.error(f"خطأ في استرجاع محفوظات المحادثة: {e}")
            return []

    def _save_conversation(self, user_id, user_name, user_message, ai_response, is_admin):
        """تخزين المحادثة الحالية للاستفادة منها مستقبلاً"""
        if not self.sheets_client:
            return
        try:
            ws = self.sheets_client.get_worksheet("chat_history")
            if not ws:
                return
            now = datetime.now().isoformat()
            self.sheets_client.safe_append_row(ws, [now, user_id, user_name, user_message, ai_response, str(is_admin)], batch=True)
        except Exception as e:
            logger.error(f"فشل حفظ المحادثة: {e}")

    # ================== دوال فهم النية الأساسية ==================
    def _understand_intent(self, message, is_admin=False):
        """
        فهم نية المستخدم – يستخدم النموذج المدرب إن وُجد، وإلا يعتمد على القواعد.
        """
        msg = message.lower()
        params = {}
        intent = "general"

        # أولاً: محاولة استخدام النموذج المدرب (إن وُجد)
        if self.classifier:
            ml_intent = self.predict_intent(message)
            if ml_intent in self.intent_labels:
                # معالجة خاصة إذا كانت النية تتوافق مع أوامر المدير
                if ml_intent == "admin_change" and is_admin:
                    # محاولة استخراج رقم المعاملة والحالة الجديدة
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
                # يمكن إضافة المزيد من الخرائط

        # إذا لم يستخدم النموذج أو لم يتعرف، ننتقل إلى التحليل التقليدي (القواعد)
        tid_match = re.search(r'MUT-\d{14}-\d{4}', message)
        if tid_match:
            params['transaction_id'] = tid_match.group(0)
            return "specific_transaction", params

        if any(word in msg for word in ['معاملتي', 'خاصتي', 'تابع معاملتي']):
            return "my_transaction", params

        if any(word in msg for word in ['إحصاء', 'إحصائيات', 'عدد', 'كم', 'stats', 'إجمالي', 'مجموع']):
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

        if is_admin:
            # أوامر مدير باللغة الطبيعية
            if 'غير حالة' in msg:
                match = re.search(r'غير حالة\s+(\S+)\s+إلى\s+(\S+)', msg)
                if match:
                    params['transaction_id'] = match.group(1)
                    params['new_status'] = match.group(2)
                    return "admin_change_status", params
            if 'عين مسؤول' in msg or 'اسند إلى' in msg:
                match = re.search(r'(?:عين مسؤول|اسند إلى)\s+(\S+)\s+(.+)', msg)
                if match:
                    params['transaction_id'] = match.group(1)
                    params['employee'] = match.group(2).strip()
                    return "admin_assign_employee", params

        # بحث بقسم أو موظف
        dept_match = re.search(r'قسم\s+([^\s]+(?:\s+[^\s]+)?)', msg)
        if dept_match:
            params['department'] = dept_match.group(1).strip()
            return "department_transactions", params

        emp_match = re.search(r'موظف\s+([^\s]+(?:\s+[^\s]+)?)', msg)
        if emp_match:
            params['employee'] = emp_match.group(1).strip()
            return "employee_transactions", params

        # حالات محددة
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

        if 'ابحث' in msg or 'بحث' in msg:
            search_match = re.search(r'(?:ابحث|بحث)\s+عن\s+(.+)', msg)
            if search_match:
                params['keyword'] = search_match.group(1).strip()
                return "search_transactions", params

        return intent, params

    # ================== جلب البيانات من Sheets ==================
    async def _fetch_data_by_intent(self, intent, params, user_id, is_admin=False):
        if not self.sheets_client:
            return "نظام قاعدة البيانات غير متصل حالياً."

        try:
            if intent == "specific_transaction":
                tid = params.get('transaction_id')
                data = self.sheets_client.get_latest_row_by_id_fast("manager", tid)
                return self._format_transaction_context(data) if data else f"لم أجد معاملة بالرقم {tid}."

            if intent == "my_transaction":
                tid = self._get_user_transaction_id(user_id)
                if tid:
                    data = self.sheets_client.get_latest_row_by_id_fast("manager", tid)
                    return self._format_transaction_context(data) if data else "لم أجد معاملة مرتبطة بحسابك."
                return "لم يتم ربط حسابك بأي معاملة بعد. استخدم رابط البوت لربط حسابك."

            if intent == "admin_change_status" and is_admin:
                tid = params.get('transaction_id')
                new_status = params.get('new_status')
                if not tid or not new_status:
                    return "لم أفهم رقم المعاملة أو الحالة الجديدة."
                valid_status = ['جديد', 'قيد المعالجة', 'مكتملة', 'متأخرة']
                if new_status not in valid_status:
                    return f"الحالة `{new_status}` غير صالحة. الحالات المسموحة: {', '.join(valid_status)}"
                # تحديث الحالة
                success = self.sheets_client.update_transaction_field(tid, 'الحالة', new_status)
                if success:
                    self.sheets_client.add_history_entry(tid, f"تغيير الحالة إلى {new_status} (عن طريق AI)", "AI")
                    return f"✅ تم تغيير حالة المعاملة {tid} إلى **{new_status}**."
                else:
                    return f"❌ فشل تغيير حالة المعاملة {tid}. تأكد من الرقم."

            if intent == "admin_assign_employee" and is_admin:
                tid = params.get('transaction_id')
                emp = params.get('employee')
                if not tid or not emp:
                    return "لم أفهم رقم المعاملة أو اسم الموظف."
                success = self.sheets_client.update_transaction_field(tid, 'الموظف المسؤول', emp)
                if success:
                    self.sheets_client.add_history_entry(tid, f"تعيين {emp} كمسؤول (عن طريق AI)", "AI")
                    return f"✅ تم تعيين **{emp}** كمسؤول عن المعاملة {tid}."
                else:
                    return f"❌ فشل التعيين. تأكد من رقم المعاملة."

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

            # استعلام عام: عرض نظرة عامة (للمستخدمين العاديين)
            records = self.sheets_client.get_latest_transactions_fast("manager")
            total = len(records)
            completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
            pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
            delayed = sum(1 for r in records if r.get('التأخير') == 'نعم')
            if is_admin:
                # للمدير: إحصائيات أكثر تفصيلاً
                recent = sorted(records, key=lambda x: x.get('آخر تعديل بتاريخ', ''), reverse=True)[:5]
                recent_text = "\n".join([f"- {r.get('ID')} - {r.get('اسم صاحب المعاملة الثلاثي')} ({r.get('الحالة')})" for r in recent])
                return (f"📊 نظرة عامة على النظام (للمدير):\n- إجمالي: {total}\n- مكتملة: {completed}\n- قيد المعالجة/جديد: {pending}\n- متأخرة: {delayed}\n\nأحدث المعاملات:\n{recent_text}")
            else:
                # للمستخدم العادي: إحصائيات مختصرة
                return (f"📊 إحصائيات عامة:\n- إجمالي المعاملات: {total}\n- مكتملة: {completed}\n- قيد المعالجة/جديد: {pending}\n- متأخرة: {delayed}\n\nيمكنك استخدام الأزرار لتفاصيل معاملتك الخاصة.")

        except Exception as e:
            logger.error(f"خطأ في جلب البيانات: {e}")
            return "حدث خطأ أثناء جلب البيانات من النظام."

    # ================== توليد الرد باستخدام Groq ==================
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

    def _build_system_prompt(self, is_admin):
        base = (
            "أنت مساعد ذكي ومحترف لإدارة المعاملات الإدارية. لديك إمكانية الوصول إلى بيانات حقيقية عن المعاملات. "
            "أجب بالعربية الفصحى أو العامية المفهومة، بأسلوب مهذب ودقيق. "
            "لا تختلق معلومات غير موجودة في البيانات. إذا لم تعرف الإجابة، قل ذلك بوضوح. "
        )
        if is_admin:
            base += (
                "\nأنت تتحدث مع **المدير**. يمكنك تنفيذ أوامر مثل: 'غير حالة MUT-xxxx إلى مكتملة' أو 'عين مسؤول MUT-xxxx أحمد'. "
                "كن دقيقاً وأكد دائماً للمدير ما تم تنفيذه. استخدم البيانات التي أوصلناها لك."
            )
        else:
            base += (
                "\nأنت تتحدث مع **مستخدم عادي**. يمكنه فقط الاستعلام عن معاملته الشخصية أو الإحصائيات العامة. "
                "ساعده بلطف ولا تقم بأي تغييرات على البيانات."
            )
        return base

    def _build_user_prompt(self, user_message, data_context, history_context, user_name):
        prompt = f"المستخدم: {user_name}\n"
        if history_context:
            prompt += "**آخر محادثة:**\n"
            for msg in history_context:
                role = "المستخدم" if msg['role'] == 'user' else "المساعد"
                prompt += f"{role}: {msg['content']}\n"
        prompt += f"\n**سؤال المستخدم الحالي:** {user_message}\n\n"
        if data_context:
            prompt += f"**بيانات من النظام:**\n{data_context}\n\n"
        prompt += "قدم إجابة مفيدة وواضحة بناءً على البيانات أعلاه."
        return prompt

    # ================== الدالة الرئيسية للرد ==================
    async def get_response(self, user_message, user_id, user_name, is_admin=False):
        """الدالة الرئيسية التي يستدعيها البوت"""
        try:
            # 1. فهم النية
            intent, params = self._understand_intent(user_message, is_admin)
            logger.debug(f"Intent: {intent}, Params: {params}")
            
            # 2. جلب البيانات بناءً على النية
            data_context = await self._fetch_data_by_intent(intent, params, user_id, is_admin)
            
            # 3. استرجاع سجل المحادثات السابقة
            history_context = self._get_conversation_history(user_id, limit=self.max_history)
            
            # 4. توليد الرد باستخدام Groq (مع السياق)
            response = await self._generate_response(user_message, data_context, user_name, is_admin, history_context)
            
            # 5. حفظ المحادثة الحالية لاستخدامها مستقبلاً (للتعلم)
            self._save_conversation(user_id, user_name, user_message, response, is_admin)
            
            return response
        except Exception as e:
            logger.error(f"خطأ عام في get_response: {e}", exc_info=True)
            return "عذراً، حدث خطأ غير متوقع. يُرجى المحاولة مرة أخرى لاحقاً."

    # ================== دوال مساعدة ==================
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

    async def analyze_transaction(self, transaction_data, history):
        """تحليل معمق لمعاملة معينة (دالة منفصلة)"""
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
