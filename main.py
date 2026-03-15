#!/usr/bin/env python
import logging
import sys
import threading
import time
import os
from flask import Flask, request, jsonify, render_template_string
from sheets import GoogleSheetsClient
from config import Config
from email_service import EmailService
from qr_generator import QRGenerator
from datetime import datetime

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# تهيئة Google Sheets Client
try:
    sheets_client = GoogleSheetsClient()
    logger.info("✅ تم الاتصال بـ Google Sheets")
except Exception as e:
    logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
    sheets_client = None

app = Flask(__name__)

# ---------- مراقبة المعاملات الجديدة (إيميلات تلقائية) ----------
last_row_count = 0

def monitor_new_transactions():
    global last_row_count
    while True:
        try:
            if not sheets_client:
                time.sleep(30)
                continue
            records = sheets_client.get_all_records(Config.SHEET_MANAGER)
            current_count = len(records)
            if current_count > last_row_count:
                logger.info(f"📦 تم اكتشاف {current_count - last_row_count} معاملات جديدة")
                for i in range(last_row_count, current_count):
                    new_row = records[i]
                    transaction_id = new_row.get('ID')
                    customer_email = new_row.get('البريد الإلكتروني')
                    customer_name = new_row.get('اسم صاحب المعاملة الثلاثي')
                    if transaction_id and customer_email:
                        qr_url = QRGenerator.get_qr_url(f"{Config.WEB_APP_URL}?id={transaction_id}")
                        EmailService.send_customer_email(
                            customer_email,
                            customer_name,
                            transaction_id,
                            qr_url
                        )
                        logger.info(f"📧 تم إرسال إيميل للمعاملة {transaction_id}")
                last_row_count = current_count
        except Exception as e:
            logger.error(f"❌ خطأ في مراقبة المعاملات: {e}")
        time.sleep(30)

if sheets_client:
    try:
        last_row_count = len(sheets_client.get_all_records(Config.SHEET_MANAGER))
    except:
        last_row_count = 0
    monitor_thread = threading.Thread(target=monitor_new_transactions, daemon=True)
    monitor_thread.start()
    logger.info("🔍 بدأت مراقبة المعاملات الجديدة")

# ---------- واجهة HTML ومسارات API (كما هي) ----------
# (نفس المحتوى السابق، يمكنك الاحتفاظ به)