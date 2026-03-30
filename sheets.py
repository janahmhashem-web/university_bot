import os
import json
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from datetime import datetime, timedelta
import uuid
import tempfile

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
            # افتح الجدول باستخدام المعرف أو الاسم – هنا نستخدم الاسم
            self.spreadsheet = self.client.open("university system")
            logger.info("✅ تم الاتصال بـ Google Sheets")
            self._init_sheets()
            self.drive_service = build('drive', 'v3', credentials=creds)
        except Exception as e:
            logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
            raise

    def _init_sheets(self):
        from config import Config

        sheets_required = {
            Config.SHEET_MANAGER: [
                "Timestamp", "اسم صاحب المعاملة الثلاثي", "رقم الهاتف",
                "الوظيفة", "القسم", "نوع المعاملة", "المرافقات", "ID", "الحالة", "الأولوية",
                "الموظف المسؤول", "المؤسسة الحالية", "المؤسسة التالية", "تاريخ التحويل",
                "سبب التحويل", "الموافق", "ملاحظات إضافية", "آخر إجراء", "التأخير",
                "المستمسكات المطلوبة", "الرابط", "آخر تعديل بواسطة", "آخر تعديل بتاريخ",
                "عدد التعديلات", "البريد الإلكتروني الموظف", "LOG_JSON"
            ],
            Config.SHEET_HISTORY: ["timestamp", "ID", "action", "user"],
            Config.SHEET_QR: ["name", "transaction_id", "view_link", "qr_image_url", "qr_page_link", "edit_link"],
            Config.SHEET_USERS: ["transaction_id", "chat_id"],
            Config.SHEET_ACCESS_TOKENS: ["token", "transaction_id", "email", "expires_at"]
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

    # دوال الرموز المؤقتة
    def generate_access_token(self, transaction_id, email, expiry_minutes=60):
        """توليد رمز وصول مؤقت مرتبط بالبريد الإلكتروني"""
        try:
            ws = self.get_worksheet('access_tokens')
            if not ws:
                return None
            token = uuid.uuid4().hex
            expires_at = (datetime.now() + timedelta(minutes=expiry_minutes)).isoformat()
            ws.append_row([token, transaction_id, email, expires_at])
            return token
        except Exception as e:
            logger.error(f"فشل توليد رمز الوصول: {e}")
            return None

    def verify_access_token(self, token, transaction_id):
        """التحقق من صحة الرمز (لم ينتهِ ولنفس المعاملة)"""
        try:
            ws = self.get_worksheet('access_tokens')
            if not ws:
                return False
            records = ws.get_all_records()
            now = datetime.now().isoformat()
            for row in records:
                if row.get('token') == token and str(row.get('transaction_id')) == str(transaction_id):
                    expires_at = row.get('expires_at')
                    if expires_at and expires_at > now:
                        return True
            return False
        except Exception as e:
            logger.error(f"فشل التحقق من رمز الوصول: {e}")
            return False

    def revoke_access_token(self, token):
        """حذف الرمز بعد الاستخدام (اختياري)"""
        try:
            ws = self.get_worksheet('access_tokens')
            if not ws:
                return
            records = ws.get_all_records()
            for idx, row in enumerate(records):
                if row.get('token') == token:
                    ws.delete_row(idx+2)
                    break
        except Exception as e:
            logger.error(f"فشل إبطال رمز الوصول: {e}")

    # رفع الملفات إلى Drive
    def upload_file_to_drive(self, file_data, filename, folder_name="Transaction Attachments"):
        try:
            folder_id = self._get_or_create_folder(folder_name)
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as tmp:
                tmp.write(file_data)
                tmp_path = tmp.name

            media = MediaFileUpload(tmp_path, resumable=True)
            file_metadata = {'name': filename, 'parents': [folder_id]}
            file = self.drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            file_id = file.get('id')
            self.drive_service.permissions().create(
                fileId=file_id,
                body={'type': 'anyone', 'role': 'reader'}
            ).execute()
            os.unlink(tmp_path)
            return f"https://drive.google.com/uc?export=view&id={file_id}"
        except Exception as e:
            logger.error(f"فشل رفع الملف إلى Drive: {e}")
            return None

    def _get_or_create_folder(self, folder_name):
        try:
            response = self.drive_service.files().list(
                q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='files(id, name)'
            ).execute()
            folders = response.get('files', [])
            if folders:
                return folders[0]['id']
            else:
                folder_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
                folder = self.drive_service.files().create(body=folder_metadata, fields='id').execute()
                return folder.get('id')
        except Exception as e:
            logger.error(f"فشل البحث/إنشاء المجلد: {e}")
            raise