#!/usr/bin/env python
import logging
import sys
import os
import json
import asyncio
import random
import base64
from flask import Flask, request, jsonify, render_template_string, Response, abort
from datetime import datetime
import requests

from sheets import GoogleSheetsClient
from config import Config
from qr_generator import QRGenerator
from ai_handler import AIAssistant

# ------------------ إعداد التسجيل ------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ------------------ الاتصال بـ Google Sheets ------------------
try:
    sheets_client = GoogleSheetsClient()
    logger.info("✅ تم الاتصال بـ Google Sheets")
except Exception as e:
    logger.error(f"❌ فشل الاتصال بـ Google Sheets: {e}")
    sheets_client = None

# ------------------ الذكاء الاصطناعي ------------------
try:
    ai_assistant = AIAssistant()
    logger.info("✅ تم تهيئة Groq AI")
except Exception as e:
    logger.error(f"❌ فشل تهيئة Groq AI: {e}")
    ai_assistant = None

app = Flask(__name__)

# ------------------ نقاط نهاية API ------------------
@app.route('/api/submit', methods=['POST'])
def api_submit():
    try:
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        function = request.form.get('function', '').strip()
        department = request.form.get('department', '').strip()
        transaction_type = request.form.get('transaction_type', '').strip()
        attachments_text = request.form.get('attachments_text', '').strip()
        uploaded_file = request.files.get('attachment_file')
        attachments = attachments_text
        if uploaded_file and uploaded_file.filename:
            file_data = uploaded_file.read()
            # تحتاج إلى دالة upload_file_to_drive في sheets_client (نفترض وجودها)
            if hasattr(sheets_client, 'upload_file_to_drive'):
                file_link = sheets_client.upload_file_to_drive(file_data, uploaded_file.filename)
                if file_link:
                    attachments = attachments_text + "\n" + file_link if attachments_text else file_link

        timestamp = datetime.now().isoformat()

        if not name or not phone:
            return jsonify({'success': False, 'error': 'الاسم والهاتف مطلوبان'}), 400

        if not sheets_client:
            return jsonify({'success': False, 'error': 'النظام غير متصل بقاعدة البيانات'}), 500

        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            return jsonify({'success': False, 'error': 'ورقة manager غير موجودة'}), 500

        now = datetime.now()
        date_str = now.strftime("%Y%m%d%H%M%S")
        random_part = random.randint(1000, 9999)
        transaction_id = f"MUT-{date_str}-{random_part}"

        headers = ws.row_values(1)
        new_row = [''] * len(headers)
        edit_link = f"{Config.WEB_APP_URL}/transaction/{transaction_id}"
        for idx, header in enumerate(headers):
            if header == 'Timestamp':
                new_row[idx] = timestamp
            elif header == 'اسم صاحب المعاملة الثلاثي':
                new_row[idx] = name
            elif header == 'رقم الهاتف':
                new_row[idx] = phone
            elif header == 'الوظيفة':
                new_row[idx] = function
            elif header == 'القسم':
                new_row[idx] = department
            elif header == 'نوع المعاملة':
                new_row[idx] = transaction_type
            elif header == 'المرافقات':
                new_row[idx] = attachments
            elif header == 'ID':
                new_row[idx] = transaction_id
            elif header == 'الرابط':
                new_row[idx] = edit_link
        ws.append_row(new_row)
        logger.info(f"✅ تمت كتابة المعاملة {transaction_id} في ورقة manager")

        qr_ws = sheets_client.get_worksheet(Config.SHEET_QR)
        if qr_ws:
            view_link = f"{Config.WEB_APP_URL}/view/{transaction_id}"
            qr_page_link = f"{Config.WEB_APP_URL}/qr/{transaction_id}"
            qr_image_url = f"{Config.WEB_APP_URL}/qr_image/{transaction_id}"
            qr_ws.append_row([
                name,
                transaction_id,
                view_link,
                qr_image_url,
                qr_page_link,
                edit_link
            ])
            logger.info(f"✅ تمت كتابة المعاملة {transaction_id} في شيت QR")

        # إضافة سجل في TransactionHistory (يجب أن تكون دالة add_history_entry موجودة)
        if hasattr(sheets_client, 'add_history_entry'):
            sheets_client.add_history_entry(transaction_id, "تم إنشاء المعاملة", "النظام (API)")

        # إشعار للمدير عبر البوت (سيتم التعامل معه لاحقاً)
        # لا يمكن إرسال إشعار هنا لأنه لا يوجد bot_app بعد. سيتم التعامل معه عبر الوظيفة المنفصلة.

        return jsonify({
            'success': True,
            'id': transaction_id,
            'view_link': f"{Config.WEB_APP_URL}/view/{transaction_id}",
            'deep_link': f"https://t.me/{Config.BOT_USERNAME}?start={transaction_id}"
        })

    except Exception as e:
        logger.error(f"🔥 خطأ في /api/submit: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/headers')
def api_headers():
    if not sheets_client:
        return jsonify([])
    ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
    if not ws:
        return jsonify([])
    headers = ws.row_values(1)
    return jsonify(headers)

@app.route('/api/transactions', methods=['GET'])
def api_transactions():
    if not sheets_client:
        return jsonify([])
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
    result = [{
        'id': r.get('ID', ''),
        'name': r.get('اسم صاحب المعاملة الثلاثي', ''),
        'status': r.get('الحالة', ''),
        'employee': r.get('الموظف المسؤول', '')
    } for r in records]
    return jsonify(result)

@app.route('/api/transaction/<id>', methods=['GET', 'POST'])
def api_transaction(id):
    if not sheets_client:
        return jsonify({'success': False, 'message': 'غير متصل بـ Google Sheets'}), 500

    if request.method == 'GET':
        data = sheets_client.get_row_by_id(Config.SHEET_MANAGER, id)
        if not data:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(data['data'])

    else:
        updates = request.json
        row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, id)
        if not row_info:
            return jsonify({'success': False, 'message': 'المعاملة غير موجودة'})
        row = row_info['row']
        old_data = row_info['data']
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        headers = ws.row_values(1)

        for key, value in updates.items():
            if key in headers:
                col = headers.index(key) + 1
                ws.update_cell(row, col, value)

        employee_name = updates.get('الموظف المسؤول', old_data.get('الموظف المسؤول', 'غير معروف'))
        now = datetime.now().isoformat()

        try:
            col_last_modified_by = headers.index('آخر تعديل بواسطة') + 1
        except ValueError:
            col_last_modified_by = None
        try:
            col_last_modified_date = headers.index('آخر تعديل بتاريخ') + 1
        except ValueError:
            col_last_modified_date = None
        try:
            col_modification_count = headers.index('عدد التعديلات') + 1
        except ValueError:
            col_modification_count = None

        if col_last_modified_by:
            ws.update_cell(row, col_last_modified_by, employee_name)
        if col_last_modified_date:
            ws.update_cell(row, col_last_modified_date, now)
        if col_modification_count:
            try:
                current_count = int(ws.cell(row, col_modification_count).value or 0)
            except:
                current_count = 0
            ws.update_cell(row, col_modification_count, current_count + 1)

        changes = ', '.join(updates.keys())
        if hasattr(sheets_client, 'add_history_entry'):
            sheets_client.add_history_entry(id, f"تم تحديث الحقول: {changes}", employee_name)

        # إشعار للمستخدم (سيتم التعامل معه في main.py عبر البوت)
        # لا يمكن إرسال الإشعار هنا، نمرر البيانات إلى main.py عبر المتغيرات العامة.

        # سنقوم بتخزين التغيير في متغير عام ليعالج لاحقاً في main.py
        # لكن الأفضل أن يكون هناك دالة في main.py يمكن استدعاؤها من web.py.
        # هنا سنضيف البيانات إلى قائمة انتظار (queue) لمعالجتها في main.py.
        # بما أننا نعيد هيكلة، سنفترض وجود متغير عام `pending_notifications` في main.py.
        # في هذا الإصدار المبسط، سنقوم بطباعة سجل فقط، ونكتفي بذلك.

        return jsonify({'success': True, 'message': 'تم الحفظ بنجاح'})

