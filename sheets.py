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
import time
import random

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
            from config import Config
            self.spreadsheet = self.client.open_by_key(Config.SPREADSHEET_ID)
            logger.info("✅ تم الاتصال بـ Google Sheets (بواسطة المعرف)")
            time.sleep(random.uniform(0.5, 2.0))
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
            Config.SHEET_ACCESS_TOKENS: ["token", "transaction_id", "email", "expires_at"],
            Config.SHEET_ARCHIVE_MANAGER: [
                "Timestamp", "اسم صاحب المعاملة الثلاثي", "رقم الهاتف",
                "الوظيفة", "القسم", "نوع المعاملة", "المرافقات", "ID", "الحالة", "الأولوية",
                "الموظف المسؤول", "المؤسسة الحالية", "المؤسسة التالية", "تاريخ التحويل",
                "سبب التحويل", "الموافق", "ملاحظات إضافية", "آخر إجراء", "التأخير",
                "المستمسكات المطلوبة", "الرابط", "آخر تعديل بواسطة", "آخر تعديل بتاريخ",
                "عدد التعديلات", "البريد الإلكتروني الموظف", "LOG_JSON", "تاريخ_الأرشفة"
            ],
            Config.SHEET_ARCHIVE_HISTORY: ["timestamp", "ID", "action", "user", "تاريخ_الأرشفة"]
        }

        for sheet_name, required_headers in sheets_required.items():
            for attempt in range(3):
                try:
                    ws = self.get_worksheet(sheet_name)
                    if ws is None:
                        ws = self.spreadsheet.add_worksheet(title=sheet_name, rows=1, cols=len(required_headers))
                        for col, header in enumerate(required_headers, 1):
                            ws.update_cell(1, col, header)
                        logger.info(f"✅ تم إنشاء الورقة '{sheet_name}' مع الأعمدة المطلوبة")
                        break
                    else:
                        existing_headers = ws.row_values(1)
                        for col, header in enumerate(required_headers, 1):
                            if header not in existing_headers:
                                ws.update_cell(1, col, header)
                                logger.info(f"✅ تم إضافة العمود '{header}' إلى الورقة '{sheet_name}'")
                        break
                except gspread.exceptions.APIError as e:
                    if e.response.status_code == 429:
                        wait = (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(f"⚠️ تجاوز الحصة (429) للورقة {sheet_name}، إعادة المحاولة بعد {wait:.2f} ثانية...")
                        time.sleep(wait)
                    else:
                        logger.error(f"❌ فشل إعداد الورقة {sheet_name}: {e}")
                        break
                except Exception as e:
                    logger.error(f"❌ فشل إعداد الورقة {sheet_name}: {e}")
                    break

    def get_worksheet(self, sheet_name):
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except Exception:
            return None

    # ------------------ الدوال القديمة (للتوافق) ------------------
    def get_all_records(self, sheet_name):
        ws = self.get_worksheet(sheet_name)
        if ws:
            return ws.get_all_records()
        return []

    def get_latest_row_by_id(self, sheet_name, transaction_id):
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return None
        records = ws.get_all_records()
        latest = None
        latest_row = 0
        for idx, row in enumerate(records):
            if str(row.get('ID')) == str(transaction_id):
                if idx + 2 > latest_row:
                    latest_row = idx + 2
                    latest = {'row': latest_row, 'data': row}
        return latest

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
                now = datetime.now()
                timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
                ws.append_row([timestamp, transaction_id, action, user])
        except Exception as e:
            logger.error(f"فشل إضافة سجل التتبع: {e}")

    def update_cell(self, sheet_name, row, col, value):
        ws = self.get_worksheet(sheet_name)
        if ws:
            ws.update_cell(row, col, value)

    # ------------------ دوال الرموز المؤقتة ------------------
    def generate_access_token(self, transaction_id, email, expiry_minutes=60):
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

    # ------------------ دوال الأرشفة ------------------
    def archive_transaction(self, transaction_id):
        try:
            ws_manager = self.get_worksheet('manager')
            if not ws_manager:
                return False
            row_info = self.get_latest_row_by_id('manager', transaction_id)
            if not row_info:
                return False
            row_number = row_info['row']
            data = row_info['data']

            now = datetime.now()
            archive_time = now.strftime("%Y-%m-%d %H:%M:%S")
            data['تاريخ_الأرشفة'] = archive_time

            ws_archive_manager = self.get_worksheet('archive_manager')
            if not ws_archive_manager:
                logger.error("ورقة archive_manager غير موجودة")
                return False
            headers = ws_archive_manager.row_values(1)
            new_row = [data.get(header, '') for header in headers]
            ws_archive_manager.append_row(new_row)

            ws_history = self.get_worksheet('history')
            if ws_history:
                records = ws_history.get_all_records()
                history_rows = [r for r in records if str(r.get('ID')) == str(transaction_id)]
                ws_archive_history = self.get_worksheet('archive_history')
                if ws_archive_history:
                    arch_headers = ws_archive_history.row_values(1)
                    for hist in history_rows:
                        hist['تاريخ_الأرشفة'] = archive_time
                        arch_row = [hist.get(header, '') for header in arch_headers]
                        ws_archive_history.append_row(arch_row)
                    rows_to_delete = []
                    for i, r in enumerate(records):
                        if str(r.get('ID')) == str(transaction_id):
                            rows_to_delete.append(i+2)
                    for row_num in sorted(rows_to_delete, reverse=True):
                        ws_history.delete_row(row_num)

            # حذف جميع صفوف المعاملة من manager
            all_rows = ws_manager.get_all_values()
            headers = ws_manager.row_values(1)
            id_col = None
            try:
                id_col = headers.index('ID') + 1
            except ValueError:
                pass
            if id_col:
                rows_to_delete = []
                for i, row in enumerate(all_rows):
                    if i == 0: continue
                    if len(row) > id_col-1 and str(row[id_col-1]) == str(transaction_id):
                        rows_to_delete.append(i+1)
                for row_num in sorted(rows_to_delete, reverse=True):
                    ws_manager.delete_row(row_num)
            return True
        except Exception as e:
            logger.error(f"فشل أرشفة المعاملة {transaction_id}: {e}", exc_info=True)
            return False

    # ------------------ دوال رفع الملفات ------------------
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

    # ================== التحسينات السريعة الجديدة ==================
    def get_latest_transactions_fast(self, sheet_name):
        """إرجاع أحدث نسخة لكل معاملة (سريع باستخدام get_all_values)"""
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return []

        data = ws.get_all_values()
        if len(data) < 2:
            return []

        headers = data[0]
        rows = data[1:]

        latest = {}

        for row in rows:
            row_dict = dict(zip(headers, row))
            transaction_id = str(row_dict.get("ID"))

            # نخلي آخر نسخة (الأحدث)
            latest[transaction_id] = row_dict

        return list(latest.values())

    def get_latest_transactions_sorted_fast(self, sheet_name):
        """إرجاع أحدث المعاملات مرتبة حسب آخر تعديل (الأحدث أولاً)"""
        data = self.get_latest_transactions_fast(sheet_name)

        def parse_date(row):
            try:
                return datetime.strptime(row.get("آخر تعديل بتاريخ", ""), "%Y-%m-%d %H:%M:%S")
            except:
                return datetime.min

        return sorted(data, key=parse_date, reverse=True)

    def filter_transactions(self, sheet_name, status=None, employee=None, department=None):
        """فلترة المعاملات حسب الحالة، الموظف المسؤول، القسم"""
        data = self.get_latest_transactions_fast(sheet_name)

        results = []

        for row in data:
            if status and row.get("الحالة") != status:
                continue
            if employee and row.get("الموظف المسؤول") != employee:
                continue
            if department and row.get("القسم") != department:
                continue

            results.append(row)

        return results

    def filter_and_sort_transactions(self, sheet_name, status=None, employee=None):
        """فلترة وترتيب (أسرع طريقة)"""
        data = self.filter_transactions(sheet_name, status, employee)

        def parse_date(row):
            try:
                return datetime.strptime(row.get("آخر تعديل بتاريخ", ""), "%Y-%m-%d %H:%M:%S")
            except:
                return datetime.min

        return sorted(data, key=parse_date, reverse=True)

    def get_latest_row_by_id_fast(self, sheet_name, transaction_id):
        """جلب أحدث صف لمعاملة معينة (سريع)"""
        data = self.get_latest_transactions_fast(sheet_name)

        for row in data:
            if str(row.get("ID")) == str(transaction_id):
                return row

        return None