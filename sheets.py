import gspread
from google.oauth2.service_account import Credentials
import logging
import json
import os
import base64
from config import Config

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self.scope = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        self.connect()

    def connect(self):
        try:
            creds = None
            logger.info("🔍 بدء محاولة الاتصال بـ Google Sheets")

            # 1. محاولة قراءة JSON مباشرة من المتغير (الأفضل)
            creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
            if creds_json:
                try:
                    info = json.loads(creds_json)
                    creds = Credentials.from_service_account_info(info, scopes=self.scope)
                    logger.info("✅ تم تحميل بيانات الاعتماد من JSON مباشرة")
                except Exception as e:
                    logger.error(f"❌ فشل تحليل JSON: {e}")

            # 2. إذا لم ينجح، جرب Base64
            if not creds:
                creds_b64 = os.getenv('GOOGLE_CREDENTIALS_BASE64')
                if creds_b64:
                    try:
                        json_bytes = base64.b64decode(creds_b64)
                        info = json.loads(json_bytes)
                        creds = Credentials.from_service_account_info(info, scopes=self.scope)
                        logger.info("✅ تم تحميل بيانات الاعتماد من base64")
                    except Exception as e:
                        logger.error(f"❌ فشل فك base64: {e}")

            # 3. كحل أخير، جرب الملف
            if not creds:
                file_path = '/volumes/credentials.json'
                if os.path.exists(file_path):
                    try:
                        creds = Credentials.from_service_account_file(file_path, scopes=self.scope)
                        logger.info("✅ تم تحميل بيانات الاعتماد من الملف")
                    except Exception as e:
                        logger.error(f"❌ فشل قراءة الملف: {e}")
                else:
                    logger.warning("⚠️ ملف الاعتماد غير موجود في المسار المتوقع")

            if not creds:
                raise ValueError("❌ لا يوجد مصدر موثوق لبيانات الاعتماد!")

            self.client = gspread.authorize(creds)
            self.spreadsheet = self.client.open_by_key(Config.SPREADSHEET_ID)
            logger.info("✅ متصل بـ Google Sheets")

        except Exception as e:
            logger.error(f"❌ فشل الاتصال: {e}")
            raise

    # باقي الدوال (get_worksheet, ensure_sheets_exist, get_all_records, ...) كما هي سابقاً
    # (لن أكررها هنا للاختصار، لكنها موجودة في النسخة السابقة)