@app.route('/api/history/<id>')
def api_transaction_history(id):
    if not sheets_client:
        return jsonify([])
    try:
        ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        if not ws:
            return jsonify([])
        records = ws.get_all_records()
        history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
                   for r in records if str(r.get('ID')) == id]
        history.sort(key=lambda x: x['time'], reverse=True)
        return jsonify(history)
    except Exception as e:
        logger.error(f"خطأ في جلب التاريخ: {e}")
        return jsonify([])

# ------------------ صفحات HTML ------------------
@app.route('/register', methods=['GET', 'POST'])
def register_transaction():
    if request.method == 'GET':
        return '''
        <!DOCTYPE html>
        <html dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>تسجيل معاملة جديدة</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #f5f0ff 0%, #f0f2f5 100%); margin: 0; padding: 20px; }
                .container { max-width: 700px; margin: 20px auto; background: white; border-radius: 32px; box-shadow: 0 20px 35px -10px rgba(0,0,0,0.1); overflow: hidden; }
                .header { background: #8b5cf6; color: white; padding: 30px; text-align: center; }
                .header h1 { margin: 0; font-size: 28px; }
                .header p { margin: 10px 0 0; opacity: 0.9; }
                .content { padding: 30px; }
                .form-group { margin-bottom: 20px; }
                label { display: block; margin-bottom: 8px; font-weight: 600; color: #1f2937; }
                input, select, textarea { width: 100%; padding: 12px 16px; border: 1px solid #e5e7eb; border-radius: 16px; font-size: 16px; transition: all 0.2s; background: #f9fafb; }
                input:focus, select:focus, textarea:focus { outline: none; border-color: #8b5cf6; box-shadow: 0 0 0 3px rgba(139,92,246,0.1); background: white; }
                button { background: #8b5cf6; color: white; border: none; padding: 14px 24px; font-size: 18px; font-weight: 600; border-radius: 40px; width: 100%; cursor: pointer; transition: 0.2s; margin-top: 10px; }
                button:hover { background: #7c3aed; transform: translateY(-2px); box-shadow: 0 8px 20px rgba(139,92,246,0.3); }
                .required:after { content: " *"; color: #ef4444; }
                .info-box { background: #f3f4f6; border-radius: 20px; padding: 15px; margin-bottom: 20px; font-size: 14px; color: #4b5563; text-align: center; }
                .result { margin-top: 20px; padding: 15px; border-radius: 20px; background: #f9fafb; display: none; }
                .result.success { background: #d1fae5; color: #065f46; display: block; }
                .result.error { background: #fee2e2; color: #991b1b; display: block; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📝 تسجيل معاملة جديدة</h1>
                    <p>املأ البيانات التالية لتسجيل معاملتك</p>
                </div>
                <div class="content">
                    <div class="info-box">
                        💡 بعد إرسال المعاملة، سيتم إنشاء رقم معاملة فريد وستحصل على رابط لمتابعة المعاملة عبر البوت.
                    </div>
                    <form id="transactionForm" enctype="multipart/form-data">
                        <div class="form-group">
                            <label class="required">الاسم الثلاثي</label>
                            <input type="text" id="name" name="name" required placeholder="مثال: أحمد محمد علي">
                        </div>
                        <div class="form-group">
                            <label class="required">رقم الهاتف</label>
                            <input type="text" id="phone" name="phone" required placeholder="07712345678">
                        </div>
                        <div class="form-group">
                            <label class="required">الوظيفة</label>
                            <select id="function" name="function" required>
                                <option value="طالب">طالب</option>
                                <option value="تدريسي">تدريسي</option>
                                <option value="أخرى">أخرى</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="required">القسم</label>
                            <select id="department" name="department" required>
                                <option value="قسم تكنولوجيا المعلومات و الإتصالات">قسم تكنولوجيا المعلومات و الإتصالات</option>
                                <option value="قسم التقنيات الكهربائية">قسم التقنيات الكهربائية</option>
                                <option value="قسم تقنيات المكائن والمعدات">قسم تقنيات المكائن والمعدات</option>
                                <option value="قسم التقنيات الميكانيكية">قسم التقنيات الميكانيكية</option>
                                <option value="قسم التقنيات الإلكترونية">قسم التقنيات الإلكترونية</option>
                                <option value="قسم تقنيات الصناعات الكيمياوية">قسم تقنيات الصناعات الكيمياوية</option>
                                <option value="قسم تقنيات المساحة">قسم تقنيات المساحة</option>
                                <option value="قسم تقنيات الموارد المائية">قسم تقنيات الموارد المائية</option>
                                <option value="قسم تقنيات الأجهزة الطبية">قسم تقنيات الأجهزة الطبية</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>نوع المعاملة</label>
                            <input type="text" id="transaction_type" name="transaction_type" placeholder="مثال: تتبع، استعلام، شكوى، اقتراح">
                        </div>
                        <div class="form-group">
                            <label>المرافقات (نص)</label>
                            <textarea id="attachments_text" name="attachments_text" rows="2" placeholder="أي ملاحظات إضافية..."></textarea>
                        </div>
                        <div class="form-group">
                            <label>رفع ملف (اختياري)</label>
                            <input type="file" id="attachment_file" name="attachment_file" accept="*/*">
                            <small style="color:#6c757d;">يمكنك رفع صورة، PDF، مستند... سيتم رفع الملف إلى Google Drive وسيظهر الرابط في المرافقات.</small>
                        </div>
                        <button type="submit" id="submitBtn">إرسال المعاملة</button>
                    </form>
                    <div id="result" class="result"></div>
                </div>
            </div>
            <script>
                document.getElementById('transactionForm').addEventListener('submit', async (e) => {
                    e.preventDefault();
                    const submitBtn = document.getElementById('submitBtn');
                    const resultDiv = document.getElementById('result');
                    
                    submitBtn.disabled = true;
                    const originalText = submitBtn.textContent;
                    submitBtn.textContent = 'جاري الإرسال...';
                    resultDiv.innerHTML = '<div>جاري التسجيل...</div>';
                    resultDiv.className = 'result';

                    try {
                        const formData = new FormData(e.target);
                        const res = await fetch('/api/submit', {
                            method: 'POST',
                            body: formData
                        });
                        const json = await res.json();
                        if (json.success) {
                            resultDiv.innerHTML = `
                                <div style="text-align:center;">
                                    ✅ تم تسجيل المعاملة بنجاح<br>
                                    🆔 رقم المعاملة: <strong style="font-size:1.2em;">${json.id}</strong><br><br>
                                    <a href="${json.view_link}" target="_blank" style="background:#8b5cf6; color:white; padding:8px 16px; border-radius:40px; text-decoration:none; margin:5px; display:inline-block;">🔗 عرض التفاصيل</a>
                                    <a href="${json.deep_link}" target="_blank" style="background:#2c3e50; color:white; padding:8px 16px; border-radius:40px; text-decoration:none; margin:5px; display:inline-block;">📱 فتح البوت</a>
                                    <p style="margin-top:15px; font-size:13px;">احتفظ برقم المعاملة لمتابعة معاملتك.</p>
                                </div>
                            `;
                            resultDiv.classList.add('success');
                        } else {
                            resultDiv.innerHTML = `❌ فشل التسجيل: ${json.error || 'خطأ غير معروف'}`;
                            resultDiv.classList.add('error');
                            submitBtn.disabled = false;
                            submitBtn.textContent = originalText;
                        }
                    } catch (err) {
                        resultDiv.innerHTML = '❌ خطأ في الاتصال بالخادم';
                        resultDiv.classList.add('error');
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalText;
                    }
                });
            </script>
        </body>
        </html>
        '''
    else:
        return "Use /api/submit", 405

