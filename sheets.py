import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    def __init__(self):
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if not creds_json:
                raise ValueError("GOOGLE_CREDENTIALS_JSON not set")
            creds_dict = json.loads(creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            # افتح الجدول الرئيسي (تأكد من أن الاسم صحيح)
            self.spreadsheet = self.client.open('اسم_جدولك')  # غيّر إلى اسم جدولك الفعلي
            logger.info("✅ تم الاتصال بـ Google Sheets")
        except Exception as e:
            logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
            raise

    def get_worksheet(self, sheet_name):
        """فتح ورقة معينة داخل الجدول الرئيسي"""
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except Exception as e:
            logger.error(f"❌ فشل فتح الورقة {sheet_name}: {e}")
            return None

    def get_all_records(self, sheet_name):
        """إرجاع جميع السجلات كقائمة من القواميس"""
        ws = self.get_worksheet(sheet_name)
        if ws:
            return ws.get_all_records()
        return []

    def get_row_by_id(self, sheet_name, transaction_id):
        """البحث عن صف حسب عمود ID (يفترض أن العمود اسمه ID)"""
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return None
        records = ws.get_all_records()
        for idx, row in enumerate(records):
            if str(row.get('ID')) == str(transaction_id):
                return {'row': idx + 2, 'data': row}
        return None