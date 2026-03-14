import gspread
from google.oauth2.service_account import Credentials
import logging
import json
import os
import base64
from config import Config

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.scope = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        self.connect()

    def connect(self):
        try:
            creds = None
            logger.info("🔍 بدء محاولة الاتصال بـ Google Sheets")

            # 1. محاولة قراءة المتغيرات المنفردة
            private_key = os.getenv('GOOGLE_PRIVATE_KEY')
            client_email = os.getenv('GOOGLE_CLIENT_EMAIL')
            project_id = os.getenv('GOOGLE_PROJECT_ID')
            private_key_id = os.getenv('GOOGLE_PRIVATE_KEY_ID')
            client_id = os.getenv('GOOGLE_CLIENT_ID')
            auth_uri = os.getenv('GOOGLE_AUTH_URI', 'https://accounts.google.com/o/oauth2/auth')
            token_uri = os.getenv('GOOGLE_TOKEN_URI', 'https://oauth2.googleapis.com/token')
            auth_provider_x509_cert_url = os.getenv('GOOGLE_AUTH_PROVIDER_CERT_URL', 'https://www.googleapis.com/oauth2/v1/certs')
            client_x509_cert_url = os.getenv('GOOGLE_CLIENT_CERT_URL')
            universe_domain = os.getenv('GOOGLE_UNIVERSE_DOMAIN', 'googleapis.com')

            if private_key and client_email:
                try:
                    info = {
                        "type": "service_account",
                        "project_id": project_id,
                        "private_key_id": private_key_id,
                        "private_key": private_key,
                        "client_email": client_email,
                        "client_id": client_id,
                        "auth_uri": auth_uri,
                        "token_uri": token_uri,
                        "auth_provider_x509_cert_url": auth_provider_x509_cert_url,
                        "client_x509_cert_url": client_x509_cert_url,
                        "universe_domain": universe_domain
                    }
                    creds = Credentials.from_service_account_info(info, scopes=self.scope)
                    logger.info("✅ تم تحميل بيانات الاعتماد من المتغيرات المنفردة")
                except Exception as e:
                    logger.error(f"❌ فشل بناء الاعتماد من المتغيرات المنفردة: {e}")

            # 2. إذا فشلت، جرب Base64 (احتياطي)
            if not creds:
                creds_b64 = os.getenv('GOOGLE_CREDENTIALS_BASE64')
                if creds_b64:
                    try:
                        json_bytes = base64.b64decode(creds_b64)
                        info = json.loads(json_bytes)
                        creds = Credentials.from_service_account_info(info, scopes=self.scope)
                        logger.info("✅ تم تحميل بيانات الاعتماد من base64")
                    except Exception as e:
                        logger.error(f"❌ فشل فك base64: {e}")

            # 3. كحل أخير، جرب الملف
            if not creds:
                file_path = '/volumes/credentials.json'
                if os.path.exists(file_path):
                    try:
                        creds = Credentials.from_service_account_file(file_path, scopes=self.scope)
                        logger.info("✅ تم تحميل بيانات الاعتماد من الملف")
                    except Exception as e:
                        logger.error(f"❌ فشل قراءة الملف: {e}")
                else:
                    logger.warning("⚠️ ملف الاعتماد غير موجود في المسار المتوقع")

            if not creds:
                raise ValueError("❌ لا يوجد مصدر موثوق لبيانات الاعتماد!")

            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(Config.SPREADSHEET_ID)
            logger.info("✅ متصل بـ Google Sheets")

        except Exception as e:
            logger.error(f"❌ فشل الاتصال: {e}")
            raise

    # ========== دوال الوصول إلى الأوراق (كما هي سابقاً) ==========
    def get_worksheet(self, sheet_name):
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return None

    def ensure_sheets_exist(self):
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
        ws = self.get_worksheet(sheet_name)
        return ws.get_all_records() if ws else []

    def get_last_row(self):
        ws = self.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return None
        all_records = ws.get_all_records()
        return all_records[-1] if all_records else None

    def append_row(self, sheet_name, row_data):
        ws = self.get_worksheet(sheet_name)
        if ws:
            ws.append_row(row_data)

    def get_row_by_id(self, sheet_name, id_value):
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return None
        records = ws.get_all_records()
        for idx, rec in enumerate(records, start=2):
            if str(rec.get('ID', '')) == str(id_value):
                return {'row': idx, 'data': rec}
        return None

    def update_cell(self, sheet_name, row, col, value):
        ws = self.get_worksheet(sheet_name)
        if ws:
            ws.update_cell(row, col, value)

    def get_headers(self, sheet_name):
        ws = self.get_worksheet(sheet_name)
        return ws.row_values(1) if ws else []