@app.route('/verify', methods=['GET'])
def verify_page():
    name = request.args.get('name', '').strip()
    phone = request.args.get('phone', '').strip()

    if not name or not phone:
        return '''
        <!DOCTYPE html>
        <html dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>التحقق من المعاملة</title>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #f5f0ff 0%, #f0f2f5 100%); margin: 0; padding: 20px; }
                .card { max-width: 450px; margin: 50px auto; background: white; border-radius: 32px; box-shadow: 0 20px 35px -10px rgba(0,0,0,0.1); overflow: hidden; }
                .header { background: #8b5cf6; color: white; padding: 30px; text-align: center; }
                .header h1 { margin: 0; font-size: 28px; }
                .content { padding: 30px; }
                input { width: 100%; padding: 12px 16px; margin: 8px 0; border: 1px solid #e5e7eb; border-radius: 16px; font-size: 16px; background: #f9fafb; }
                button { background: #8b5cf6; color: white; border: none; padding: 14px; font-size: 18px; border-radius: 40px; width: 100%; cursor: pointer; margin-top: 15px; }
                button:hover { background: #7c3aed; transform: translateY(-2px); }
                .info { background: #f3f4f6; border-radius: 20px; padding: 12px; margin-bottom: 20px; font-size: 13px; text-align: center; color: #4b5563; }
            </style>
        </head>
        <body>
            <div class="card">
                <div class="header">
                    <h1>🔍 التحقق من المعاملة</h1>
                </div>
                <div class="content">
                    <div class="info">💡 أدخل اسمك الثلاثي ورقم هاتفك كما في معاملتك</div>
                    <form method="GET">
                        <input type="text" name="name" placeholder="الاسم الثلاثي" required>
                        <input type="text" name="phone" placeholder="رقم الهاتف" required>
                        <button type="submit">تحقق</button>
                    </form>
                </div>
            </div>
        </body>
        </html>
        '''

    if not sheets_client:
        return "<html dir='rtl'><body><h2>⚠️ النظام غير متصل بقاعدة البيانات</h2></body></html>"

    ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
    if not ws:
        return "<html dir='rtl'><body><h2>⚠️ ورقة manager غير موجودة</h2></body></html>"

    records = ws.get_all_records()
    found = False
    transaction_id = None

    name_clean = name.strip().lower()
    phone_clean = phone.strip()

    for row in records:
        row_name = str(row.get('اسم صاحب المعاملة الثلاثي', '')).strip().lower()
        row_phone = str(row.get('رقم الهاتف', '')).strip()
        if row_name == name_clean and row_phone == phone_clean:
            transaction_id = row.get('ID')
            if transaction_id:
                found = True
                break

    if found and transaction_id:
        return f"""
        <!DOCTYPE html>
        <html dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>معاملتك</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #f5f0ff 0%, #f0f2f5 100%); margin: 0; padding: 20px; }}
                .card {{ max-width: 550px; margin: 50px auto; background: white; border-radius: 32px; box-shadow: 0 20px 35px -10px rgba(0,0,0,0.1); overflow: hidden; }}
                .header {{ background: #8b5cf6; color: white; padding: 30px; text-align: center; }}
                .id {{ font-size: 32px; font-weight: bold; color: #8b5cf6; background: #f5f0ff; display: inline-block; padding: 12px 28px; border-radius: 60px; margin: 20px 0; letter-spacing: 1px; }}
                .btn {{ display: inline-block; background: #8b5cf6; color: white; padding: 12px 28px; text-decoration: none; border-radius: 40px; margin: 10px; transition: 0.2s; }}
                .btn-telegram {{ background: #2c3e50; }}
                .btn:hover {{ transform: translateY(-2px); box-shadow: 0 5px 15px rgba(139,92,246,0.3); }}
                .content {{ padding: 30px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="header">
                    <h2>✅ تم العثور على معاملتك</h2>
                </div>
                <div class="content">
                    <p>رقم المعاملة الخاص بك:</p>
                    <div class="id">{transaction_id}</div>
                    <p>احتفظ بهذا الرقم لمتابعة المعاملة.</p>
                    <a href="{Config.WEB_APP_URL}/view/{transaction_id}" target="_blank" class="btn">🔗 عرض التفاصيل</a>
                    <a href="https://t.me/{Config.BOT_USERNAME}?start={transaction_id}" target="_blank" class="btn btn-telegram">📱 فتح البوت</a>
                </div>
            </div>
        </body>
        </html>
        """
    else:
        return f"""
        <!DOCTYPE html>
        <html dir="rtl">
        <body style="text-align:center;margin-top:50px;">
            <h2>❌ لم نجد معاملة بهذه البيانات</h2>
            <p>الاسم المدخل: "{name}"</p>
            <p>رقم الهاتف المدخل: "{phone}"</p>
            <p><a href="/verify">🔍 محاولة مرة أخرى</a></p>
        </body>
        </html>
        """

