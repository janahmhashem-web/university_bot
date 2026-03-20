import smtplib

smtp_host = "resend-railway-gateway.up.railway.app"  # استبدل بالرابط الفعلي
smtp_port = 587  # البوابة تعمل على هذا المنفذ

try:
    server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
    server.starttls()
    print("✅ اتصال ناجح بالبوابة!")
    server.quit()
except Exception as e:
    print(f"❌ فشل الاتصال: {e}")