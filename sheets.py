import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging

logger = logging.getLogger(__name__)

class SheetsService:
    def __init__(self, credentials_json):
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds_dict = json.loads(credentials_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            # افتح الجدول المطلوب (غيّر 'اسم_الجدول' إلى الاسم الفعلي)
            self.sheet = self.client.open("اسم_الجدول").sheet1
            logger.info("✅ تم الاتصال بـ Google Sheets")
        except Exception as e:
            logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
            raise

    def get_row_count(self):
        """إرجاع عدد الصفوف في الجدول (بما في ذلك رأس الصف الأول)"""
        return len(self.sheet.get_all_values())

    def get_new_rows(self, last_count):
        """إرجاع قائمة بالصفوف المضافة بعد last_count (كل صف كقاموس)"""
        all_rows = self.sheet.get_all_values()
        if len(all_rows) <= last_count:
            return []
        headers = all_rows[0]
        new_rows = all_rows[last_count:]
        result = []
        for row in new_rows:
            row_dict = {}
            for i, cell in enumerate(row):
                if i < len(headers):
                    row_dict[headers[i]] = cell
            result.append(row_dict)
        return result