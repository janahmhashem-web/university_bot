# ai_handler.py - نظام ذكاء اصطناعي ذاتي التعلم مع تفكير عميق وتحليل استنتاجي
import os
import re
import json
import logging
import random
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Union, Any

from groq import Groq
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.metrics.pairwise import cosine_similarity
import joblib

# اختياري: استخدام nltk لتحليل المشاعر
try:
    from nltk.sentiment import SentimentIntensityAnalyzer
    nltk.download('vader_lexicon', quiet=True)
    SENTIMENT_AVAILABLE = True
except ImportError:
    SENTIMENT_AVAILABLE = False

logger = logging.getLogger(__name__)

class SelfLearningAIAssistant:
    """
    مساعد ذكي يتعلم باستمرار من التفاعلات البشرية:
    - نموذج ML لتصنيف النية يتم إعادة تدريبه كل ليلة أو عند تجمع عينات كافية.
    - توليد عينات تدريب من التفاعلات الحقيقية (باستخدام تقييم ضمني وصريح).
    - تحليل عميق باستخدام Chain-of-Thought.
    - ذاكرة معرفية شخصية لكل مستخدم.
    """

    INTENT_CATEGORIES = [
        "general_question", "transaction_status", "transaction_history",
        "transaction_details", "assign_employee", "change_status",
        "stats", "search", "my_transaction", "analyze",
        "help", "greeting", "farewell", "support"
    ]
    
    def __init__(self, sheets_client=None, model_path="ai_model.joblib"):
        self.sheets_client = sheets_client
        self.model_path = model_path
        
        # Groq للإجابات التوليدية والتحليل العميق
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY missing")
        self.client = Groq(api_key=api_key)
        self.model_name = "llama3-70b-8192"  # قوي للتحليل والتفكير
        
        # إعدادات الإبداع والتنوع
        self.temperature = 0.85
        self.top_p = 0.95
        self.frequency_penalty = 0.5
        self.presence_penalty = 0.5
        
        # الذاكرة المؤقتة
        self.user_sessions: Dict[int, Dict] = {}
        self.user_memory: Dict[int, List[Dict]] = defaultdict(list)  # ملخصات أسبوعية
        
        # نموذج التعلم الآلي
        self.classifier = None
        self.vectorizer = None
        self.needs_retraining = False
        self.new_training_samples = []  # قائمة (text, intent_label)
        self.last_training_date = datetime.now() - timedelta(days=1)
        
        # تحميل النموذج إن وجد
        self._load_or_init_model()
        
        # إنشاء أوراق الذاكرة الطويلة
        self._init_sheets()
        
        # تحميل بيانات التدريب السابقة من Sheets
        self._load_training_data_from_sheets()
        
        # جدولة إعادة التدريب (ستُستدعى من main.py عبر scheduler)
        logger.info("AI Assistant with self-learning initialized")
    
    # ------------------ نموذج التعلم الآلي ------------------
    def _load_or_init_model(self):
        if os.path.exists(self.model_path):
            try:
                saved = joblib.load(self.model_path)
                self.classifier = saved['classifier']
                self.vectorizer = saved['vectorizer']
                logger.info("Loaded existing ML model")
                return
            except:
                pass
        # نموذج جديد
        self.vectorizer = TfidfVectorizer(max_features=500, ngram_range=(1,2))
        self.classifier = MultinomialNB()
        logger.info("Initialized new ML model (will be trained later)")
    
    def _save_model(self):
        joblib.dump({
            'classifier': self.classifier,
            'vectorizer': self.vectorizer
        }, self.model_path)
        logger.info("Model saved to disk")
    
    def _train_model(self, texts, labels):
        if len(texts) < 10:
            logger.warning(f"Not enough samples ({len(texts)}) to train")
            return
        X = self.vectorizer.fit_transform(texts)
        self.classifier.fit(X, labels)
        self._save_model()
        logger.info(f"Retrained ML model on {len(texts)} samples")
    
    def _load_training_data_from_sheets(self):
        if not self.sheets_client:
            return
        ws = self.sheets_client.get_worksheet("ml_training_data")
        if not ws:
            return
        records = ws.get_all_records()
        texts, labels = [], []
        for r in records:
            if r.get('text') and r.get('label') and r.get('label') in self.INTENT_CATEGORIES:
                texts.append(r['text'])
                labels.append(r['label'])
        if texts:
            self._train_model(texts, labels)
    
    def _add_training_sample(self, text: str, intent: str, confidence: float = 1.0):
        """أضف عينة تدريب جديدة (قد تكون من تفاعل ناجح)"""
        if intent not in self.INTENT_CATEGORIES:
            return
        # تجنب التكرار التقريبي
        for existing, _ in self.new_training_samples:
            if existing == text or cosine_similarity_vector(text, existing) > 0.85:
                return
        self.new_training_samples.append((text, intent))
        # حفظ فوري في Sheets
        if self.sheets_client:
            ws = self.sheets_client.get_worksheet("ml_training_data")
            if ws:
                ws.append_row([text, intent, datetime.now().isoformat(), confidence])
        # إذا تجمع 50 عينة جديدة، نحتاج إلى إعادة التدريب
        if len(self.new_training_samples) >= 50:
            self.needs_retraining = True
    
    def _collect_implicit_feedback(self, user_id: int, user_message: str, ai_response: str, intent: str):
        """تحليل رد الفعل الضمني لمعرفة ما إذا كانت الإجابة مفيدة أم لا"""
        session = self.user_sessions.get(user_id, {})
        last_user_msg = session.get("last_user_message", "")
        # إذا كرر المستخدم نفس السؤال بعد الإجابة مباشرة → إجابة سيئة
        if last_user_msg and cosine_similarity_vector(last_user_msg, user_message) > 0.8:
            self._add_training_sample(user_message, "help", 0.2)  # عينة سلبية
            logger.info(f"Implicit negative feedback from user {user_id}")
        # إذا شكر أو قال "شكرا"، "ممتاز" → إجابة جيدة
        if any(word in user_message.lower() for word in ["شكرا", "ممتاز", "تمام", "رائع", "احسنت"]):
            self._add_training_sample(user_message, intent, 0.9)
    
    # ------------------ تحليل المشاعر والتفكير العميق ------------------
    def _analyze_sentiment(self, text: str) -> str:
        if SENTIMENT_AVAILABLE:
            sia = SentimentIntensityAnalyzer()
            score = sia.polarity_scores(text)['compound']
            if score > 0.3:
                return 'positive'
            elif score < -0.3:
                return 'negative'
            return 'neutral'
        return 'neutral'
    
    async def _deep_reasoning_chain(self, user_message: str, context: str) -> str:
        """
        سلسلة تفكير استنتاجي (Chain-of-Thought) قبل الإجابة على الأسئلة المعقدة.
        نطلب من النموذج إظهار خطوات التفكير ثم استخلاص الإجابة النهائية.
        """
        prompt = f"""أنت خبير تحليل منطقي. قم بالتفكير خطوة بخطوة للإجابة على السؤال التالي.
        
السياق: {context}
السؤال: {user_message}

اكتب خطوات تفكيرك كما يلي:
1. فهم المطلوب: ...
2. تحليل المعطيات: ...
3. الاستنتاجات الوسيطة: ...
4. الإجابة النهائية (ملخص واضح):

الإجابة النهائية يجب أن تكون في سطر منفصل يبدأ بـ "الإجابة النهائية:".
"""
        try:
            resp = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model_name,
                temperature=0.3,
                max_tokens=800
            )
            full = resp.choices[0].message.content
            # استخراج الإجابة النهائية
            final_match = re.search(r'الإجابة النهائية:\s*(.+)', full, re.DOTALL)
            if final_match:
                return final_match.group(1).strip()
            return full
        except Exception as e:
            logger.error(f"Chain-of-thought failed: {e}")
            return await self._handle_general_question(0, user_message, "", False)  # fallback
    
    # ------------------ فهم النية باستخدام النموذج المتعلم ------------------
    def _classify_intent_ml(self, text: str) -> Tuple[str, float]:
        if self.classifier is None:
            # استخدام القواعد البسيطة كبداية
            return self._rule_based_intent(text), 0.7
        try:
            X = self.vectorizer.transform([text])
            probs = self.classifier.predict_proba(X)[0]
            max_prob = max(probs)
            if max_prob < 0.5:
                return "general_question", max_prob
            idx = probs.argmax()
            intent = self.classifier.classes_[idx]
            return intent, max_prob
        except:
            return self._rule_based_intent(text), 0.5
    
    def _rule_based_intent(self, text: str) -> str:
        msg = text.lower()
        if re.search(r'\b(مرحب|اهلا|سلام)\b', msg):
            return "greeting"
        if re.search(r'\b(مع السلامة|باي|وداعا)\b', msg):
            return "farewell"
        if re.search(r'MUT-\d{14}-\d{4}', text):
            return "transaction_details"
        if re.search(r'\b(معاملتي|خاصتي)\b', msg):
            return "my_transaction"
        if re.search(r'\b(حالة|status)\b', msg):
            return "transaction_status"
        if re.search(r'\b(سجل|تتبع|history)\b', msg):
            return "transaction_history"
        if re.search(r'\b(تحليل|analyze|اقتراح)\b', msg):
            return "analyze"
        if re.search(r'\b(إحصاء|عدد|كم)\b', msg):
            return "stats"
        if re.search(r'\b(ابحث|بحث|search)\b', msg):
            return "search"
        if re.search(r'\b(مساعدة|أزرار|help)\b', msg):
            return "help"
        return "general_question"
    
    # ------------------ تحليل المعاملات بطريقة عميقة ------------------
    async def _deep_transaction_analysis(self, transaction_data: Dict, history: List[Dict], user_id: int) -> str:
        """تحليل استنتاجي متعمق ينتج رؤى غير سطحية"""
        # بناء ملخص تفصيلي
        summary = f"""
        المعاملة: {transaction_data.get('ID')}
        الاسم: {transaction_data.get('اسم صاحب المعاملة الثلاثي')}
        القسم: {transaction_data.get('القسم')}
        الحالة الحالية: {transaction_data.get('الحالة')}
        الموظف المسؤول: {transaction_data.get('الموظف المسؤول')}
        التأخير: {transaction_data.get('التأخير')}
        تاريخ التحويل: {transaction_data.get('تاريخ التحويل')}
        سبب التحويل: {transaction_data.get('سبب التحويل')}
        الملاحظات: {transaction_data.get('ملاحظات إضافية')}
        """
        timeline = "\n".join([f"- {h['time']}: {h['action']} ({h['user']})" for h in history[-8:]])
        
        prompt = f"""أنت خبير استشاري في تحسين العمليات الإدارية. قم بتحليل معمق للمعاملة التالية. يجب أن يتضمن تحليلك:
- تحديد نقاط الضعف أو التأخير المحتملة
- تقييم كفاءة الإجراءات الحالية
- اقتراح 3 تحسينات قابلة للتنفيذ (بأولويات)
- توقع العواقب إذا لم يتم التحسين

معلومات المعاملة:
{summary}

سجل التتبع:
{timeline}

اكتب تحليلك بأسلوب احترافي، مع أمثلة ملموسة. كن دقيقاً ومباشراً.
"""
        try:
            response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=self.model_name,
                temperature=0.4,
                max_tokens=1200
            )
            analysis = response.choices[0].message.content.strip()
            # إضافة التوصيات في أزرار منفصلة؟ يمكن إرجاعها كنص منسق
            return f"📊 **تحليل معمق للمعاملة {transaction_data.get('ID')}**\n\n{analysis}"
        except Exception as e:
            logger.error(f"Deep analysis error: {e}")
            return "عذراً، حدث خطأ أثناء التحليل العميق."
    
    # ------------------ الدوال الأساسية (مشابهة للإصدار السابق ولكن محسنة) ------------------
    async def get_response(self, user_message: str, user_id: int, user_name: str = "", is_admin: bool = False) -> Union[str, Dict]:
        """الدالة الرئيسية: تتعلم وتفكر وتجيب"""
        # 1. تحليل المشاعر
        sentiment = self._analyze_sentiment(user_message)
        
        # 2. تصنيف النية باستخدام النموذج المتعلم
        intent, confidence = self._classify_intent_ml(user_message)
        logger.info(f"Intent: {intent} (confidence={confidence:.2f})")
        
        # 3. إذا كان النموذج غير واثق، ربما نطلب توضيحاً (Active Learning)
        if confidence < 0.5 and intent == "general_question":
            clarification = "لم أفهم جيداً، هل يمكنك توضيح سؤالك؟ أو استخدم الأزرار أدناه."
            keyboard = self._get_main_keyboard(is_admin)
            return {"type": "buttons", "text": clarification, "reply_markup": keyboard}
        
        # 4. معالجة النية (سيتم استدعاء دوال مشابهة للإصدار السابق لكن مع استخدام التحليل العميق)
        answer = await self._process_intent(intent, user_message, user_id, user_name, is_admin, sentiment)
        
        # 5. جمع الملاحظات الضمنية
        self._collect_implicit_feedback(user_id, user_message, answer, intent)
        
        # 6. تحديث الذاكرة الشخصية للمستخدم (ملخص أسبوعي)
        self._update_user_memory(user_id, user_message, answer, intent)
        
        # 7. إذا كانت الإجابة قصيرة جداً أو متكررة، أعد صياغتها
        if len(answer) < 30 and answer in self.user_sessions.get(user_id, {}).get("last_answers", []):
            answer = "دعني أوضح بشكل أفضل: " + await self._rephrase_answer(user_message, answer)
        
        return answer
    
    async def _process_intent(self, intent, user_message, user_id, user_name, is_admin, sentiment):
        # سيتم تنفيذ الإجراءات المناسبة باستخدام دوال مشابهة للنسخة السابقة
        # ... (نختصر هنا لعرض المبادئ)
        if intent == "analyze":
            # تحليل عميق باستخدام chain-of-thought
            return await self._handle_deep_analysis(user_id, user_message)
        elif intent == "transaction_details":
            return await self._handle_transaction_details(user_id, user_message)
        else:
            return await self._handle_general_question(user_id, user_message, user_name, is_admin)
    
    async def _handle_deep_analysis(self, user_id, user_message):
        # استخراج رقم المعاملة من الجلسة أو من النص
        # ثم استدعاء _deep_transaction_analysis
        # ...
        return "تحليل مفصل (سيتم تنفيذه)"
    
    # ------------------ جدولة إعادة التدريب ------------------
    def request_retraining_if_needed(self):
        if self.needs_retraining or (datetime.now() - self.last_training_date) > timedelta(hours=24):
            self._retrain_from_samples()
            self.needs_retraining = False
            self.last_training_date = datetime.now()
    
    def _retrain_from_samples(self):
        if not self.new_training_samples:
            return
        texts, labels = zip(*self.new_training_samples)
        # دمج مع بيانات التدريب القديمة (نحتفظ بآخر 1000 عينة)
        old_texts, old_labels = self._get_old_training_data()
        all_texts = list(old_texts) + list(texts)
        all_labels = list(old_labels) + list(labels)
        self._train_model(all_texts, all_labels)
        self.new_training_samples.clear()
    
    def _get_old_training_data(self):
        # استرجاع من الذاكرة أو من Sheets
        # ...
        return [], []
    
    # ------------------ أزرار ووظائف إضافية ------------------
    def _get_main_keyboard(self, is_admin):
        # كما هو موجود سابقاً
        pass
    
    def _update_user_memory(self, user_id, user_message, ai_response, intent):
        # تخزين ملخص في `user_context`
        pass
