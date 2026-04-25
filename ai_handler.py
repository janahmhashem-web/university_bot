import os
import re
import logging
from datetime import datetime
from collections import defaultdict
from groq import Groq
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# محاولة استيراد sklearn (اختياري، إن لم يكن موجوداً نستمر بدون نموذج)
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.naive_bayes import MultinomialNB
    from sklearn.pipeline import Pipeline
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("scikit-learn غير مثبت، سيُعتمد على التحليل بالقواعد فقط")

logger = logging.getLogger(__name__)

class AIAssistant:
    """مساعد ذكي متكامل – يتذكر المحادثة، يفهم الأسئلة المتابعة، يرسل أزراراً تفاعلية"""

    def __init__(self, sheets_client=None):
        # Groq API
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not set")
        self.client = Groq(api_key=api_key)
        self.model = "llama-3.1-8b-instant"

        self.sheets_client = sheets_client

        # إعدادات الذاكرة المؤقتة (جلسات المستخدمين)
        self.user_sessions = {}       # {user_id: {'last_tid': str, 'last_data': dict, 'last_intent': str}}
        self.max_history = 5          # عدد المحادثات السابقة المسترجعة للسياق

        # تفضيلات المستخدمين (اختياري)
        self.user_preferences = defaultdict(dict)

        # نموذج التعلم الآلي إن توفر
        self.classifier = None
        if SKLEARN_AVAILABLE:
            self._init_ml_model()

        # التأكد من وجود الأوراق اللازمة في Google Sheets
        self._init_sheets()

    # ================== التهيئة وإنشاء الأوراق ==================
    def _init_sheets(self):
        if not self.sheets_client:
            return
        try:
            sheets = [
                ("ml_training_data", ['text', 'label', 'timestamp']),
                ("ml_feedback", ['timestamp', 'user_id', 'user_message', 'ai_response', 'helpful', 'processed']),
                ("user_preferences", ['user_id', 'preference', 'value', 'updated_at']),
                ("chat_history", ['timestamp', 'user_id', 'user_name', 'user_message', 'ai_response', 'is_admin']),
            ]
            for name, cols in sheets:
                ws = self.sheets_client.get_worksheet(name)
                if not ws:
                    ws = self.sheets_client.spreadsheet.add_worksheet(title=name, rows=1, cols=len(cols))
                    ws.append_row(cols)
        except Exception as e:
            logger.error(f"خطأ في إنشاء الأوراق: {e}")

    def _init_ml_model(self):
        """تحميل بيانات التدريب وبناء النموذج إن وجدت"""
        try:
            if self.sheets_client:
                training = self._load_training_data()
                if training:
                    self._train_model(training)
                    logger.info(f"✅ تم تدريب النموذج على {len(training)} عينة")
                else:
                    self.classifier = Pipeline([
                        ('tfidf', TfidfVectorizer(max_features=500)),
                        ('clf', MultinomialNB())
                    ])
        except Exception as e:
            logger.error(f"فشل تهيئة النموذج: {e}")

    def _load_training_data(self):
        try:
            ws = self.sheets_client.get_worksheet("ml_training_data")
            if not ws:
                return []
            recs = ws.get_all_records()
            return [(r['text'], r['label']) for r in recs if r.get('text') and r.get('label')]
        except Exception:
            return []

    def _train_model(self, data):
        texts, labels = zip(*data)
        self.classifier.fit(texts, labels)

    # ================== دوال إدارة الجلسة والذاكرة ==================
    def _get_session(self, user_id):
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {
                'last_tid': None,
                'last_data': None,
                'last_intent': None
            }
        return self.user_sessions[user_id]

    def _update_session(self, user_id, tid=None, data=None, intent=None):
        sess = self._get_session(user_id)
        if tid:
            sess['last_tid'] = tid
        if data:
            sess['last_data'] = data
        if intent:
            sess['last_intent'] = intent

    def _find_transaction_by_name(self, name):
        """يبحث عن معاملة باسم صاحبها – تطابق تام ثم جزئي"""
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
            logger.error(f"بحث بالاسم فشل: {e}")
            return None

    def _extract_tid_or_name(self, text):
        """استخراج رقم معاملة MUT-xxxx أو اسم (بعد كلمة 'معاملة')"""
        tid = re.search(r'MUT-\d{14}-\d{4}', text)
        if tid:
            return tid.group(), None
        match = re.search(r'معاملة\s+([^\s]+(?:\s+[^\s]+){0,4})', text)
        if match:
            return None, match.group(1).strip()
        return None, None

    # ================== دوال بناء الأزرار التفاعلية ==================
    def _user_keyboard(self):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 تفاصيل معاملتي", callback_data="my_id")],
            [InlineKeyboardButton("📜 سجل تتبع معاملتي", callback_data="my_history")],
            [InlineKeyboardButton("📱 تعليمات QR", callback_data="cmd_qr")],
            [InlineKeyboardButton("💬 الدعم الفني", callback_data="cmd_support")],
            [InlineKeyboardButton("🤖 أسأل المساعد", callback_data="cmd_ai_chat")],
        ])

    def _admin_keyboard(self):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 إحصائيات متقدمة", callback_data="cmd_advanced_stats")],
            [InlineKeyboardButton("🏢 إحصائيات الأقسام", callback_data="cmd_dept_stats")],
            [InlineKeyboardButton("👥 إحصائيات الموظفين", callback_data="cmd_emp_stats")],
            [InlineKeyboardButton("📈 توزيع الحالات", callback_data="cmd_status_dist")],
            [InlineKeyboardButton("📋 آخر 10 معاملات", callback_data="cmd_recent")],
            [InlineKeyboardButton("🔍 بحث متقدم", callback_data="cmd_advanced_search")],
            [InlineKeyboardButton("⚙️ إدارة المعاملات", callback_data="cmd_admin_manage")],
        ])

    def _full_keyboard(self, is_admin):
        buttons = self._user_keyboard().inline_keyboard[:]
        if is_admin:
            buttons.extend(self._admin_keyboard().inline_keyboard)
        return InlineKeyboardMarkup(buttons)

    def _search_keyboard(self):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔎 بحث برقم المعاملة", callback_data="cmd_id")],
            [InlineKeyboardButton("🔎 بحث بالاسم", callback_data="cmd_search")],
            [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="cmd_back")],
        ])

    # ================== فهم النية (بالقواعد + نموذج إن وجد) ==================
    def _understand_intent(self, text, is_admin=False):
        msg = text.lower()
        params = {}
        intent = "general"

        # 1. طلب أزرار أو مساعدة
        if any(w in msg for w in ['ازرار', 'أزرار', 'buttons', 'كيف أتعامل', 'كيف استخدم', 'مساعدة', 'help']):
            return "request_buttons", params
        if any(w in msg for w in ['زر البحث', 'بحث', 'ابحث']):
            return "request_search_buttons", params
        if any(w in msg for w in ['سجل التتبع', 'history', 'أريد السجل']):
            return "request_history_buttons", params

        # 2. استخدام النموذج المدرب إن وجد
        if SKLEARN_AVAILABLE and self.classifier:
            try:
                pred = self.classifier.predict([text])[0]
                if pred == "request_buttons":
                    return "request_buttons", params
            except:
                pass

        # 3. أسئلة متابعة (بدون ذكر معاملة جديدة)
        if any(w in msg for w in ['الحالة', 'حالة', 'status']):
            return "ask_status", params
        if any(w in msg for w in ['وصلت', 'أين', 'مرحلة', 'تحولت', 'مكان']):
            return "ask_location", params
        if any(w in msg for w in ['المسؤول', 'موظف', 'مسؤول']):
            return "ask_employee", params
        if any(w in msg for w in ['متأخرة', 'تأخير']):
            return "ask_delayed", params

        # 4. أوامر المدير
        if is_admin:
            if 'غير حالة' in msg:
                m = re.search(r'غير حالة\s+(\S+)\s+إلى\s+(\S+)', msg)
                if m:
                    params['tid'] = m.group(1)
                    params['new_status'] = m.group(2)
                    return "admin_change_status", params
            if 'عين مسؤول' in msg or 'اسند إلى' in msg:
                m = re.search(r'(?:عين مسؤول|اسند إلى)\s+(\S+)\s+(.+)', msg)
                if m:
                    params['tid'] = m.group(1)
                    params['employee'] = m.group(2).strip()
                    return "admin_assign_employee", params

        # 5. استعلام بمعاملة محددة (رقم أو اسم)
        tid, name = self._extract_tid_or_name(text)
        if tid:
            params['tid'] = tid
            return "specific_transaction", params
        if name:
            params['name'] = name
            return "specific_transaction", params

        # 6. معاملتي الخاصة
        if any(w in msg for w in ['معاملتي', 'خاصتي']):
            return "my_transaction", params

        # 7. إحصائيات
        if any(w in msg for w in ['إحصاء', 'عدد', 'كم', 'stats']):
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

        # 8. تصفية حسب قسم أو موظف أو حالة
        dept = re.search(r'قسم\s+([^\s]+(?:\s+[^\s]+)?)', msg)
        if dept:
            params['department'] = dept.group(1).strip()
            return "department_transactions", params
        emp = re.search(r'موظف\s+([^\s]+(?:\s+[^\s]+)?)', msg)
        if emp:
            params['employee'] = emp.group(1).strip()
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

        # 9. بحث مفتوح
        if 'ابحث' in msg or 'بحث' in msg:
            m = re.search(r'(?:ابحث|بحث)\s+عن\s+(.+)', msg)
            if m:
                params['keyword'] = m.group(1).strip()
                return "search_transactions", params

        return intent, params

    # ================== جلب البيانات وتنفيذ الإجراءات ==================
    async def _fetch_action(self, intent, params, user_id, is_admin):
        """ترجع إما نصاً أو قاموساً {'type':'buttons', 'text':..., 'reply_markup':...}"""

        # معالجة طلبات الأزرار أولاً
        if intent == "request_buttons":
            return {
                "type": "buttons",
                "text": "👇 إليك الأزرار المتاحة يمكنك الضغط عليها للوصول السريع.",
                "reply_markup": self._full_keyboard(is_admin)
            }
        if intent == "request_search_buttons":
            return {
                "type": "buttons",
                "text": "🔍 اختر طريقة البحث:",
                "reply_markup": self._search_keyboard()
            }
        if intent == "request_history_buttons":
            sess = self._get_session(user_id)
            tid = sess.get('last_tid')
            if not tid:
                return "لم أحدد معاملة لعرض سجلها. يرجى أولاً الاستعلام عن معاملة (بذكر اسمها أو رقمها)."
            return {
                "type": "buttons",
                "text": f"📜 سجل التتبع للمعاملة {tid}:",
                "reply_markup": InlineKeyboardMarkup([
                    [InlineKeyboardButton("عرض السجل", callback_data=f"history_{tid}")],
                    [InlineKeyboardButton("🔙 العودة", callback_data="cmd_qr")]
                ])
            }

        # التعرف على المعاملة المستخدمة (جديدة أو من الجلسة)
        sess = self._get_session(user_id)
        tid = params.get('tid')
        name = params.get('name')
        data = None

        if tid:
            data = self.sheets_client.get_latest_row_by_id_fast("manager", tid) if self.sheets_client else None
        elif name:
            data = self._find_transaction_by_name(name)
            if data:
                tid = data.get('ID')
        else:
            # استخدم آخر معاملة من الجلسة
            tid = sess.get('last_tid')
            if tid:
                data = sess.get('last_data')
            else:
                return "لم تحدّد معاملة. يرجى كتابة رقم المعاملة (MUT-xxxx) أو الاسم الكامل لصاحبها."

        if not data and tid:
            data = self.sheets_client.get_latest_row_by_id_fast("manager", tid) if self.sheets_client else None
        if not data:
            return f"لم أجد معاملة بالرقم {tid or name}."

        # تحديث الجلسة بالمعاملة الحالية
        self._update_session(user_id, tid=tid, data=data, intent=intent)

        # تنفيذ الإجراء حسب النية
        if intent == "specific_transaction":
            return self._format_transaction_context(data)

        if intent == "my_transaction":
            # حصلنا على معاملة المستخدم إن كانت مرتبطة (قد لا تكون موجودة في الجلسة بعد)
            my_tid = self._get_user_transaction_id(user_id)
            if my_tid and my_tid != tid:
                my_data = self.sheets_client.get_latest_row_by_id_fast("manager", my_tid) if self.sheets_client else None
                if my_data:
                    self._update_session(user_id, tid=my_tid, data=my_data)
                    return self._format_transaction_context(my_data)
            return self._format_transaction_context(data)

        if intent == "ask_status":
            return f"حالة المعاملة: **{data.get('الحالة', 'غير معروف')}**"
        if intent == "ask_location":
            loc = data.get('المؤسسة الحالية') or data.get('مرحلة') or data.get('آخر إجراء') or 'غير معروف'
            return f"المعاملة الآن في: **{loc}**"
        if intent == "ask_employee":
            emp = data.get('الموظف المسؤول', 'غير معروف')
            return f"المسؤول عن المعاملة: **{emp}**"
        if intent == "ask_delayed":
            return f"**المعاملة {'متأخرة' if data.get('التأخير') == 'نعم' else 'غير متأخرة'}**"

        if intent == "admin_change_status" and is_admin:
            new_status = params.get('new_status')
            if not new_status:
                return "يرجى تحديد الحالة الجديدة (جديد, قيد المعالجة, مكتملة, متأخرة)."
            valid = ['جديد', 'قيد المعالجة', 'مكتملة', 'متأخرة']
            if new_status not in valid:
                return f"الحالة غير صالحة. اختر من {', '.join(valid)}"
            success = self._update_transaction_field(tid, 'الحالة', new_status)
            if success:
                self._add_history(tid, f"تغيير الحالة إلى {new_status} (عن طريق AI)", "AI")
                # تحديث الجلسة بالبيانات الجديدة
                new_data = self.sheets_client.get_latest_row_by_id_fast("manager", tid)
                if new_data:
                    self._update_session(user_id, data=new_data)
                return f"✅ تم تغيير حالة المعاملة إلى **{new_status}**."
            return f"❌ فشل تغيير الحالة. تأكد من الرقم."

        if intent == "admin_assign_employee" and is_admin:
            emp = params.get('employee')
            if not emp:
                return "يرجى ذكر اسم الموظف."
            success = self._update_transaction_field(tid, 'الموظف المسؤول', emp)
            if success:
                self._add_history(tid, f"تعيين {emp} كمسؤول (عن طريق AI)", "AI")
                new_data = self.sheets_client.get_latest_row_by_id_fast("manager", tid)
                if new_data:
                    self._update_session(user_id, data=new_data)
                return f"✅ تم تعيين **{emp}** كمسؤول عن المعاملة."
            return f"❌ فشل التعيين."

        # باقي النيات (إحصائيات، تصفية، بحث) – لا تحتاج سياق معاملة
        if intent == "stats":
            records = self.sheets_client.get_latest_transactions_fast("manager") if self.sheets_client else []
            total = len(records)
            if params.get('status'):
                filtered = [r for r in records if r.get('الحالة') == params['status']]
                return f"عدد المعاملات ذات الحالة '{params['status']}' هو {len(filtered)} من إجمالي {total}."
            completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
            pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
            delayed = sum(1 for r in records if r.get('التأخير') == 'نعم')
            return f"📊 إجمالي: {total}\nمكتملة: {completed}\nقيد المعالجة/جديد: {pending}\nمتأخرة: {delayed}"

        if intent == "department_transactions":
            dept = params.get('department')
            records = self.sheets_client.filter_transactions("manager", department=dept) if self.sheets_client else []
            if not records:
                return f"لا توجد معاملات في قسم {dept}."
            sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} ({r.get('الحالة')})" for r in records[:5]])
            more = f"\n... و{len(records)-5} أخرى" if len(records) > 5 else ""
            return f"📁 قسم {dept} ({len(records)} معاملة):\n{sample}{more}"

        if intent == "employee_transactions":
            emp = params.get('employee')
            records = self.sheets_client.filter_transactions("manager", employee=emp) if self.sheets_client else []
            if not records:
                return f"لا توجد معاملات للموظف {emp}."
            sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} ({r.get('الحالة')})" for r in records[:5]])
            more = f"\n... و{len(records)-5} أخرى" if len(records) > 5 else ""
            return f"👤 معاملات {emp} ({len(records)}):\n{sample}{more}"

        if intent == "status_transactions":
            status = params.get('status')
            records = self.sheets_client.filter_transactions("manager", status=status) if self.sheets_client else []
            if not records:
                return f"لا توجد معاملات بحالة {status}."
            sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} (قسم: {r.get('القسم')})" for r in records[:5]])
            more = f"\n... و{len(records)-5} أخرى" if len(records) > 5 else ""
            return f"📋 حالة {status} ({len(records)}):\n{sample}{more}"

        if intent == "search_transactions":
            kw = params.get('keyword', '').lower()
            records = self.sheets_client.get_latest_transactions_fast("manager") if self.sheets_client else []
            found = []
            for r in records:
                if (kw in str(r.get('ID', '')).lower() or
                    kw in str(r.get('اسم صاحب المعاملة الثلاثي', '')).lower() or
                    kw in str(r.get('القسم', '')).lower()):
                    found.append(r)
            if not found:
                return f"لم أعثر على معاملات تحتوي '{kw}'."
            sample = "\n".join([f"- {r.get('ID')}: {r.get('اسم صاحب المعاملة الثلاثي')} ({r.get('الحالة')})" for r in found[:5]])
            more = f"\n... و{len(found)-5} أخرى" if len(found) > 5 else ""
            return f"🔎 نتائج البحث عن '{kw}' ({len(found)}):\n{sample}{more}"

        # افتراضي: إحصائيات عامة مختصرة
        return "يمكنك سؤالي عن إحصاءات المعاملات، أو البحث باسم معاملة، أو استخدام الأزرار."

    # ================== دوال مساعدة للتحديث والتاريخ ==================
    def _update_transaction_field(self, tid, field, value):
        """تحديث حقل معين في ورقة manager (يجب إضافته في sheets.py، أو ننفذه مباشرة)"""
        if not self.sheets_client:
            return False
        try:
            ws = self.sheets_client.get_worksheet('manager')
            if not ws:
                return False
            headers = ws.row_values(1)
            if field not in headers:
                return False
            col = headers.index(field) + 1
            # البحث عن الصف
            all_rows = ws.get_all_values()
            row_num = None
            for i, row in enumerate(all_rows):
                if i == 0:
                    continue
                if len(row) > 0 and row[0] == tid:  # العمود الأول عادةً هو ID
                    row_num = i + 1
                    break
            if not row_num:
                return False
            ws.update_cell(row_num, col, value)
            return True
        except Exception as e:
            logger.error(f"فشل تحديث الحقل: {e}")
            return False

    def _add_history(self, tid, action, user):
        if self.sheets_client:
            self.sheets_client.add_history_entry(tid, action, user)

    def _get_user_transaction_id(self, chat_id):
        if not self.sheets_client:
            return None
        try:
            ws = self.sheets_client.get_worksheet("users")
            if not ws:
                return None
            recs = ws.get_all_records()
            for r in recs:
                if str(r.get('chat_id')) == str(chat_id):
                    return r.get('transaction_id')
        except Exception as e:
            logger.error(f"خطأ في جلب معاملة المستخدم: {e}")
        return None

    def _format_transaction_context(self, data):
        lines = [f"**المعاملة {data.get('ID', 'غير معروف')}**"]
        lines.append(f"- الاسم: {data.get('اسم صاحب المعاملة الثلاثي', 'غير معروف')}")
        lines.append(f"- القسم: {data.get('القسم', 'غير معروف')}")
        lines.append(f"- الحالة: {data.get('الحالة', 'غير معروف')}")
        lines.append(f"- المسؤول: {data.get('الموظف المسؤول', 'غير معروف')}")
        if data.get('التأخير') == 'نعم':
            lines.append("- ⚠️ **متأخرة**")
        if data.get('تاريخ التحويل'):
            lines.append(f"- تاريخ التحويل: {data.get('تاريخ التحويل')}")
        if data.get('المرافقات'):
            lines.append(f"- المرافقات: {data.get('المرافقات')}")
        return "\n".join(lines)

    # ================== الدالة الرئيسية للرد (التي يستدعيها البوت) ==================
    async def get_response(self, user_message, user_id, user_name, is_admin=False):
        """المدخل الرئيسي. تعيد إما نصاً أو قاموساً يحوي 'text' و 'reply_markup'"""
        try:
            intent, params = self._understand_intent(user_message, is_admin)
            logger.debug(f"Intent: {intent}, Params: {params}")

            result = await self._fetch_action(intent, params, user_id, is_admin)

            # تخزين المحادثة في السجل الدائم (chat_history) لاستخدامها في سياقات مستقبلية
            if self.sheets_client:
                try:
                    ws = self.sheets_client.get_worksheet("chat_history")
                    if ws:
                        text_result = result if isinstance(result, str) else result.get('text', '')
                        now = datetime.now().isoformat()
                        ws.append_row([now, user_id, user_name, user_message, text_result, str(is_admin)])
                except:
                    pass

            return result
        except Exception as e:
            logger.error(f"خطأ في get_response: {e}", exc_info=True)
            return "عذراً، حدث خطأ غير متوقع. حاول مرة أخرى."

    # ================== تحليل معمق لمعاملة (الوظيفة القديمة) ==================
    async def analyze_transaction(self, transaction_data, history):
        try:
            summary = f"المعاملة {transaction_data.get('ID')}\nالاسم: {transaction_data.get('اسم صاحب المعاملة الثلاثي')}\nالقسم: {transaction_data.get('القسم')}\nالحالة: {transaction_data.get('الحالة')}\nالتأخير: {transaction_data.get('التأخير')}\nالتاريخ: {transaction_data.get('تاريخ التحويل')}\nالمسؤول: {transaction_data.get('الموظف المسؤول')}\n\n"
            timeline = "سجل التتبع:\n" + "\n".join([f"- {e['time']}: {e['action']} ({e['user']})" for e in history])
            prompt = f"قدم تحليلاً مختصراً وتوصيات:\n{summary}\n{timeline}"
            resp = self.client.chat.completions.create(
                messages=[{"role": "system", "content": "أنت محلل معاملات خبير."}, {"role": "user", "content": prompt}],
                model=self.model,
                temperature=0.5,
                max_tokens=600
            )
            return resp.choices[0].message.content
        except Exception as e:
            logger.error(f"خطأ في التحليل: {e}")
            return "عذراً، حدث خطأ أثناء التحليل."