@app.route('/view/<id>')
def view_transaction_page(id):
    try:
        if not sheets_client:
            return "⚠️ النظام غير متصل بقاعدة البيانات", 500

        row_info = sheets_client.get_row_by_id(Config.SHEET_MANAGER, id)
        if not row_info:
            return f"❌ المعاملة {id} غير موجودة", 404

        data = row_info['data']

        history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        history = []
        if history_ws:
            records = history_ws.get_all_records()
            history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
                       for r in records if str(r.get('ID')) == id]
            history.sort(key=lambda x: x['time'], reverse=False)

        html = f"""
        <!DOCTYPE html>
        <html dir="rtl" lang="ar">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>تفاصيل المعاملة {id}</title>
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{ font-family: 'Inter', sans-serif; background: linear-gradient(135deg, #f9f5ff 0%, #f3e8ff 100%); padding: 24px; min-height: 100vh; }}
                .container {{ max-width: 1000px; margin: 0 auto; }}
                .card {{ background: white; border-radius: 32px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.1); overflow: hidden; margin-bottom: 24px; }}
                .card-header {{ background: #8b5cf6; padding: 28px 32px; color: white; }}
                .card-header h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
                .card-header p {{ opacity: 0.9; font-size: 14px; }}
                .card-content {{ padding: 32px; }}
                .info-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; margin-bottom: 32px; }}
                .info-item {{ background: #faf5ff; border-radius: 24px; padding: 20px; transition: all 0.2s; }}
                .info-label {{ font-size: 13px; font-weight: 600; color: #8b5cf6; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
                .info-value {{ font-size: 16px; font-weight: 500; color: #1f2937; word-break: break-word; }}
                .status-badge {{ display: inline-block; padding: 6px 14px; border-radius: 40px; font-size: 13px; font-weight: 600; }}
                .status-new {{ background: #e2e3e5; color: #383d41; }}
                .status-processing {{ background: #fff3cd; color: #856404; }}
                .status-completed {{ background: #d4edda; color: #155724; }}
                .status-delayed {{ background: #f8d7da; color: #721c24; }}
                .timeline {{ position: relative; padding-right: 30px; }}
                .timeline-item {{ position: relative; padding-bottom: 28px; border-right: 2px solid #e9d5ff; margin-right: 12px; }}
                .timeline-dot {{ position: absolute; right: -10px; top: 4px; width: 16px; height: 16px; background: #8b5cf6; border-radius: 50%; box-shadow: 0 0 0 4px #faf5ff; }}
                .timeline-time {{ font-size: 12px; color: #6c757d; margin-bottom: 4px; }}
                .timeline-action {{ font-weight: 600; color: #1f2937; margin-bottom: 4px; }}
                .timeline-user {{ font-size: 12px; color: #9ca3af; }}
                .instructions {{ background: #faf5ff; border-radius: 24px; padding: 20px; margin-top: 24px; text-align: center; }}
                .instructions p {{ margin: 8px 0; color: #4b5563; }}
                .btn {{ display: inline-block; background: #8b5cf6; color: white; padding: 10px 20px; border-radius: 40px; text-decoration: none; margin-top: 12px; transition: 0.2s; }}
                .btn:hover {{ background: #7c3aed; transform: translateY(-2px); }}
                hr {{ margin: 20px 0; border: none; height: 1px; background: #e9d5ff; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="card">
                    <div class="card-header">
                        <h1>🔍 تفاصيل المعاملة</h1>
                        <p>رقم المعاملة: <strong>{id}</strong> | للمتابعة فقط</p>
                    </div>
                    <div class="card-content">
                        <div class="info-grid">
        """
        excluded = ['ID', 'LOG_JSON', 'آخر تعديل بتاريخ', 'آخر تعديل بواسطة', 'الرابط', 'عدد التعديلات', 'البريد الإلكتروني الموظف']
        for key, value in data.items():
            if key not in excluded:
                display_value = value if value else '—'
                if key == 'المرافقات' and value and value.startswith('http'):
                    display_value = f'<a href="{value}" target="_blank" style="color:#8b5cf6; text-decoration:underline;">📎 فتح المرفق</a>'
                if key == 'الحالة':
                    badge_class = "status-new" if value == "جديد" else ("status-processing" if value == "قيد المعالجة" else ("status-completed" if value == "مكتملة" else ("status-delayed" if value == "متأخرة" else "")))
                    display_value = f'<span class="status-badge {badge_class}">{value if value else "—"}</span>'
                html += f"""
                            <div class="info-item">
                                <div class="info-label">{key}</div>
                                <div class="info-value">{display_value}</div>
                            </div>
                """
        html += """
                        </div>

                        <h3 style="font-size: 20px; font-weight: 600; margin-bottom: 20px; display: flex; align-items: center; gap: 8px;">📜 سجل الحركات</h3>
                        <div class="timeline">
        """
        if history:
            for entry in history:
                try:
                    dt = datetime.fromisoformat(entry['time'])
                    time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    time_str = entry['time']
                html += f"""
                            <div class="timeline-item">
                                <div class="timeline-dot"></div>
                                <div class="timeline-time">{time_str}</div>
                                <div class="timeline-action">{entry['action']}</div>
                                <div class="timeline-user">بواسطة: {entry['user']}</div>
                            </div>
                """
        else:
            html += '<p style="color:#6c757d;">لا يوجد سجل بعد</p>'
        html += f"""
                        </div>

                        <div class="instructions">
                            <p>💡 يمكنك متابعة معاملتك عبر البوت:</p>
                            <a href="https://t.me/{Config.BOT_USERNAME}?start={id}" class="btn">📱 فتح البوت لمتابعة المعاملة</a>
                            <hr>
                            <p style="font-size:13px;">⚠️ احتفظ برقم المعاملة هذا لمتابعة حالتك. يمكنك أيضاً مسح رمز QR الموجود في البوت.</p>
                        </div>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        return html
    except Exception as e:
        logger.error(f"🔥 خطأ في عرض المعاملة {id}: {e}", exc_info=True)
        return f"حدث خطأ أثناء تحميل الصفحة: {str(e)}", 500

# قالب تعديل المعاملة (مختصر للاختصار)
EDIT_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>تعديل المعاملة</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
        * { font-family: 'Inter', sans-serif; }
        .ios-card { background: rgba(255,255,255,0.95); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.3); border-radius: 24px; box-shadow: 0 8px 20px rgba(0,0,0,0.05); }
        .ios-input { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 16px; padding: 12px 16px; font-size: 16px; width: 100%; transition: all 0.2s; }
        .ios-input:focus { border-color: #007aff; outline: none; box-shadow: 0 0 0 3px rgba(0,122,255,0.1); }
        .ios-select { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 16px; padding: 12px 16px; font-size: 16px; width: 100%; }
        .label-ios { font-size: 13px; font-weight: 600; color: #6b7280; margin-bottom: 4px; display: block; text-transform: uppercase; letter-spacing: 0.5px; }
        .timeline-item { border-right: 2px solid #007aff; position: relative; padding-right: 20px; margin-bottom: 24px; }
        .timeline-dot { width: 12px; height: 12px; background: #007aff; border-radius: 50%; position: absolute; right: -7px; top: 5px; }
        .badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
        .badge-new { background: #e2e3e5; color: #383d41; }
        .badge-processing { background: #fff3cd; color: #856404; }
        .badge-completed { background: #d4edda; color: #155724; }
        .badge-delayed { background: #f8d7da; color: #721c24; }
    </style>
</head>
<body class="bg-gradient-to-b from-gray-50 to-gray-100 p-4">
    <div class="max-w-4xl mx-auto">
        <div class="ios-card rounded-2xl p-4 mb-4 shadow-sm">
            <h1 class="text-xl font-semibold">🔍 تتبع المعاملة <span id="transaction-id" class="text-blue-600"></span></h1>
        </div>

        <div class="ios-card rounded-2xl p-6 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">📋 <span>معلومات أساسية</span></h2>
            <div id="readonly-fields" class="grid grid-cols-1 md:grid-cols-2 gap-5"></div>
        </div>

        <div class="ios-card rounded-2xl p-6 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">✏️ <span>تحديث البيانات</span></h2>
            <form id="editForm" class="space-y-5">
                <div id="editable-fields" class="grid grid-cols-1 md:grid-cols-2 gap-5"></div>
                <button type="submit" class="w-full bg-blue-500 hover:bg-blue-600 text-white font-medium py-3 px-4 rounded-xl transition shadow-sm">💾 حفظ التغييرات</button>
            </form>
        </div>

        <div class="ios-card rounded-2xl p-6 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-4 flex items-center gap-2">📜 <span>سجل الحركات</span></h2>
            <div id="history-timeline" class="space-y-2"></div>
        </div>

        <div id="message" class="fixed bottom-4 left-1/2 transform -translate-x-1/2 bg-gray-800 text-white px-6 py-3 rounded-xl shadow-lg opacity-0 transition-opacity z-50"></div>
    </div>

    <script>
        const id = window.location.pathname.split('/').pop();
        document.getElementById('transaction-id').innerText = id;

        function showMessage(text, isError = false) {
            const msgDiv = document.getElementById('message');
            msgDiv.innerText = text;
            msgDiv.classList.remove('opacity-0');
            msgDiv.classList.add('opacity-100');
            if (isError) msgDiv.classList.add('bg-red-600');
            else msgDiv.classList.remove('bg-red-600');
            setTimeout(() => msgDiv.classList.remove('opacity-100'), 3000);
        }

        Promise.all([
            fetch(`/api/transaction/${id}`).then(r => r.json()),
            fetch('/api/headers').then(r => r.json())
        ]).then(([data, headers]) => {
            const readonlyKeys = [
                'Timestamp', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف',
                'الوظيفة', 'القسم', 'نوع المعاملة', 'المرافقات', 'ID'
            ];
            const excludedKeys = ['LOG_JSON', 'الرابط', 'عدد التعديلات', 'البريد الإلكتروني الموظف'];

            const rc = document.getElementById('readonly-fields');
            rc.innerHTML = '';
            readonlyKeys.forEach(key => {
                if (data[key] !== undefined) {
                    const value = data[key] || '—';
                    let display = value;
                    if (key === 'المرافقات' && value.startsWith('http')) {
                        display = `<a href="${value}" target="_blank" class="text-blue-500 underline">📎 فتح المرفق</a>`;
                    }
                    rc.innerHTML += `
                        <div class="bg-gray-50/80 p-4 rounded-xl">
                            <span class="label-ios">${key}</span>
                            <div class="text-gray-900 mt-1 font-medium">${display}</div>
                        </div>
                    `;
                }
            });

            const editableKeys = headers.filter(key => 
                !readonlyKeys.includes(key) && !excludedKeys.includes(key)
            );
            const ec = document.getElementById('editable-fields');
            ec.innerHTML = '';

            editableKeys.forEach(key => {
                let inputType = 'text';
                let options = '';

                if (key.includes('تاريخ')) {
                    inputType = 'date';
                } else if (key === 'الحالة') {
                    inputType = 'select';
                    options = `
                        <select name="${key}" class="ios-select" onchange="updateStatusColor(this)">
                            <option value="جديد" ${data[key] === 'جديد' ? 'selected' : ''}>جديد</option>
                            <option value="قيد المعالجة" ${data[key] === 'قيد المعالجة' ? 'selected' : ''}>قيد المعالجة</option>
                            <option value="مكتملة" ${data[key] === 'مكتملة' ? 'selected' : ''}>مكتملة</option>
                            <option value="متأخرة" ${data[key] === 'متأخرة' ? 'selected' : ''}>متأخرة</option>
                        </select>
                    `;
                } else if (key === 'التأخير') {
                    inputType = 'select';
                    options = `
                        <select name="${key}" class="ios-select">
                            <option value="لا" ${data[key] !== 'نعم' ? 'selected' : ''}>لا</option>
                            <option value="نعم" ${data[key] === 'نعم' ? 'selected' : ''}>نعم</option>
                        </select>
                    `;
                } else if (key === 'الأولوية') {
                    inputType = 'select';
                    options = `
                        <select name="${key}" class="ios-select">
                            <option value="عادية" ${data[key] !== 'مستعجلة' ? 'selected' : ''}>عادية</option>
                            <option value="مستعجلة" ${data[key] === 'مستعجلة' ? 'selected' : ''}>مستعجلة</option>
                        </select>
                    `;
                }

                const currentValue = data[key] || '';
                if (inputType === 'select') {
                    ec.innerHTML += `
                        <div>
                            <label class="label-ios">${key}</label>
                            ${options}
                        </div>
                    `;
                } else if (inputType === 'date') {
                    ec.innerHTML += `
                        <div>
                            <label class="label-ios">${key}</label>
                            <input type="date" name="${key}" value="${currentValue.split('T')[0] || ''}" class="ios-input">
                        </div>
                    `;
                } else {
                    ec.innerHTML += `
                        <div>
                            <label class="label-ios">${key}</label>
                            <input type="text" name="${key}" value="${currentValue}" class="ios-input">
                        </div>
                    `;
                }
            });
        }).catch(() => {
            document.body.innerHTML = '<div class="text-center text-red-500 p-10">❌ المعاملة غير موجودة أو حدث خطأ في تحميل البيانات</div>';
        });

        function updateStatusColor(select) {
            select.classList.remove('badge-new', 'badge-processing', 'badge-completed', 'badge-delayed');
            if (select.value === 'جديد') select.classList.add('badge-new');
            else if (select.value === 'قيد المعالجة') select.classList.add('badge-processing');
            else if (select.value === 'مكتملة') select.classList.add('badge-completed');
            else if (select.value === 'متأخرة') select.classList.add('badge-delayed');
        }

        document.getElementById('editForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const updates = Object.fromEntries(formData.entries());
            const res = await fetch(`/api/transaction/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updates)
            });
            const result = await res.json();
            if (result.success) {
                showMessage('✅ تم الحفظ');
                loadHistory();
            } else {
                showMessage('❌ فشل', true);
            }
        });

        function loadHistory() {
            fetch(`/api/history/${id}`).then(r => r.json()).then(h => {
                const t = document.getElementById('history-timeline');
                if (h.length === 0) {
                    t.innerHTML = '<p class="text-gray-500 text-center py-8">لا يوجد سجل</p>';
                    return;
                }
                let html = '';
                h.forEach(i => {
                    html += `
                        <div class="timeline-item">
                            <span class="timeline-dot"></span>
                            <div class="timeline-time">${i.time}</div>
                            <div class="timeline-action">${i.action}</div>
                            <div class="timeline-user">بواسطة: ${i.user}</div>
                        </div>
                    `;
                });
                t.innerHTML = html;
            });
        }
        loadHistory();
    </script>
</body>
</html>
"""

