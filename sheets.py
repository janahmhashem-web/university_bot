import gspread
from google.oauth2.service_account import Credentials
import logging
from config import Config

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.connect()
    
    def connect(self):
        try:
            scope = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = Credentials.from_service_account_file(
                Config.GOOGLE_CREDENTIALS_FILE, 
                scopes=scope
            )
            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(Config.SPREADSHEET_ID)
            logger.info("✅ Connected to Google Sheets")
        except Exception as e:
            logger.error(f"❌ Failed to connect: {e}")
            raise
    
    def get_worksheet(self, sheet_name):
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return None
    
    def create_worksheet(self, sheet_name, rows=100, cols=26):
        """إنشاء ورقة جديدة إذا لم تكن موجودة"""
        return self.spreadsheet.add_worksheet(sheet_name, rows, cols)
    
    def ensure_sheets_exist(self):
        """التأكد من وجود جميع الأوراق المطلوبة، وإنشائها إذا لم تكن موجودة"""
        required_sheets = [
            Config.SHEET_MANAGER,
            Config.SHEET_QR,
            Config.SHEET_ARCHIVE,
            Config.SHEET_HISTORY
        ]
        
        for sheet in required_sheets:
            if not self.get_worksheet(sheet):
                ws = self.create_worksheet(sheet)
                logger.info(f"✅ Created sheet: {sheet}")
                # إضافة العناوين إذا كانت الورقة جديدة
                if sheet == Config.SHEET_MANAGER:
                    headers = [
                        'Timestamp', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف', 'البريد الإلكتروني',
                        'القسم', 'نوع المعاملة', 'المرافقات', 'ID', 'الحالة', 'الأولوية',
                        'الموظف المسؤول', 'المؤسسة الحالية', 'المؤسسة التالية', 'تاريخ التحويل',
                        'سبب التحويل', 'الموافق', 'ملاحظات إضافية', 'آخر إجراء', 'التأخير',
                        'المستمسكات المطلوبة', 'الرابط', 'آخر تعديل بواسطة', 'آخر تعديل بتاريخ',
                        'عدد التعديلات', 'البريد الإلكتروني الموظف', 'LOG_JSON'
                    ]
                    ws.append_row(headers)
                elif sheet == Config.SHEET_QR:
                    ws.append_row(['الطابع الزمني', 'اسم صاحب المعاملة', 'ID', 'الرابط', 'QR Code', 'رابط الصورة'])
                elif sheet == Config.SHEET_ARCHIVE:
                    # العناوين نفس manager مع إضافة تاريخ الأرشفة
                    manager_headers = self.get_worksheet(Config.SHEET_MANAGER).row_values(1)
                    ws.append_row(manager_headers + ['تاريخ الأرشفة'])
                elif sheet == Config.SHEET_HISTORY:
                    manager_headers = self.get_worksheet(Config.SHEET_MANAGER).row_values(1)
                    history_headers = manager_headers + ['المؤسسة السابقة', 'المؤسسة الحالية بعد النقل', 'الموظف المسؤول', 'الإجراء', 'نوع الإجراء', 'تاريخ التتبع', 'رابط المعاملة']
                    ws.append_row(history_headers)
    
    def add_sample_data(self):
        """إضافة بيانات تجريبية إلى ورقة manager"""
        ws = self.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return
        
        # التحقق من وجود بيانات بالفعل (أكثر من سطر العنوان)
        if len(ws.get_all_values()) > 1:
            logger.info("Sample data already exists, skipping.")
            return
        
        sample = [
            ['2025-03-10 09:30', 'أحمد محمد علي', '07701234567', 'ahmed@example.com', 'علوم حاسوب', 'طلب تقسيط', '', 'MUT-20250310-001', 'قيد الانتظار', 'عالي', 'سارة خالد', 'مكتب شؤون الطلبة', 'شعبة المالية', '2025-03-10', 'مكتملة المستمسكات', '', 'الطالب مستجد', 'في انتظار مراجعة المالية', '', 'هوية, كشف علامات', '', 'سارة خالد', '2025-03-10 14:20', '2', 'sara@example.com', '[{"action":"إنشاء","employee":"نظام","timestamp":"2025-03-10T09:30:00Z"}]'],
            ['2025-03-11 11:15', 'فاطمة حسن', '07802345678', 'fatima@example.com', 'هندسة مدنية', 'طلب وثيقة تخرج', '', 'MUT-20250311-002', 'مكتملة', 'عادي', 'محمود حسين', 'شعبة الوثائق', '', '2025-03-12', 'تم إنجاز الوثيقة', 'د. خالد', 'تم تسليم الوثيقة', 'تم الإنجاز', '', 'طلب, إثبات هوية', '', 'محمود حسين', '2025-03-12 10:05', '3', 'mahmoud@example.com', '[{"action":"إنشاء","employee":"نظام","timestamp":"2025-03-11T11:15:00Z"},{"action":"إنجاز","employee":"محمود","timestamp":"2025-03-12T10:05:00Z"}]']
        ]
        for row in sample:
            ws.append_row(row)
        logger.info("✅ Sample data added.")
    
    def get_all_records(self, sheet_name):
        ws = self.get_worksheet(sheet_name)
        return ws.get_all_records() if ws else []
    
    def append_row(self, sheet_name, row_data):
        ws = self.get_worksheet(sheet_name)
        if ws:
            ws.append_row(row_data)
    
    def update_cell(self, sheet_name, row, col, value):
        ws = self.get_worksheet(sheet_name)
        if ws:
            ws.update_cell(row, col, value)
    
    def get_row_by_id(self, sheet_name, id_value):
        ws = self.get_worksheet(sheet_name)
        if not ws:
            return None
        records = ws.get_all_records()
        for idx, record in enumerate(records, start=2):
            if str(record.get('ID', '')) == str(id_value):
                return {'row_index': idx, 'data': record}
        return None
    
    def get_headers(self, sheet_name):
        ws = self.get_worksheet(sheet_name)
        return ws.row_values(1) if ws else []