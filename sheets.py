import os
import json
import gspread
from google.oauth2.service_account import Credentials
from config import Config

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

class GoogleSheetsClient:
    def __init__(self):
        self.client = None
        self.connect()

    def connect(self):
        """الاتصال بـ Google Sheets باستخدام بيانات الاعتماد من المتغيرات البيئية"""
        try:
            # محاولة قراءة JSON من المتغير البيئي
            creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
            if not creds_json:
                raise Exception("❌ GOOGLE_CREDENTIALS_JSON غير موجود في المتغيرات البيئية")
            
            # تحويل النص JSON إلى قاموس
            creds_dict = json.loads(creds_json)
            credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            self.client = gspread.authorize(credentials)
            print("✅ تم الاتصال بـ Google Sheets")
        except Exception as e:
            print(f"❌ فشل الاتصال: {e}")
            self.client = None

    def get_worksheet(self, sheet_name):
        """الحصول على ورقة عمل محددة"""
        if not self.client:
            return None
        try:
            sh = self.client.open_by_key(Config.SPREADSHEET_ID)
            return sh.worksheet(sheet_name)
        except Exception as e:
            print(f"⚠️ خطأ في الحصول على الورقة {sheet_name}: {e}")
            return None

    def ensure_sheets_exist(self):
        """التأكد من وجود الأوراق المطلوبة (يمكن توسيعها لإنشائها إن لم توجد)"""
        # هذه الدالة اختيارية، يمكن تركها pass أو إضافة منطق إنشاء الأوراق
        pass

    # ==================== دوال القراءة ====================

    def get_all_records(self, sheet_name):
        """جلب جميع السجلات من ورقة معينة (بافتراض أن الصف الأول هو العناوين)"""
        ws = self.get_worksheet(sheet_name)
        if ws:
            try:
                return ws.get_all_records()
            except Exception as e:
                print(f"⚠️ خطأ في جلب السجلات من {sheet_name}: {e}")
                return []
        return []

    def get_row_by_id(self, sheet_name, transaction_id):
        """البحث عن صف بواسطة ID (يفترض أن ID موجود في العمود A)"""
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return None
        try:
            # البحث عن الخلية التي تحتوي على النص المطابق لـ transaction_id
            cell = ws.find(transaction_id)
            if cell:
                row_data = ws.row_values(cell.row)
                headers = ws.row_values(1)  # الصف الأول هو العناوين
                return {
                    'row': cell.row,
                    'data': dict(zip(headers, row_data))
                }
        except Exception as e:
            print(f"⚠️ خطأ في البحث عن ID {transaction_id} في {sheet_name}: {e}")
        return None

    def get_headers(self, sheet_name):
        """الحصول على عناوين الأعمدة (الصف الأول)"""
        ws = self.get_worksheet(sheet_name)
        if ws:
            try:
                return ws.row_values(1)
            except Exception as e:
                print(f"⚠️ خطأ في جلب العناوين من {sheet_name}: {e}")
        return []

    # ==================== دوال الكتابة ====================

    def update_cell(self, sheet_name, row, col, value):
        """تحديث خلية محددة (رقم الصف، رقم العمود، القيمة)"""
        ws = self.get_worksheet(sheet_name)
        if ws:
            try:
                ws.update_cell(row, col, value)
                return True
            except Exception as e:
                print(f"⚠️ خطأ في تحديث الخلية ({row},{col}): {e}")
        return False

    def update_row(self, sheet_name, row, data_dict):
        """تحديث صف كامل باستخدام قاموس (المفتاح = اسم العمود)"""
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return False
        try:
            headers = ws.row_values(1)
            # قائمة القيم بنفس ترتيب الأعمدة
            row_values = [data_dict.get(h, '') for h in headers]
            # تحديث الخلايا من العمود A إلى آخر عمود
            cell_range = f'A{row}:{chr(64 + len(headers))}{row}'
            ws.update(cell_range, [row_values])
            return True
        except Exception as e:
            print(f"⚠️ خطأ في تحديث الصف {row}: {e}")
        return False

    def append_row(self, sheet_name, values):
        """إضافة صف جديد في نهاية الورقة"""
        ws = self.get_worksheet(sheet_name)
        if ws:
            try:
                ws.append_row(values)
                return True
            except Exception as e:
                print(f"⚠️ خطأ في إضافة صف إلى {sheet_name}: {e}")
        return False

    # ==================== دوال إضافية ====================

    def find_cell(self, sheet_name, value):
        """البحث عن خلية تحتوي على قيمة محددة"""
        ws = self.get_worksheet(sheet_name)
        if ws:
            try:
                return ws.find(value)
            except Exception as e:
                print(f"⚠️ خطأ في البحث عن '{value}' في {sheet_name}: {e}")
        return None