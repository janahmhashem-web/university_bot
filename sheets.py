import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import logging
from datetime import datetime
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
            # تأكد من أن اسم الجدول مطابق تماماً لما في Google Sheets
            self.spreadsheet = self.client.open("university system")  # غيّر الاسم إذا كان مختلفاً
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
                logger.info(f"📝 تم إضافة سجل تاريخ للمعاملة {transaction_id}")
        except Exception as e:
            logger.error(f"فشل إضافة سجل التتبع: {e}")

    def update_cell(self, sheet_name, row, col, value):
        ws = self.get_worksheet(sheet_name)
        if ws:
            ws.update_cell(row, col, value)

    # ------------------ رفع الملفات إلى Google Drive ------------------
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