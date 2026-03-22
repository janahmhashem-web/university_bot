import os
import requests
import logging

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_customer_email(customer_email, customer_name, transaction_id, qr_page_url):
        try:
            if not customer_email:
                logger.error("❌ البريد الإلكتروني فارغ!")
                return False

            api_key = os.getenv("RESEND_API_KEY")
            if not api_key:
                logger.error("❌ RESEND_API_KEY غير مضبوط")
                return False

            from_email = os.getenv("EMAIL_USER", "onboarding@resend.dev")  # بريد Resend الافتراضي
            # ملاحظة: onboarding@resend.dev يعمل فوراً بدون توثيق للاختبار
            
            bot_link = f"https://t.me/{os.getenv('BOT_USERNAME', 'mtu_jit_bot')}"
            transaction_link = f"{os.getenv('WEB_APP_URL')}/view/{transaction_id}"

            html_content = f"""
            <html>
            <body dir="rtl">
                <p>مرحباً {customer_name}،</p>
                <p>تم إنشاء معاملة جديدة برقم: <strong>{transaction_id}</strong></p>
                <p>لعرض التفاصيل: <a href="{transaction_link}">اضغط هنا</a></p>
                <p>لمتابعة المعاملة عبر البوت: <a href="{bot_link}">@{os.getenv('BOT_USERNAME')}</a></p>
                <p>مع الشكر،</p>
                <p>فريق النظام</p>
            </body>
            </html>
            """

            response = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": from_email,
                    "to": [customer_email],
                    "subject": f"📄 معاملة جديدة: {transaction_id}",
                    "html": html_content
                },
                timeout=30
            )

            if response.status_code == 200:
                logger.info(f"✅ تم إرسال الإيميل إلى {customer_email}")
                return True
            else:
                logger.error(f"❌ فشل Resend: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"❌ خطأ في الإيميل: {e}", exc_info=True)
            return False