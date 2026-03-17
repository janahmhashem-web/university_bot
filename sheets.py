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
        try:
            creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
            if not creds_json:
                raise Exception("❌ GOOGLE_CREDENTIALS_JSON غير موجود")
            creds_dict = json.loads(creds_json)
            credentials = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
            self.client = gspread.authorize(credentials)
            print("✅ تم الاتصال بـ Google Sheets")
        except Exception as e:
            print(f"❌ فشل الاتصال: {e}")
            self.client = None

    def get_worksheet(self, sheet_name):
        if not self.client:
            return None
        try:
            sh = self.client.open_by_key(Config.SPREADSHEET_ID)
            return sh.worksheet(sheet_name)
        except Exception as e:
            print(f"⚠️ خطأ في الحصول على الورقة {sheet_name}: {e}")
            return None

    def get_all_records(self, sheet_name):
        ws = self.get_worksheet(sheet_name)
        if ws:
            try:
                return ws.get_all_records()
            except Exception as e:
                print(f"⚠️ خطأ في جلب السجلات: {e}")
                return []
        return []

    def get_row_by_id(self, sheet_name, transaction_id):
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return None
        try:
            cell = ws.find(transaction_id, in_column=8)
            if cell:
                row_data = ws.row_values(cell.row)
                headers = ws.row_values(1)
                return {'row': cell.row, 'data': dict(zip(headers, row_data))}
        except Exception as e:
            print(f"⚠️ خطأ في البحث عن ID {transaction_id}: {e}")
        return None

    def update_cell(self, sheet_name, row, col, value):
        ws = self.get_worksheet(sheet_name)
        if ws:
            try:
                ws.update_cell(row, col, value)
                return True
            except Exception as e:
                print(f"⚠️ خطأ في تحديث الخلية: {e}")
        return False

    def append_row(self, sheet_name, values):
        ws = self.get_worksheet(sheet_name)
        if ws:
            try:
                ws.append_row(values)
                return True
            except Exception as e:
                print(f"⚠️ خطأ في إضافة صف: {e}")
        return False