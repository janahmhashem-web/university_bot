import requests
import os

# ===== إعدادات الاختبار =====
BREVO_API_KEY = "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"
SENDER_EMAIL = "janahmhashem@gmail.com"  # بريدك
TEST_EMAIL = "janahmhashem@gmail.com"    # استخدم بريدك للتجربة
# ============================

url = "https://api.brevo.com/v3/smtp/email"
headers = {
    "accept": "application/json",
    "api-key": BREVO_API_KEY,
    "content-type": "application/json"
}
payload = {
    "sender": {"email": SENDER_EMAIL, "name": "اختبار"},
    "to": [{"email": TEST_EMAIL, "name": "مستخدم اختبار"}],
    "subject": "📧 اختبار من النظام",
    "htmlContent": "<h1>هذا اختبار</h1><p>إذا وصلتك هذه الرسالة، فالإيميل يعمل.</p>"
}

try:
    print("🔍 جاري إرسال الطلب إلى Brevo...")
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    print(f"📬 رمز الحالة: {response.status_code}")
    print(f"📄 نص الرد: {response.text}")
    if response.status_code == 201:
        print("✅ تم الإرسال بنجاح!")
    else:
        print("❌ فشل الإرسال!")
except Exception as e:
    print(f"🔥 خطأ غير متوقع: {e}")