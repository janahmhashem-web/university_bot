#!/usr/bin/env python
import logging
import sys
import threading
import os
from flask import Flask, request, jsonify, render_template_string
from telegram_bot import TransactionBot
from sheets import GoogleSheetsClient

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# إعداد Flask
app = Flask(__name__)
sheets_client = GoogleSheetsClient()

# صفحة HTML الرئيسية (قائمة المعاملات)
INDEX_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>قائمة المعاملات</title>
    <style>
        body { font-family: Arial; margin: 20px; background: #f4f4f4; }
        .container { max-width: 1200px; margin: auto; background: white; padding: 20px; border-radius: 8px; }
        table { width: 100%; border-collapse: collapse; }
        th { background: #007aff; color: white; padding: 10px; }
        td, th { border: 1px solid #ddd; padding: 8px; text-align: center; }
        tr:nth-child(even) { background-color: #f2f2f2; }
        a { color: #007aff; text-decoration: none; }
        .edit-link { background: #007aff; color: white; padding: 4px 12px; border-radius: 20px; }
    </style>
</head>
<body>
<div class="container">
    <h2>جميع المعاملات</h2>
    <table>
        <thead><tr><th>ID</th><th>الاسم</th><th>الحالة</th><th>الموظف</th><th>تعديل</th></tr></thead>
        <tbody id="transactions"></tbody>
    </table>
</div>
<script>
fetch('/api/transactions')
    .then(r => r.json())
    .then(data => {
        let html = '';
        data.forEach(t => {
            html += `<tr><td>${t.id}</td><td>${t.name}</td><td>${t.status || '-'}</td><td>${t.employee || '-'}</td><td><a href="/transaction/${t.id}" class="edit-link">✏️ تعديل</a></td></tr>`;
        });
        document.getElementById('transactions').innerHTML = html;
    });
</script>
</body>
</html>
"""

# صفحة HTML لتعديل معاملة واحدة
EDIT_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <title>تعديل المعاملة</title>
    <style>
        body { font-family: Arial; margin: 20px; background: #f4f4f4; }
        .container { max-width: 800px; margin: auto; background: white; padding: 20px; border-radius: 8px; }
        .field { margin-bottom: 15px; }
        label { display: block; font-weight: bold; margin-bottom: 5px; }
        input, select, textarea { width: 100%; padding: 8px; border: 1px solid #ccc; border-radius: 4px; }
        input[readonly] { background-color: #eee; }
        button { background: #007aff; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; }
        .message { padding: 10px; margin-top: 15px; border-radius: 4px; display: none; }
        .success { background: #d4edda; color: #155724; display: block; }
        .error { background: #f8d7da; color: #721c24; display: block; }
    </style>
</head>
<body>
<div class="container">
    <h2 id="title">تعديل المعاملة</h2>
    <div id="fields"></div>
    <button onclick="save()">💾 حفظ</button>
    <div id="message" class="message"></div>
</div>
<script>
const params = new URLSearchParams(window.location.search);
const id = params.get('id') || window.location.pathname.split('/')[2];
document.getElementById('title').innerText = 'تعديل المعاملة ' + id;

fetch(`/api/transaction/${id}`)
    .then(r => r.json())
    .then(data => {
        let html = '';
        // الحقول للقراءة فقط
        const readonlyFields = ['الطابع الزمني', 'اسم صاحب المعاملة الثلاثي', 'رقم الهاتف', 'البريد الإلكتروني', 'القسم', 'نوع المعاملة', 'المرافقات'];
        // باقي الحقول قابلة للتعديل
        for (let key in data) {
            if (key.startsWith('_')) continue;
            if (readonlyFields.includes(key)) {
                html += `<div class="field"><label>${key}</label><input type="text" value="${escapeHtml(data[key]) || ''}" readonly></div>`;
            } else {
                if (key === 'الحالة') {
                    html += `<div class="field"><label>${key}</label><select id="edit_${key}">` +
                        `<option value="قيد الانتظار" ${data[key] === 'قيد الانتظار' ? 'selected' : ''}>قيد الانتظار</option>` +
                        `<option value="موافق" ${data[key] === 'موافق' ? 'selected' : ''}>موافق</option>` +
                        `<option value="مكتملة" ${data[key] === 'مكتملة' ? 'selected' : ''}>مكتملة</option>` +
                        `<option value="فاشلة" ${data[key] === 'فاشلة' ? 'selected' : ''}>فاشلة</option>` +
                        `</select></div>`;
                } else if (key === 'الأولوية') {
                    html += `<div class="field"><label>${key}</label><select id="edit_${key}">` +
                        `<option value="عاجل" ${data[key] === 'عاجل' ? 'selected' : ''}>عاجل</option>` +
                        `<option value="عادي" ${data[key] === 'عادي' ? 'selected' : ''}>عادي</option>` +
                        `<option value="منخفض" ${data[key] === 'منخفض' ? 'selected' : ''}>منخفض</option>` +
                        `</select></div>`;
                } else if (key === 'تاريخ التحويل') {
                    const dateVal = data[key] ? data[key].split(' ')[0] : '';
                    html += `<div class="field"><label>${key}</label><input type="date" id="edit_${key}" value="${dateVal}"></div>`;
                } else {
                    html += `<div class="field"><label>${key}</label><input type="text" id="edit_${key}" value="${escapeHtml(data[key]) || ''}"></div>`;
                }
            }
        }
        document.getElementById('fields').innerHTML = html;
    });

function save() {
    const updates = {};
    document.querySelectorAll('[id^="edit_"]').forEach(input => {
        const field = input.id.replace('edit_', '');
        updates[field] = input.value;
    });
    fetch(`/api/transaction/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates)
    })
    .then(r => r.json())
    .then(result => {
        const msgDiv = document.getElementById('message');
        msgDiv.innerText = result.message;
        msgDiv.className = 'message ' + (result.success ? 'success' : 'error');
    });
}

function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return unsafe.toString().replace(/[&<>"]/g, function(m) {
        if (m === '&') return '&amp;';
        if (m === '<') return '&lt;';
        if (m === '>') return '&gt;';
        if (m === '"') return '&quot;';
        return m;
    });
}
</script>
</body>
</html>
"""

# ======================== مسارات Flask ========================
@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/transaction/<id>')
def edit_transaction_page(id):
    return render_template_string(EDIT_HTML)

@app.route('/api/transactions')
def api_transactions():
    records = sheets_client.get_all_records(Config.SHEET_MANAGER)
    result = []
    for r in records:
        result.append({
            'id': r.get('ID', ''),
            'name': r.get('اسم صاحب المعاملة الثلاثي', ''),
            'status': r.get('الحالة', ''),
            'employee': r.get('الموظف المسؤول', '')
        })
    return jsonify(result)

@app.route('/api/transaction/<id>', methods=['GET', 'POST'])
def api_transaction(id):
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
        headers = sheets_client.get_headers(Config.SHEET_MANAGER)
        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        for key, value in updates.items():
            col = headers.index(key) + 1
            ws.update_cell(row, col, value)
        # تحديث آخر تعديل
        if 'آخر تعديل بتاريخ' in headers:
            col = headers.index('آخر تعديل بتاريخ') + 1
            ws.update_cell(row, col, datetime.now().isoformat())
        return jsonify({'success': True, 'message': 'تم الحفظ بنجاح'})

# ======================== تشغيل البوت والخادم معاً ========================
def run_bot():
    bot = TransactionBot()
    bot.run()

if __name__ == "__main__":
    # تشغيل البوت في خيط منفصل
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    # تشغيل خادم الويب
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)