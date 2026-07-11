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
import re
from collections import deque
import threading
from cachetools import TTLCache
import jwt

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    def __init__(self):
        from config import Config
        self.config = Config
        self._data_cache = TTLCache(maxsize=10, ttl=Config.CACHE_TTL)

        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if not creds_json:
                raise ValueError("GOOGLE_CREDENTIALS_JSON not set")
            creds_dict = json.loads(creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(Config.SPREADSHEET_ID)
            logger.info("✅ تم الاتصال بـ Google Sheets")
            self.drive_service = build('drive', 'v3', credentials=creds)
            self._init_sheets()
            self._init_employees_sheet()
            self._init_audit_sheets()
            self._start_batch_worker()
        except Exception as e:
            logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
            raise

    # ================== تهيئة الأوراق ==================
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
            Config.SHEET_QR: ["transaction_id", "qr_image", "qr_verify_link"],
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
            Config.SHEET_ARCHIVE_HISTORY: ["timestamp", "ID", "action", "user", "تاريخ_الأرشفة"],
            Config.SHEET_ALLOWED_EMAILS: ["email", "name", "role"],
            "chat_history": ["timestamp", "user_id", "user_name", "user_message", "ai_response", "is_admin"],
            "ml_training_data": ["text", "label", "timestamp"],
            "ml_feedback": ["timestamp", "user_id", "user_message", "ai_response", "helpful", "processed"],
            "user_preferences": ["user_id", "preference", "value", "updated_at"],
        }

        for sheet_name, required_headers in sheets_required.items():
            try:
                ws = self.get_worksheet(sheet_name)
                if ws is None:
                    ws = self.spreadsheet.add_worksheet(title=sheet_name, rows=1, cols=len(required_headers))
                    for col, header in enumerate(required_headers, 1):
                        ws.update_cell(1, col, header)
                    logger.info(f"✅ تم إنشاء الورقة '{sheet_name}'")
            except Exception as e:
                logger.error(f"❌ فشل إعداد الورقة {sheet_name}: {e}")

    def _init_employees_sheet(self):
        try:
            ws = self.get_worksheet('employees')
            if ws is None:
                headers = ['email', 'name', 'role', 'department', 'created_at', 'last_active']
                ws = self.spreadsheet.add_worksheet(title='employees', rows=1, cols=len(headers))
                for col, header in enumerate(headers, 1):
                    ws.update_cell(1, col, header)
                logger.info("✅ تم إنشاء ورقة الموظفين")
        except Exception as e:
            logger.error(f"❌ فشل إنشاء ورقة الموظفين: {e}")

    def _init_audit_sheets(self):
        try:
            for sheet_name, headers in [
                ('audit_log', ['timestamp', 'transaction_id', 'field_name', 'old_value', 'new_value', 'changed_by', 'ip_address']),
                ('employee_activity', ['timestamp', 'email', 'action', 'details', 'ip', 'success', 'user_agent'])
            ]:
                ws = self.get_worksheet(sheet_name)
                if ws is None:
                    ws = self.spreadsheet.add_worksheet(title=sheet_name, rows=1, cols=len(headers))
                    for col, header in enumerate(headers, 1):
                        ws.update_cell(1, col, header)
                    logger.info(f"✅ تم إنشاء ورقة {sheet_name}")
        except Exception as e:
            logger.error(f"❌ فشل إنشاء أوراق التدقيق: {e}")

    # ================== نظام الكتابة المجمعة ==================
    _batch_queue = deque()
    _batch_lock = threading.Lock()
    _batch_thread = None
    _batch_stop = False

    WRITE_RATE_LIMIT = 250
    RATE_WINDOW = 60
    _write_timestamps = deque(maxlen=WRITE_RATE_LIMIT)
    _write_rate_lock = threading.Lock()

    def _wait_for_write_rate(self):
        with self._write_rate_lock:
            while len(self._write_timestamps) >= self.WRITE_RATE_LIMIT:
                oldest = self._write_timestamps[0]
                elapsed = time.time() - oldest
                if elapsed < self.RATE_WINDOW:
                    sleep_time = self.RATE_WINDOW - elapsed + 0.1
                    time.sleep(sleep_time)
                else:
                    self._write_timestamps.popleft()
            self._write_timestamps.append(time.time())

    def _start_batch_worker(self):
        def worker():
            while not self._batch_stop:
                time.sleep(0.5)
                batch = []
                with self._batch_lock:
                    while self._batch_queue and len(batch) < 50:
                        batch.append(self._batch_queue.popleft())
                if batch:
                    self._execute_batch(batch)
        self._batch_thread = threading.Thread(target=worker, daemon=True)
        self._batch_thread.start()

    def _execute_batch(self, batch_items):
        if not batch_items:
            return
        self._wait_for_write_rate()
        try:
            sheets_ops = {}
            for item in batch_items:
                sheet_name = item['sheet_name']
                if sheet_name not in sheets_ops:
                    sheets_ops[sheet_name] = []
                sheets_ops[sheet_name].append(item['row_data'])
            for sheet_name, rows in sheets_ops.items():
                ws = self.get_worksheet(sheet_name)
                if ws:
                    all_values = ws.get_all_values()
                    next_row = len(all_values) + 1
                    ws.insert_rows(rows, next_row, value_input_option='RAW')
                    logger.debug(f"✅ Batch inserted {len(rows)} rows into {sheet_name}")
        except Exception as e:
            logger.error(f"❌ Batch insert failed: {e}")
            for item in batch_items:
                self._safe_append_row_single(item['worksheet'], item['row_data'])

    def queue_append_row(self, worksheet, row_data):
        try:
            sheet_name = worksheet.title
            with self._batch_lock:
                self._batch_queue.append({
                    'sheet_name': sheet_name,
                    'worksheet': worksheet,
                    'row_data': row_data.copy()
                })
            return True
        except Exception as e:
            logger.error(f"Failed to queue row: {e}")
            return False

    def _safe_append_row_single(self, worksheet, row_data):
        try:
            existing_headers = worksheet.row_values(1)
            if not existing_headers:
                return False
            if len(row_data) != len(existing_headers):
                if len(row_data) < len(existing_headers):
                    row_data.extend([''] * (len(existing_headers) - len(row_data)))
                else:
                    row_data = row_data[:len(existing_headers)]
            all_values = worksheet.get_all_values()
            next_row = len(all_values) + 1
            worksheet.insert_row(row_data, next_row, value_input_option='RAW')
            return True
        except Exception as e:
            logger.error(f"❌ فشل إدراج الصف: {e}")
            return False

    def safe_append_row(self, worksheet, row_data, batch=True):
        if batch:
            return self.queue_append_row(worksheet, row_data)
        else:
            self._wait_for_write_rate()
            return self._safe_append_row_single(worksheet, row_data)

    # ================== دوال القراءة الأساسية ==================
    def get_worksheet(self, sheet_name):
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except Exception:
            return None

    def get_latest_transactions_fast(self, sheet_name):
        cache_key = f"transactions_{sheet_name}"
        if cache_key in self._data_cache:
            return self._data_cache[cache_key]
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
            if len(row) < len(headers):
                row.extend([''] * (len(headers) - len(row)))
            row_dict = dict(zip(headers, row))
            transaction_id = str(row_dict.get("ID"))
            latest[transaction_id] = row_dict
        result = list(latest.values())
        self._data_cache[cache_key] = result
        return result

    def get_latest_transactions_sorted_fast(self, sheet_name):
        data = self.get_latest_transactions_fast(sheet_name)
        def parse_date(row):
            try:
                return datetime.strptime(row.get("آخر تعديل بتاريخ", ""), "%Y-%m-%d %H:%M:%S")
            except:
                return datetime.min
        return sorted(data, key=parse_date, reverse=True)

    def filter_transactions(self, sheet_name, status=None, employee=None, department=None):
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

    def get_latest_row_by_id_fast(self, sheet_name, transaction_id):
        data = self.get_latest_transactions_fast(sheet_name)
        for row in data:
            if str(row.get("ID")) == str(transaction_id):
                return row
        return None

    # ================== دوال التحديث ==================
    def update_transaction_field(self, transaction_id, field_name, new_value):
        try:
            ws = self.get_worksheet('manager')
            if not ws:
                return False
            headers = ws.row_values(1)
            if field_name not in headers:
                return False
            col = headers.index(field_name) + 1
            id_col = headers.index('ID') + 1
            cell = ws.find(str(transaction_id), in_column=id_col)
            if not cell:
                return False
            ws.update_cell(cell.row, col, new_value, value_input_option='USER_ENTERED')
            self._data_cache.clear()
            return True
        except Exception as e:
            logger.error(f"فشل update_transaction_field: {e}")
            return False

    def add_history_entry(self, transaction_id, action, user="النظام"):
        try:
            ws = self.get_worksheet('history')
            if ws:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.safe_append_row(ws, [now, transaction_id, action, user], batch=True)
        except Exception as e:
            logger.error(f"فشل إضافة سجل التتبع: {e}")

    # ================== إدارة الموظفين والصلاحيات ==================
    def get_employee_role(self, email):
        try:
            ws = self.get_worksheet('employees')
            if not ws:
                return None
            records = ws.get_all_records()
            email_lower = email.strip().lower()
            for row in records:
                if row.get('email', '').strip().lower() == email_lower:
                    return row.get('role', '').strip().lower()
            return None
        except Exception as e:
            logger.error(f"خطأ في جلب دور الموظف: {e}")
            return None

    def is_qr_authorized(self, email, required_role='viewer'):
        role = self.get_employee_role(email)
        if not role:
            return False
        role_hierarchy = {'admin': 3, 'qr_operator': 2, 'viewer': 1}
        required_level = role_hierarchy.get(required_role, 0)
        user_level = role_hierarchy.get(role, 0)
        return user_level >= required_level

    def get_all_employees(self):
        try:
            ws = self.get_worksheet('employees')
            if not ws:
                return []
            return ws.get_all_records()
        except Exception as e:
            logger.error(f"فشل جلب الموظفين: {e}")
            return []

    def add_employee(self, email, name, role, department=''):
        try:
            ws = self.get_worksheet('employees')
            if not ws:
                return False
            records = ws.get_all_records()
            email_lower = email.strip().lower()
            for row in records:
                if row.get('email', '').strip().lower() == email_lower:
                    return False
            now = datetime.now().isoformat()
            ws.append_row([email.strip(), name.strip(), role.strip().lower(), department.strip(), now, ''])
            return True
        except Exception as e:
            logger.error(f"فشل إضافة الموظف: {e}")
            return False

    def update_employee_role(self, email, new_role):
        try:
            ws = self.get_worksheet('employees')
            if not ws:
                return False
            records = ws.get_all_records()
            email_lower = email.strip().lower()
            for i, row in enumerate(records, start=2):
                if row.get('email', '').strip().lower() == email_lower:
                    ws.update_cell(i, 3, new_role.strip().lower())
                    return True
            return False
        except Exception as e:
            logger.error(f"فشل تحديث دور الموظف: {e}")
            return False

    def delete_employee(self, email):
        try:
            ws = self.get_worksheet('employees')
            if not ws:
                return False
            records = ws.get_all_records()
            email_lower = email.strip().lower()
            for i, row in enumerate(records, start=2):
                if row.get('email', '').strip().lower() == email_lower:
                    ws.delete_row(i)
                    return True
            return False
        except Exception as e:
            logger.error(f"فشل حذف الموظف: {e}")
            return False

    # ================== JWT والتوكنات ==================
    def generate_access_token(self, transaction_id, email, expiry_days=None):
        from config import Config
        if expiry_days is None:
            expiry_days = Config.TOKEN_EXPIRY_DAYS
        payload = {
            'transaction_id': transaction_id,
            'email': email,
            'exp': datetime.utcnow() + timedelta(days=expiry_days)
        }
        token = jwt.encode(payload, Config.JWT_SECRET, algorithm='HS256')
        return token

    def verify_access_token(self, token, transaction_id):
        from config import Config
        try:
            payload = jwt.decode(token, Config.JWT_SECRET, algorithms=['HS256'])
            return payload.get('transaction_id') == str(transaction_id)
        except jwt.ExpiredSignatureError:
            return False
        except jwt.InvalidTokenError:
            return False

    def get_direct_token(self, transaction_id, expiry_days=None):
        return self.generate_access_token(transaction_id, "direct@system.com", expiry_days)

    # ================== سجل التدقيق (Audit Log) ==================
    def log_audit_change(self, transaction_id, field_name, old_value, new_value, changed_by, ip_address=''):
        try:
            ws = self.get_worksheet('audit_log')
            if not ws:
                return
            now = datetime.now().isoformat()
            ws.append_row([now, str(transaction_id), str(field_name), str(old_value), str(new_value), str(changed_by), str(ip_address)])
        except Exception as e:
            logger.error(f"فشل تسجيل تغيير التدقيق: {e}")

    def get_audit_log(self, transaction_id, limit=50):
        try:
            ws = self.get_worksheet('audit_log')
            if not ws:
                return []
            records = ws.get_all_records()
            filtered = [r for r in records if str(r.get('transaction_id')) == str(transaction_id)]
            filtered.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            return filtered[:limit]
        except Exception as e:
            logger.error(f"فشل جلب سجل التدقيق: {e}")
            return []

    def get_audit_log_grouped(self, transaction_id):
        log = self.get_audit_log(transaction_id, limit=100)
        if not log:
            return []
        versions = {}
        for entry in log:
            timestamp = entry.get('timestamp', '')
            if '.' in timestamp:
                timestamp = timestamp.split('.')[0]
            if timestamp not in versions:
                versions[timestamp] = {
                    'timestamp': timestamp,
                    'changed_by': entry.get('changed_by', ''),
                    'changes': []
                }
            versions[timestamp]['changes'].append({
                'field': entry.get('field_name', ''),
                'old': entry.get('old_value', ''),
                'new': entry.get('new_value', '')
            })
        return list(versions.values())

    # ================== نشاط الموظفين ==================
    def log_employee_activity(self, email, action, details='', success=True, ip_address=''):
        try:
            ws = self.get_worksheet('employee_activity')
            if not ws:
                return
            now = datetime.now().isoformat()
            ws.append_row([now, email, action, details, ip_address, '1' if success else '0', ''])
        except Exception as e:
            logger.error(f"فشل تسجيل نشاط الموظف: {e}")

    def get_employee_activity(self, email=None, limit=50, only_failed=False):
        try:
            ws = self.get_worksheet('employee_activity')
            if not ws:
                return []
            records = ws.get_all_records()
            if email:
                email_lower = email.strip().lower()
                records = [r for r in records if r.get('email', '').strip().lower() == email_lower]
            if only_failed:
                records = [r for r in records if r.get('success') == '0' or r.get('success') == 0]
            records.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            return records[:limit]
        except Exception as e:
            logger.error(f"فشل جلب سجل النشاط: {e}")
            return []

    def get_unauthorized_attempts(self, limit=20):
        return self.get_employee_activity(only_failed=True, limit=limit)

    def get_employee_stats(self, email):
        try:
            ws = self.get_worksheet('employee_activity')
            if not ws:
                return {'total': 0, 'success': 0, 'failed': 0}
            records = ws.get_all_records()
            email_lower = email.strip().lower()
            user_records = [r for r in records if r.get('email', '').strip().lower() == email_lower]
            total = len(user_records)
            success = sum(1 for r in user_records if r.get('success') == '1' or r.get('success') == 1)
            failed = total - success
            return {'total': total, 'success': success, 'failed': failed}
        except Exception as e:
            logger.error(f"فشل جلب إحصائيات الموظف: {e}")
            return {'total': 0, 'success': 0, 'failed': 0}

    # ================== دوال إدارة المعاملات ==================
    def is_transaction_editable(self, transaction_id):
        data = self.get_latest_row_by_id_fast('manager', transaction_id)
        if not data:
            return False
        status = data.get('الحالة', '')
        if status in ['مكتملة', 'مؤرشفة']:
            return False
        return True

    def archive_completed_transaction(self, transaction_id):
        data = self.get_latest_row_by_id_fast('manager', transaction_id)
        if not data or data.get('الحالة') != 'مكتملة':
            return False
        return self.update_transaction_field(transaction_id, 'الحالة', 'مؤرشفة')

    def get_delayed_transactions(self):
        records = self.get_latest_transactions_fast('manager')
        return [r for r in records if r.get('التأخير') == 'نعم']

    def get_transactions_by_name(self, name):
        records = self.get_latest_transactions_fast('manager')
        name_clean = name.strip().lower()
        return [r for r in records if name_clean in r.get('اسم صاحب المعاملة الثلاثي', '').strip().lower()]

    # ================== الإحصائيات المتقدمة ==================
    def get_distinct_departments(self):
        records = self.get_latest_transactions_fast('manager')
        return sorted(set(r.get('القسم', '').strip() for r in records if r.get('القسم')))

    def get_distinct_employees(self):
        records = self.get_latest_transactions_fast('manager')
        return sorted(set(r.get('الموظف المسؤول', '').strip() for r in records if r.get('الموظف المسؤول') and r.get('الموظف المسؤول') != 'غير معروف'))

    def get_department_stats(self):
        records = self.get_latest_transactions_fast('manager')
        stats = {}
        for r in records:
            dept = r.get('القسم', 'غير محدد')
            stats[dept] = stats.get(dept, 0) + 1
        return dict(sorted(stats.items(), key=lambda x: x[1], reverse=True))

    def get_status_distribution(self):
        records = self.get_latest_transactions_fast('manager')
        stats = {}
        for r in records:
            status = r.get('الحالة', 'أخرى')
            stats[status] = stats.get(status, 0) + 1
        return stats

    def get_recent_transactions(self, limit=10):
        return self.get_latest_transactions_sorted_fast('manager')[:limit]

    def get_employee_workload(self):
        records = self.get_latest_transactions_fast('manager')
        workload = {}
        for r in records:
            emp = r.get('الموظف المسؤول', 'غير معروف')
            if emp not in workload:
                workload[emp] = {'total': 0, 'delayed': 0}
            workload[emp]['total'] += 1
            if r.get('التأخير') == 'نعم':
                workload[emp]['delayed'] += 1
        return dict(sorted(workload.items(), key=lambda x: x[1]['total'], reverse=True))

    # ================== رفع الملفات والأرشفة ==================
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

    def archive_transaction(self, transaction_id, department_name=None):
        try:
            ws_manager = self.get_worksheet('manager')
            if not ws_manager:
                return False
            latest_data = self.get_latest_row_by_id_fast('manager', transaction_id)
            if not latest_data:
                return False
            archive_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            latest_data['تاريخ_الأرشفة'] = archive_time

            ws_archive_manager = self.get_worksheet('archive_manager')
            if not ws_archive_manager:
                return False
            headers = ws_archive_manager.row_values(1)
            new_row = [latest_data.get(header, '') for header in headers]
            self.safe_append_row(ws_archive_manager, new_row, batch=True)

            all_rows = ws_manager.get_all_values()
            headers_mgr = ws_manager.row_values(1)
            id_col = None
            try:
                id_col = headers_mgr.index('ID') + 1
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

            if department_name:
                # أرشفة من شيت القسم (اختصاراً)
                pass

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
                        self.safe_append_row(ws_archive_history, arch_row, batch=True)
                    rows_to_delete = []
                    for i, r in enumerate(records):
                        if str(r.get('ID')) == str(transaction_id):
                            rows_to_delete.append(i+2)
                    for row_num in sorted(rows_to_delete, reverse=True):
                        ws_history.delete_row(row_num)
            return True
        except Exception as e:
            logger.error(f"فشل أرشفة المعاملة {transaction_id}: {e}")
            return False

    # ================== دوال الأقسام (اختصار) ==================
    def append_to_department_sheet(self, department_name, row_data, headers):
        # تبسيط: يمكن تنفيذها حسب الحاجة
        return True

    def update_department_sheet(self, department_name, transaction_id, row_data, headers):
        return True
