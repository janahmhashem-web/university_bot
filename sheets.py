import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging
from datetime import datetime

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
            # ⚠️ استبدل "اسم_جدولك" بالاسم الحقيقي للجدول
            self.spreadsheet = self.client.open("university system")
            logger.info("✅ تم الاتصال بـ Google Sheets")
            self._init_sheets()
        except Exception as e:
            logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
            raise

    def _init_sheets(self):
        """إنشاء الأوراق المطلوبة وإضافة الأعمدة الأساسية"""
        from config import Config

        sheets_required = {
            Config.SHEET_MANAGER: [
                "Timestamp", "اسم صاحب المعاملة الثلاثي", "رقم الهاتف", "البريد الإلكتروني",
                "القسم", "نوع المعاملة", "المرافقات", "ID", "الحالة", "الأولوية",
                "الموظف المسؤول", "المؤسسة الحالية", "المؤسسة التالية", "تاريخ التحويل",
                "سبب التحويل", "الموافق", "ملاحظات إضافية", "آخر إجراء", "التأخير",
                "المستمسكات المطلوبة", "الرابط", "آخر تعديل بواسطة", "آخر تعديل بتاريخ",
                "عدد التعديلات", "البريد الإلكتروني الموظف", "LOG_JSON"
            ],
            Config.SHEET_HISTORY: ["timestamp", "ID", "action", "user"],
            Config.SHEET_QR: ["name", "email", "transaction_id", "view_link", "qr_image_url", "qr_page_link", "edit_link"],
            Config.SHEET_USERS: ["transaction_id", "chat_id"]
        }

        for sheet_name, required_headers in sheets_required.items():
            try:
                ws = self.get_worksheet(sheet_name)
                if ws is None:
                    ws = self.spreadsheet.add_worksheet(title=sheet_name, rows=1, cols=len(required_headers))
                    for col, header in enumerate(required_headers, 1):
                        ws.update_cell(1, col, header)
                    logger.info(f"✅ تم إنشاء الورقة '{sheet_name}' مع الأعمدة المطلوبة")
                else:
                    existing_headers = ws.row_values(1)
                    for col, header in enumerate(required_headers, 1):
                        if header not in existing_headers:
                            ws.update_cell(1, col, header)
                            logger.info(f"✅ تم إضافة العمود '{header}' إلى الورقة '{sheet_name}'")
            except Exception as e:
                logger.error(f"❌ فشل إعداد الورقة {sheet_name}: {e}")

    def get_worksheet(self, sheet_name):
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except Exception:
            return None

    def get_all_records(self, sheet_name):
        ws = self.get_worksheet(sheet_name)
        if ws:
            return ws.get_all_records()
        return []

    def get_row_by_id(self, sheet_name, transaction_id):
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return None
        records = ws.get_all_records()
        for idx, row in enumerate(records):
            if str(row.get('ID')) == str(transaction_id):
                return {'row': idx + 2, 'data': row}
        return None

    def add_history_entry(self, transaction_id, action, user="النظام"):
        try:
            ws = self.get_worksheet('history')
            if ws:
                ws.append_row([datetime.now().isoformat(), transaction_id, action, user])
        except Exception as e:
            logger.error(f"فشل إضافة سجل التتبع: {e}")

    def update_cell(self, sheet_name, row, col, value):
        ws = self.get_worksheet(sheet_name)
        if ws:
            ws.update_cell(row, col, value)