import smtplib
import os

@app.route('/test-email')
def test_email():
    try:
        smtp_server = "smtp-relay.brevo.com"
        port = 587
        username = "janahmhashem@gmail.com"
        password = os.getenv("BREVO_SMTP_KEY")
        if not password:
            return "❌ مفتاح SMTP غير موجود في متغيرات البيئة"

        msg = "Subject: اختبار\n\nهذه رسالة تجريبية من البوت."
        with smtplib.SMTP(smtp_server, port) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(username, "test@mailinator.com", msg)
        return "✅ تم إرسال البريد بنجاح"
    except Exception as e:
        return f"❌ فشل: {e}"