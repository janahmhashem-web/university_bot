import gspread
from google.oauth2.service_account import Credentials
import logging
import json
import os
import base64
from config import Config

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    """
    عميل للتعامل مع Google Sheets يدعم:
    - التحميل من متغير البيئة GOOGLE_CREDENTIALS_BASE64 (أولوية قصوى)
    - التحميل من GOOGLE_CREDENTIALS_JSON (إذا لم يوجد base64)
    - التحميل من ملف /volumes/credentials.json (كحل أخير)
    """

    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.scope = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        self.connect()

    def connect(self):
        """محاولة الاتصال بـ Google Sheets باستخدام أول مصدر متاح"""
        try:
            creds = None

            # ---------- تسجيل معلومات للتشخيص ----------
            logger.info("🔍 بدء محاولة الاتصال بـ Google Sheets")
            env_vars = [k for k in os.environ.keys() if 'GOOGLE' in k or 'CRED' in k]
            logger.info(f"📋 المتغيرات البيئية ذات الصلة: {env_vars}")

            # 1. المحاولة من المتغير Base64 (الأكثر أماناً)
            creds_b64 = os.getenv('GOOGLE_CREDENTIALS_BASE64')
            if creds_b64:
                logger.info(f"📏 طول base64: {len(creds_b64)}")
                try:
                    json_bytes = base64.b64decode(creds_b64)
                    info = json.loads(json_bytes)
                    creds = Credentials.from_service_account_info(info, scopes=self.scope)
                    logger.info("✅ تم تحميل بيانات الاعتماد من base64")
                except Exception as e:
                    logger.error(f"❌ فشل فك base64: {e}")

            # 2. إذا فشل base64، نجرب المتغير JSON العادي
            if not creds:
                creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
                if creds_json:
                    logger.info(f"📏 طول JSON: {len(creds_json)}")
                    logger.info(f"🔍 أول 100 حرف: {creds_json[:100]}")
                    logger.info(f"🔍 آخر 100 حرف: {creds_json[-100:]}")
                    try:
                        info = json.loads(creds_json)
                        creds = Credentials.from_service_account_info(info, scopes=self.scope)
                        logger.info("✅ تم تحميل بيانات الاعتماد من JSON")
                    except Exception as e:
                        logger.error(f"❌ فشل تحليل JSON: {e}")

            # 3. إذا فشل كل ما سبق، نجرب الملف (إذا كان موجوداً في Volume)
            if not creds:
                file_path = '/volumes/credentials.json'
                if os.path.exists(file_path):
                    logger.info(f"📁 محاولة قراءة الملف: {file_path}")
                    try:
                        creds = Credentials.from_service_account_file(file_path, scopes=self.scope)
                        logger.info("✅ تم تحميل بيانات الاعتماد من الملف")
                    except Exception as e:
                        logger.error(f"❌ فشل قراءة الملف: {e}")
                else:
                    logger.warning("⚠️ ملف الاعتماد غير موجود في المسار المتوقع")

            if not creds:
                raise ValueError("❌ لا يوجد مصدر موثوق لبيانات الاعتماد!")

            # الاتصال باستخدام gspread
            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(Config.SPREADSHEET_ID)
            logger.info("✅ متصل بـ Google Sheets")

        except Exception as e:
            logger.error(f"❌ فشل الاتصال: {e}")
            raise

    # ======================= دوال الوصول إلى الأوراق =======================

    def get_worksheet(self, sheet_name):
        """إرجاع ورقة عمل، أو None إذا لم توجد"""
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return None

    def ensure_sheets_exist(self):
        """إنشاء الأوراق المطلوبة إذا لم تكن موجودة"""
        required = [Config.SHEET_MANAGER, Config.SHEET_QR, Config.SHEET_ARCHIVE, Config.SHEET_HISTORY]
        for sheet in required:
            if not self.get_worksheet(sheet):
                ws = self.spreadsheet.add_worksheet(sheet, 100, 26)
                logger.info(f"✅ تم إنشاء ورقة: {sheet}")

                if sheet == Config.SHEET_MANAGER:
                    headers = [
                        'Timestamp', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف', 'البريد الإلكتروني',
                        'القسم', 'نوع المعاملة', 'المرافقات', 'ID', 'الحالة', 'الأولوية',
                        'الموظف المسؤول', 'المؤسسة الحالية', 'المؤسسة التالية', 'تاريخ التحويل',
                        'سبب التحويل', 'الموافق', 'ملاحظات إضافية', 'آخر إجراء', 'التأخير',
                        'المستمسكات المطلوبة', 'الرابط', 'آخر تعديل بواسطة', 'آخر تعديل بتاريخ',
                        'عدد التعديلات', 'البريد الإلكتروني الموظف', 'LOG_JSON'
                    ]
                    ws.append_row(headers)

                elif sheet == Config.SHEET_QR:
                    ws.append_row(['الطابع الزمني', 'اسم صاحب المعاملة', 'ID', 'الرابط', 'QR Code', 'رابط الصورة'])

                elif sheet == Config.SHEET_ARCHIVE:
                    mgr_headers = self.get_worksheet(Config.SHEET_MANAGER).row_values(1)
                    ws.append_row(mgr_headers + ['تاريخ الأرشفة'])

                elif sheet == Config.SHEET_HISTORY:
                    mgr_headers = self.get_worksheet(Config.SHEET_MANAGER).row_values(1)
                    hist_headers = mgr_headers + [
                        'المؤسسة السابقة', 'المؤسسة الحالية بعد النقل',
                        'الموظف المسؤول', 'الإجراء', 'نوع الإجراء',
                        'تاريخ التتبع', 'رابط المعاملة'
                    ]
                    ws.append_row(hist_headers)

    def get_all_records(self, sheet_name):
        """جلب جميع السجلات من ورقة معينة كقائمة من القواميس"""
        ws = self.get_worksheet(sheet_name)
        return ws.get_all_records() if ws else []

    def append_row(self, sheet_name, row_data):
        """إضافة صف جديد في نهاية الورقة"""
        ws = self.get_worksheet(sheet_name)
        if ws:
            ws.append_row(row_data)

    def get_row_by_id(self, sheet_name, id_value):
        """البحث عن صف بواسطة ID (يعيد رقم الصف وبياناته)"""
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return None
        records = ws.get_all_records()
        for idx, rec in enumerate(records, start=2):   # start=2 لأن الصف 1 هو الترويسة
            if str(rec.get('ID', '')) == str(id_value):
                return {'row': idx, 'data': rec}
        return None

    def update_cell(self, sheet_name, row, col, value):
        """تحديث خلية محددة (row و col هما 1‑based)"""
        ws = self.get_worksheet(sheet_name)
        if ws:
            ws.update_cell(row, col, value)

    def get_headers(self, sheet_name):
        """إرجاع عناوين الأعمدة في الصف الأول"""
        ws = self.get_worksheet(sheet_name)
        return ws.row_values(1) if ws else []