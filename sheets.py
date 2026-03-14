import gspread
from google.oauth2.service_account import Credentials
import logging
import json
import os
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
            # ✅ تسجيل أسماء جميع المتغيرات البيئية (للتشخيص)
            logger.info(f"📋 جميع المتغيرات البيئية المتوفرة: {list(os.environ.keys())}")

            # ✅ قراءة المتغير البيئي (طريقة آمنة)
            creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
            logger.info(f"🔍 GOOGLE_CREDENTIALS_JSON موجود؟ {creds_json is not None}")
            if creds_json:
                logger.info(f"🔍 طول القيمة: {len(creds_json)} حرف")
            else:
                # 🔴 في حالة عدم وجود المتغير، نسجل خطأ ونرفع استثناء
                raise ValueError("❌ المتغير البيئي GOOGLE_CREDENTIALS_JSON غير موجود!")

            # ✅ تحويل JSON إلى قاموس
            try:
                info = json.loads(creds_json)
                logger.info("✅ تم فك ترميز JSON بنجاح")
            except json.JSONDecodeError as e:
                logger.error(f"❌ فشل فك ترميز JSON: {e}")
                raise

            # ✅ إنشاء بيانات الاعتماد
            creds = Credentials.from_service_account_info(info, scopes=self.scope)
            logger.info("✅ تم تحميل بيانات الاعتماد من المتغير البيئي")

            # ✅ الاتصال بـ gspread
            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(Config.SPREADSHEET_ID)
            logger.info("✅ متصل بـ Google Sheets")

        except Exception as e:
            logger.error(f"❌ فشل الاتصال: {e}")
            raise

    # ======================= باقي الدوال (بدون تغيير) =======================

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