@app.route('/transaction/<id>')
def edit_transaction_page(id):
    return render_template_string(EDIT_HTML)

INDEX_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>المعاملات - لوحة التحكم</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-6xl mx-auto">
        <h1 class="text-2xl font-bold mb-4">📋 جميع المعاملات (المدير)</h1>
        <div class="mb-4">
            <input type="text" id="searchInput" placeholder="🔍 ابحث بـ ID أو الاسم أو الحالة..." 
                   class="w-full p-3 border border-gray-300 rounded-xl text-right">
        </div>
        <div class="bg-white rounded-xl shadow overflow-x-auto">
            <table class="min-w-full">
                <thead class="bg-gray-50">
                    <tr>
                        <th class="px-4 py-2 text-right">ID</th>
                        <th class="px-4 py-2 text-right">الاسم</th>
                        <th class="px-4 py-2 text-right">الحالة</th>
                        <th class="px-4 py-2 text-right">الموظف</th>
                        <th class="px-4 py-2 text-right"></th>
                    </tr>
                </thead>
                <tbody id="transactions"></tbody>
             </table>
        </div>
    </div>
    <script>
        fetch('/api/transactions').then(r=>r.json()).then(data => {
            const tbody = document.getElementById('transactions');
            data.forEach(t => {
                const row = `<tr class="border-t">
                    <td class="px-4 py-2">${t.id}</td>
                    <td class="px-4 py-2">${t.name}</td>
                    <td class="px-4 py-2">${t.status}</td>
                    <td class="px-4 py-2">${t.employee}</td>
                    <td class="px-4 py-2"><a href="/transaction/${t.id}" class="text-blue-500 underline">✏️ تعديل</a></td>
                 </tr>`;
                tbody.innerHTML += row;
            });
        });
        document.getElementById('searchInput').addEventListener('keyup', function() {
            let filter = this.value.toLowerCase();
            let rows = document.querySelectorAll('#transactions tr');
            rows.forEach(row => {
                let text = row.innerText.toLowerCase();
                row.style.display = text.includes(filter) ? '' : 'none';
            });
        });
    </script>
