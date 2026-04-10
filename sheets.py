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

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    _headers_cache = None
    _headers_cache_time = None
    _department_sheets_cache = {}
    _department_archive_cache = {}

    _batch_queue = deque()
    _batch_lock = threading.Lock()
    _batch_thread = None
    _batch_stop = False

    WRITE_RATE_LIMIT = 250
    RATE_WINDOW = 60
    _write_timestamps = deque(maxlen=WRITE_RATE_LIMIT)
    _write_rate_lock = threading.Lock()

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
            logger.info("✅ تم الاتصال بـ Google Sheets")
            time.sleep(random.uniform(0.5, 2.0))
            self._init_sheets()
            self.drive_service = build('drive', 'v3', credentials=creds)
            self._start_batch_worker()
        except Exception as e:
            logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
            raise

    # ------------------ دوال التحكم في معدل الكتابة ------------------
    def _wait_for_write_rate(self):
        with self._write_rate_lock:
            while len(self._write_timestamps) >= self.WRITE_RATE_LIMIT:
                oldest = self._write_timestamps[0]
                elapsed = time.time() - oldest
                if elapsed < self.RATE_WINDOW:
                    sleep_time = self.RATE_WINDOW - elapsed + 0.1
                    logger.warning(f"⏳ تجاوز حد الكتابة، انتظار {sleep_time:.2f} ثانية")
                    time.sleep(sleep_time)
                else:
                    self._write_timestamps.popleft()
            self._write_timestamps.append(time.time())

    # ------------------ نظام الكتابة المجمعة (Batch) مع إصلاح الصيغ ------------------
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

    def _fix_formula_cells(self, worksheet, start_row, rows_data):
        """تصحيح الخلايا التي تبدأ بـ '=' في الصفوف المدرجة"""
        try:
            for offset, row_data in enumerate(rows_data):
                current_row = start_row + offset
                for col_idx, value in enumerate(row_data, start=1):
                    if isinstance(value, str) and value.startswith('='):
                        cell_addr = gspread.utils.rowcol_to_a1(current_row, col_idx)
                        # استخدام update بدلاً من update_acell (يدعم value_input_option)
                        worksheet.update(cell_addr, value, value_input_option='USER_ENTERED')
                        # فحص إضافي: إذا بقيت علامة اقتباس نزيلها
                        cell_value = worksheet.acell(cell_addr).value
                        if cell_value and isinstance(cell_value, str) and cell_value.startswith("'="):
                            clean = cell_value[1:]
                            worksheet.update(cell_addr, clean, value_input_option='USER_ENTERED')
                            logger.debug(f"✅ تمت إزالة علامة الاقتباس من الخلية {cell_addr}")
        except Exception as e:
            logger.error(f"خطأ في إصلاح الصيغ: {e}")

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
                    ws.insert_rows(rows, next_row, value_input_option='USER_ENTERED')
                    self._fix_formula_cells(ws, next_row, rows)
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
            self._fix_formula_cells(worksheet, next_row, [row_data])
            logger.debug(f"✅ تم إدراج صف جديد في الصف {next_row}")
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

    # ------------------ دوال التهيئة والأوراق ------------------
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

    # ------------------ دوال القائمة البيضاء ------------------
    def is_email_allowed(self, email: str) -> bool:
        try:
            from config import Config
            ws = self.get_worksheet(Config.SHEET_ALLOWED_EMAILS)
            if not ws:
                logger.warning("⚠️ ورقة allowed_emails غير موجودة، سيتم رفض كل الإيميلات")
                return False
            records = ws.get_all_records()
            email_lower = email.strip().lower()
            for row in records:
                if row.get('email', '').strip().lower() == email_lower:
                    return True
            return False
        except Exception as e:
            logger.error(f"خطأ في التحقق من البريد المسموح: {e}")
            return False

    # ------------------ دوال مساعدة عامة ------------------
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

    def get_headers_cached(self):
        if (self._headers_cache is None or 
            (datetime.now() - self._headers_cache_time).seconds > 300):
            ws = self.get_worksheet('manager')
            if ws:
                self._headers_cache = ws.row_values(1)
                self._headers_cache_time = datetime.now()
        return self._headers_cache

    def get_latest_transactions_fast(self, sheet_name):
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
        return list(latest.values())

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

    # ------------------ دوال إدارة شيتات الأقسام ------------------
    def _sanitize_sheet_name(self, name):
        name = re.sub(r'[\\/*?:\[\]]', '_', name)
        name = name[:100]
        return name.strip()

    def get_or_create_department_sheet_cached(self, department_name):
        sheet_name = self._sanitize_sheet_name(department_name)
        if sheet_name in self._department_sheets_cache:
            return self._department_sheets_cache[sheet_name]
        ws = self.get_or_create_department_sheet(department_name)
        if ws:
            self._department_sheets_cache[sheet_name] = ws
        return ws

    def get_or_create_department_sheet(self, department_name):
        sheet_name = self._sanitize_sheet_name(department_name)
        try:
            ws = self.spreadsheet.worksheet(sheet_name)
            return ws
        except gspread.WorksheetNotFound:
            manager_ws = self.get_worksheet('manager')
            if not manager_ws:
                logger.error("لا يمكن العثور على ورقة manager لإنشاء شيت القسم")
                return None
            headers = manager_ws.row_values(1)
            ws = self.spreadsheet.add_worksheet(title=sheet_name, rows=1, cols=len(headers))
            for col, header in enumerate(headers, 1):
                ws.update_cell(1, col, header)
            logger.info(f"✅ تم إنشاء شيت جديد للقسم: {sheet_name}")
            return ws
        except Exception as e:
            logger.error(f"فشل الحصول على شيت القسم {department_name}: {e}")
            return None

    def append_to_department_sheet(self, department_name, row_data, headers):
        if not department_name:
            return False
        ws = self.get_or_create_department_sheet_cached(department_name)
        if ws:
            try:
                self.safe_append_row(ws, row_data, batch=True)
                logger.debug(f"✅ تم إضافة المعاملة إلى شيت القسم: {department_name}")
                return True
            except Exception as e:
                logger.error(f"فشل إضافة الصف إلى شيت القسم {department_name}: {e}")
        return False

    def update_department_sheet(self, department_name, transaction_id, row_data, headers):
        ws = self.get_or_create_department_sheet_cached(department_name)
        if not ws:
            return False

        all_rows = ws.get_all_values()
        ws_headers = ws.row_values(1)
        try:
            id_col = ws_headers.index('ID') + 1
        except ValueError:
            return False

        row_num = None
        for i, row in enumerate(all_rows):
            if i == 0: continue
            if len(row) > id_col-1 and str(row[id_col-1]) == str(transaction_id):
                row_num = i + 1
                break

        if row_num:
            for col, header in enumerate(ws_headers, 1):
                if header in headers:
                    idx = headers.index(header)
                    ws.update_cell(row_num, col, row_data[idx])
            logger.info(f"✅ تم تحديث المعاملة {transaction_id} في شيت القسم {department_name}")
            return True
        else:
            self.safe_append_row(ws, row_data, batch=True)
            logger.info(f"✅ تم إضافة المعاملة {transaction_id} إلى شيت القسم {department_name}")
            return True

    def get_or_create_department_archive_cached(self, department_name):
        archive_name = f"أرشيف_{self._sanitize_sheet_name(department_name)}"
        if archive_name in self._department_archive_cache:
            return self._department_archive_cache[archive_name]
        try:
            ws = self.spreadsheet.worksheet(archive_name)
            self._department_archive_cache[archive_name] = ws
            return ws
        except gspread.WorksheetNotFound:
            arch_manager_ws = self.get_worksheet('archive_manager')
            if not arch_manager_ws:
                logger.error("لا يمكن العثور على archive_manager لإنشاء أرشيف القسم")
                return None
            headers = arch_manager_ws.row_values(1)
            ws = self.spreadsheet.add_worksheet(title=archive_name, rows=1, cols=len(headers))
            for col, header in enumerate(headers, 1):
                ws.update_cell(1, col, header)
            self._department_archive_cache[archive_name] = ws
            logger.info(f"✅ تم إنشاء شيت أرشيف للقسم: {archive_name}")
            return ws
        except Exception as e:
            logger.error(f"فشل إنشاء أرشيف القسم {department_name}: {e}")
            return None

    def archive_from_department(self, transaction_id, department_name, row_data, archive_time):
        dept_sheet = self.get_or_create_department_sheet_cached(department_name)
        if not dept_sheet:
            return False
        all_rows = dept_sheet.get_all_values()
        headers = dept_sheet.row_values(1)
        try:
            id_col = headers.index('ID') + 1
        except ValueError:
            return False
        row_num = None
        for i, row in enumerate(all_rows):
            if i == 0: continue
            if len(row) > id_col-1 and str(row[id_col-1]) == str(transaction_id):
                row_num = i + 1
                break
        if row_num:
            dept_sheet.delete_row(row_num)
        archive_sheet = self.get_or_create_department_archive_cached(department_name)
        if archive_sheet:
            row_data_with_archive = row_data.copy()
            try:
                headers_archive = archive_sheet.row_values(1)
                if 'تاريخ_الأرشفة' in headers_archive:
                    idx = headers_archive.index('تاريخ_الأرشفة')
                    if len(row_data_with_archive) < len(headers_archive):
                        row_data_with_archive.extend([''] * (len(headers_archive) - len(row_data_with_archive)))
                    row_data_with_archive[idx] = archive_time
                self.safe_append_row(archive_sheet, row_data_with_archive, batch=True)
                return True
            except Exception as e:
                logger.error(f"فشل إضافة المعاملة إلى أرشيف القسم {department_name}: {e}")
        return False

    # ------------------ الدوال الأساسية ------------------
    def add_history_entry(self, transaction_id, action, user="النظام"):
        try:
            ws = self.get_worksheet('history')
            if ws:
                now = datetime.now()
                timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
                self.safe_append_row(ws, [timestamp, transaction_id, action, user], batch=True)
        except Exception as e:
            logger.error(f"فشل إضافة سجل التتبع: {e}")

    def generate_access_token(self, transaction_id, email, expiry_minutes=1440):
        try:
            ws = self.get_worksheet('access_tokens')
            if not ws:
                logger.error("ورقة access_tokens غير موجودة")
                return None
            token = uuid.uuid4().hex
            expires_at = (datetime.now() + timedelta(minutes=expiry_minutes)).isoformat()
            self.safe_append_row(ws, [token, transaction_id, email, expires_at], batch=True)
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
            now = datetime.now()
            for row in records:
                if row.get('token') == token and str(row.get('transaction_id')) == str(transaction_id):
                    expires_at_str = row.get('expires_at')
                    if not expires_at_str:
                        continue
                    try:
                        expires_at = datetime.fromisoformat(expires_at_str)
                    except ValueError:
                        try:
                            expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            continue
                    if expires_at > now:
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

    def get_direct_token(self, transaction_id, expiry_minutes=43200):
        try:
            ws = self.get_worksheet('access_tokens')
            if not ws:
                logger.error("ورقة access_tokens غير موجودة")
                return None
            records = ws.get_all_records()
            now = datetime.now()
            for row in records:
                if str(row.get('transaction_id')) == str(transaction_id):
                    expires_at_str = row.get('expires_at')
                    if expires_at_str:
                        try:
                            expires_at = datetime.fromisoformat(expires_at_str)
                        except ValueError:
                            try:
                                expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
                            except ValueError:
                                continue
                        if expires_at > now:
                            return row.get('token')
            token = uuid.uuid4().hex
            expires_at = (now + timedelta(minutes=expiry_minutes)).isoformat()
            self.safe_append_row(ws, [token, transaction_id, "direct@system.com", expires_at], batch=True)
            logger.info(f"✅ تم توليد رمز مباشر للمعاملة {transaction_id} ينتهي بعد {expiry_minutes} دقيقة")
            return token
        except Exception as e:
            logger.error(f"فشل توليد رمز مباشر: {e}", exc_info=True)
            return None

    def verify_direct_token(self, token, transaction_id):
        return self.verify_access_token(token, transaction_id)

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
                dept_headers = self.get_or_create_department_sheet_cached(department_name).row_values(1)
                dept_row = [latest_data.get(header, '') for header in dept_headers]
                self.archive_from_department(transaction_id, department_name, dept_row, archive_time)

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

    # ------------------ دوال الإحصائيات المتقدمة ------------------
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

    def get_employee_stats(self):
        records = self.get_latest_transactions_fast('manager')
        stats = {}
        for r in records:
            emp = r.get('الموظف المسؤول', 'غير معروف')
            stats[emp] = stats.get(emp, 0) + 1
        return dict(sorted(stats.items(), key=lambda x: x[1], reverse=True))

    def get_status_distribution(self):
        records = self.get_latest_transactions_fast('manager')
        stats = {'جديد':0, 'قيد المعالجة':0, 'مكتملة':0, 'متأخرة':0, 'أخرى':0}
        for r in records:
            status = r.get('الحالة', 'أخرى')
            stats[status] = stats.get(status, 0) + 1
        return stats

    def get_recent_transactions(self, limit=10):
        return self.get_latest_transactions_sorted_fast('manager')[:limit]

    def get_transactions_by_department(self, department):
        return self.filter_transactions('manager', department=department)

    def get_transactions_by_employee(self, employee):
        return self.filter_transactions('manager', employee=employee)

    def get_employee_workload(self):
        records = self.get_latest_transactions_fast('manager')
        workload = {}
        for r in records:
            emp = r.get('الموظف المسؤول', 'غير معروف')
            if emp not in workload:
                workload[emp] = {'total':0, 'delayed':0}
            workload[emp]['total'] += 1
            if r.get('التأخير') == 'نعم':
                workload[emp]['delayed'] += 1
        return dict(sorted(workload.items(), key=lambda x: x[1]['total'], reverse=True))

    def get_department_workload(self):
        records = self.get_latest_transactions_fast('manager')
        workload = {}
        for r in records:
            dept = r.get('القسم', 'غير محدد')
            if dept not in workload:
                workload[dept] = {'total':0, 'delayed':0}
            workload[dept]['total'] += 1
            if r.get('التأخير') == 'نعم':
                workload[dept]['delayed'] += 1
        return dict(sorted(workload.items(), key=lambda x: x[1]['total'], reverse=True))

    # ------------------ رفع الملفات ------------------
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
