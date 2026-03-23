from flask import Flask, request, jsonify, render_template_string, Response
import logging
import sys
import json
import asyncio
import base64
import os
from telegram import Update
from sheets import GoogleSheetsClient
from config import Config
from qr_generator import QRGenerator
from email_service import EmailService
# استيراد المتغيرات العالمية من البوت
from bot import bot_app, background_loop, sheets_client, ai_assistant

logger = logging.getLogger(__name__)
app = Flask(__name__)

# ------------------ نقاط نهاية API ------------------
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
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        headers = ws.row_values(1)

        for key, value in updates.items():
            if key in headers:
                col = headers.index(key) + 1
                ws.update_cell(row, col, value)

        # تحديث الأعمدة الإضافية
        from datetime import datetime
        employee_name = updates.get('الموظف المسؤول', 'غير معروف')
        if 'آخر تعديل بواسطة' in headers:
            col_v = headers.index('آخر تعديل بواسطة') + 1
            ws.update_cell(row, col_v, employee_name)
        else:
            ws.update_cell(row, 22, employee_name)

        now = datetime.now().isoformat()
        if 'آخر تعديل بتاريخ' in headers:
            col_w = headers.index('آخر تعديل بتاريخ') + 1
            ws.update_cell(row, col_w, now)
        else:
            ws.update_cell(row, 23, now)

        try:
            current_count_cell = ws.cell(row, 24).value
            current_count = int(current_count_cell) if current_count_cell and str(current_count_cell).isdigit() else 0
        except:
            current_count = 0
        new_count = current_count + 1
        if 'عدد التعديلات' in headers:
            col_x = headers.index('عدد التعديلات') + 1
            ws.update_cell(row, col_x, new_count)
        else:
            ws.update_cell(row, 24, new_count)

        # تسجيل التاريخ
        try:
            history_ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
            if history_ws:
                history_ws.append_row([
                    datetime.now().isoformat(),
                    id,
                    f"تم تحديث الحقول: {', '.join(updates.keys())}",
                    employee_name
                ])
        except Exception as e:
            logger.error(f"فشل تسجيل التاريخ: {e}")

        # إشعار للمدير
        if Config.ADMIN_CHAT_ID and background_loop and bot_app:
            try:
                asyncio.run_coroutine_threadsafe(
                    bot_app.bot.send_message(
                        chat_id=Config.ADMIN_CHAT_ID,
                        text=f"✏️ *تحديث معاملة*\nالمعاملة: {id}\nبواسطة: {employee_name}",
                        parse_mode='Markdown'
                    ),
                    background_loop
                )
            except Exception as e:
                logger.error(f"فشل إرسال إشعار البوت: {e}")

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

@app.route('/ping')
def ping():
    return "pong"

@app.route('/test-email')
def test_email():
    logger.info("📩 تم استدعاء /test-email")
    try:
        success = EmailService.send_customer_email(
            Config.RESEND_FROM_EMAIL,
            "اختبار",
            "TEST123",
            f"{Config.WEB_APP_URL}/qr/TEST123"
        )
        logger.info(f"✅ نتيجة الإرسال: {success}")
        return "تم الإرسال" if success else "فشل"
    except Exception as e:
        logger.error(f"🔥 خطأ في test_email: {e}", exc_info=True)
        return f"خطأ: {e}", 500

# ------------------ صفحة عرض QR كبيرة ------------------
@app.route('/qr/<id>')
def qr_page(id):
    view_link = f"{Config.WEB_APP_URL}/view/{id}"
    qr_base64 = QRGenerator.generate_qr(view_link)
    html = f"""
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>QR Code للمعاملة {id}</title>
        <style>
            body {{ display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f5f5f5; }}
            img {{ max-width: 90%; max-height: 90%; border: 1px solid #ddd; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
        </style>
    </head>
    <body>
        <img src="data:image/png;base64,{qr_base64}" alt="QR Code للمعاملة {id}">
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

# ------------------ صفحات HTML ------------------
INDEX_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>المعاملات</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-6xl mx-auto">
        <h1 class="text-2xl font-bold mb-4">📋 جميع المعاملات (المدير)</h1>
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
    </script>
</body>
</html>"""