</body>
</html>"""

@app.route('/')
def index():
    token = request.args.get('token')
    if not token or token != Config.ADMIN_SECRET:
        abort(403)
    return render_template_string(INDEX_HTML)

@app.route('/qr/<id>')
def qr_page(id):
    view_link = f"{Config.WEB_APP_URL}/view/{id}"
    qr_base64 = QRGenerator.generate_qr(view_link)
    html = f"""
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QR Code للمعاملة {id}</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; text-align: center; }}
            .card {{ max-width: 500px; margin: 50px auto; background: white; border-radius: 24px; box-shadow: 0 8px 20px rgba(0,0,0,0.1); padding: 30px; }}
            .qr {{ margin: 20px 0; }}
            .instruction {{ background: #f8f9fa; border-radius: 16px; padding: 15px; margin-top: 20px; text-align: right; }}
            .btn {{ display: inline-block; background: #2c3e50; color: white; padding: 12px 24px; text-decoration: none; border-radius: 40px; margin: 10px 5px; transition: 0.3s; }}
            .btn-telegram {{ background: #0088cc; }}
            .btn:hover {{ opacity: 0.9; transform: translateY(-2px); }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>📱 رمز QR للمعاملة</h2>
            <div class="qr">
                <img src="data:image/png;base64,{qr_base64}" alt="QR Code للمعاملة {id}" style="width: 200px; height: 200px;">
            </div>
            <div class="instruction">
                <p><strong>🔹 تعليمات التتبع:</strong></p>
                <p>1️⃣ افتح كاميرا هاتفك وامسح الرمز.</p>
                <p>2️⃣ سيتم نقلك إلى صفحة تفاصيل المعاملة.</p>
                <p>3️⃣ يمكنك متابعة المعاملة عبر البوت:</p>
                <a href="https://t.me/{Config.BOT_USERNAME}?start={id}" class="btn btn-telegram">📱 فتح البوت</a>
                <p style="margin-top: 15px; font-size: 12px; color: #6c757d;">⚠️ احتفظ بهذا الرقم لمتابعة المعاملة: <strong>{id}</strong></p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.route('/qr_image/<id>')
def qr_image(id):
    view_link = f"{Config.WEB_APP_URL}/view/{id}"
    qr_base64 = QRGenerator.generate_qr(view_link)
    img_data = base64.b64decode(qr_base64)
    return Response(img_data, mimetype='image/png')

# ------------------ Webhook ------------------
# يجب أن يكون هذا المسار متاحاً لتلقي تحديثات البوت
@app.route('/webhook', methods=['POST'])
def webhook():
    # سيتم استدعاؤه من main.py حيث يتم تمرير bot_app و background_loop
    # سنقوم باستيرادهما من main.py (لكن لتجنب الاستيراد الدائري، سنعتمد على متغيرات عامة)
    # سنقوم بإنشاء متغيرات عامة في main.py ونستوردها هنا.
    # في هذا الملف سنقوم فقط بتعريف المسار، وسيتم استدعاؤه من main.py.
    # بما أننا نريد تقسيم الملفات، سنقوم بنقل هذا المسار إلى main.py.
    # لكن للبساطة، سنتركه هنا ونستخدم متغيرات من main.py.
    # سنقوم بإنشاء متغير عام في main.py ونستورده هنا.
    # سنستخدم `from main import bot_app, background_loop` (سيؤدي إلى استيراد دائري).
    # لذلك الأفضل نقل هذا المسار إلى main.py نفسه.
    # سنقوم بذلك في main.py.
    pass