EDIT_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>تعديل المعاملة</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
        * { font-family: 'Inter', sans-serif; }
        .ios-card { background: rgba(255,255,255,0.8); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.3); border-radius: 16px; }
        .ios-input { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 16px; font-size: 16px; width: 100%; }
        .ios-input:focus { border-color: #007aff; outline: none; box-shadow: 0 0 0 3px rgba(0,122,255,0.1); }
        .ios-select { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 16px; font-size: 16px; width: 100%; }
        .label-ios { font-size: 14px; font-weight: 600; color: #6b7280; margin-bottom: 4px; display: block; }
        .timeline-item { border-right: 2px solid #007aff; position: relative; padding-right: 20px; margin-bottom: 20px; }
        .timeline-dot { width: 12px; height: 12px; background: #007aff; border-radius: 50%; position: absolute; right: -7px; top: 5px; }
    </style>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-3xl mx-auto">
        <div class="ios-card rounded-2xl p-4 mb-4 shadow-sm flex justify-between items-center">
            <h1 class="text-xl font-semibold">🔍 تتبع المعاملة <span id="transaction-id" class="text-blue-600"></span></h1>
            <a href="/" class="text-blue-500 text-sm">← العودة</a>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">📋 معلومات أساسية</h2>
            <div id="readonly-fields" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">✏️ تحديث البيانات</h2>
            <form id="editForm" class="space-y-4">
                <div id="editable-fields" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
                <button type="submit" class="w-full bg-blue-500 hover:bg-blue-600 text-white font-medium py-3 px-4 rounded-xl transition shadow-sm">💾 حفظ التغييرات</button>
            </form>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">📜 سجل الحركات</h2>
            <div id="history-timeline" class="space-y-2"></div>
        </div>

        <div id="message" class="fixed bottom-4 left-1/2 transform -translate-x-1/2 bg-gray-800 text-white px-6 py-3 rounded-xl shadow-lg opacity-0 transition-opacity"></div>
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

        fetch(`/api/transaction/${id}`)
            .then(res => res.ok ? res.json() : Promise.reject())
            .then(data => {
                const readonlyKeys = [
                    'Timestamp', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف', 'البريد الإلكتروني',
                    'القسم', 'نوع المعاملة', 'المرافقات'
                ];
                const rc = document.getElementById('readonly-fields');
                rc.innerHTML = '';
                readonlyKeys.forEach(key => {
                    if (data[key] !== undefined) {
                        const value = data[key] || '-';
                        let display = value;
                        if (key === 'المرافقات' && value.startsWith('http')) {
                            display = `<a href="${value}" target="_blank" class="text-blue-500 underline">📎 فتح المرفق</a>`;
                        }
                        rc.innerHTML += `
                            <div class="bg-gray-50 p-3 rounded-xl">
                                <span class="label-ios">${key}</span>
                                <div class="text-gray-900 mt-1">${display}</div>
                            </div>
                        `;
                    }
                });

                const excluded = ['ID', 'LOG_JSON', 'آخر تعديل بتاريخ', 'آخر تعديل بواسطة', 'الرابط'];
                const editableKeys = Object.keys(data).filter(k => !readonlyKeys.includes(k) && !excluded.includes(k));
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
                            <select name="${key}" class="ios-select">
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
                                <input type="date" name="${key}" value="${data[key] ? data[key].split('T')[0] : ''}" class="ios-input">
                            </div>
                        `;
                    } else {
                        ec.innerHTML += `
                            <div>
                                <label class="label-ios">${key}</label>
                                <input type="text" name="${key}" value="${data[key] || ''}" class="ios-input">
                            </div>
                        `;
                    }
                });
            })
            .catch(() => {
                document.body.innerHTML = '<div class="text-center text-red-500 p-10">❌ المعاملة غير موجودة</div>';
            });

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
                    t.innerHTML = '<p class="text-gray-500">لا يوجد سجل</p>';
                    return;
                }
                let html = '';
                h.forEach(i => {
                    html += `
                        <div class="timeline-item">
                            <span class="timeline-dot"></span>
                            <span class="text-sm text-gray-500">${i.time}</span>
                            <p class="text-gray-800">${i.action}</p>
                            <p class="text-xs text-gray-400">${i.user}</p>
                        </div>
                    `;
                });
                t.innerHTML = html;
            });
        }
        loadHistory();
    </script>
</body>
</html>"""

VIEW_HTML = """<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=yes">
    <title>تفاصيل المعاملة</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
        * { font-family: 'Inter', sans-serif; }
        .ios-card { background: rgba(255,255,255,0.8); backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.3); border-radius: 16px; }
        .label-ios { font-size: 14px; font-weight: 600; color: #6b7280; margin-bottom: 4px; display: block; }
        .timeline-item { border-right: 2px solid #007aff; position: relative; padding-right: 20px; margin-bottom: 20px; }
        .timeline-dot { width: 12px; height: 12px; background: #007aff; border-radius: 50%; position: absolute; right: -7px; top: 5px; }
    </style>
</head>
<body class="bg-gray-100 p-4">
    <div class="max-w-3xl mx-auto">
        <div class="ios-card rounded-2xl p-4 mb-4 shadow-sm flex justify-between items-center">
            <h1 class="text-xl font-semibold">🔍 تفاصيل المعاملة <span id="transaction-id" class="text-blue-600"></span></h1>
            <span class="text-gray-500 text-sm">(للمتابعة فقط)</span>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">📋 معلومات المعاملة</h2>
            <div id="fields" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </div>

        <div class="ios-card rounded-2xl p-5 mb-4 shadow-sm">
            <h2 class="text-lg font-semibold mb-3">📜 سجل الحركات</h2>
            <div id="history-timeline" class="space-y-2"></div>
        </div>
    </div>

    <script>
        const id = window.location.pathname.split('/').pop();
        document.getElementById('transaction-id').innerText = id;

        fetch(`/api/transaction/${id}`)
            .then(res => res.ok ? res.json() : Promise.reject())
            .then(data => {
                const fieldsDiv = document.getElementById('fields');
                fieldsDiv.innerHTML = '';
                const excluded = ['ID', 'LOG_JSON', 'آخر تعديل بتاريخ', 'آخر تعديل بواسطة', 'الرابط'];
                for (let key in data) {
                    if (!excluded.includes(key)) {
                        const value = data[key] || '-';
                        let display = value;
                        if (key === 'المرافقات' && value.startsWith('http')) {
                            display = `<a href="${value}" target="_blank" class="text-blue-500 underline">📎 فتح المرفق</a>`;
                        }
                        fieldsDiv.innerHTML += `
                            <div class="bg-gray-50 p-3 rounded-xl">
                                <span class="label-ios">${key}</span>
                                <div class="text-gray-900 mt-1">${display}</div>
                            </div>
                        `;
                    }
                }
            })
            .catch(() => {
                document.body.innerHTML = '<div class="text-center text-red-500 p-10">❌ المعاملة غير موجودة</div>';
            });

        function loadHistory() {
            fetch(`/api/history/${id}`).then(r => r.json()).then(h => {
                const t = document.getElementById('history-timeline');
                if (h.length === 0) {
                    t.innerHTML = '<p class="text-gray-500">لا يوجد سجل</p>';
                    return;
                }
                let html = '';
                h.forEach(i => {
                    html += `
                        <div class="timeline-item">
                            <span class="timeline-dot"></span>
                            <span class="text-sm text-gray-500">${i.time}</span>
                            <p class="text-gray-800">${i.action}</p>
                            <p class="text-xs text-gray-400">${i.user}</p>
                        </div>
                    `;
                });
                t.innerHTML = html;
            });
        }
        loadHistory();
    </script>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/transaction/<id>')
def edit_transaction_page(id):
    return render_template_string(EDIT_HTML)

@app.route('/view/<id>')
def view_transaction_page(id):
    return render_template_string(VIEW_HTML)

# ------------------ Webhook ------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    if bot_app is None or background_loop is None:
        return "Bot not initialized", 500
    try:
        logger.info("📩 تم استقبال طلب webhook")
        json_str = request.get_data(as_text=True)
        update = Update.de_json(json.loads(json_str), bot_app.bot)
        asyncio.run_coroutine_threadsafe(bot_app.process_update(update), background_loop)
        return "OK"
    except Exception as e:
        logger.error(f"خطأ في webhook: {e}")
        return "Error", 500