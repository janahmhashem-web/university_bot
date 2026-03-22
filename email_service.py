import logging
import requests
from config import Config

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_customer_email(customer_email, customer_name, transaction_id, qr_page_url):
        try:
            if not customer_email:
                logger.error("❌ البريد الإلكتروني فارغ!")
                return False

            api_key = Config.BREVO_API_KEY
            if not api_key:
                logger.error("❌ BREVO_API_KEY غير مضبوط")
                return False

            from_email = Config.BREVO_FROM_EMAIL
            from_name = Config.BREVO_FROM_NAME or "نظام المعاملات"
            bot_link = f"https://t.me/{Config.BOT_USERNAME}"
            transaction_link = f"{Config.WEB_APP_URL}/view/{transaction_id}"

            html_content = f"""
            <html>
            <body dir="rtl">
                <p>مرحباً {customer_name}،</p>
                <p>تم إنشاء معاملة جديدة برقم: <strong>{transaction_id}</strong></p>
                <p>لعرض تفاصيل المعاملة: <a href="{transaction_link}">اضغط هنا</a></p>
                <p>لعرض رمز QR: <a href="{qr_page_url}">اضغط هنا</a></p>
                <p>لمتابعة المعاملة عبر البوت: <a href="{bot_link}">@{Config.BOT_USERNAME}</a></p>
                <p>مع الشكر،</p>
                <p>فريق النظام</p>
            </body>
            </html>
            """

            payload = {
                "sender": {"name": from_name, "email": from_email},
                "to": [{"email": customer_email, "name": customer_name or "عميل"}],
                "subject": f"📄 معاملة جديدة: {transaction_id}",
                "htmlContent": html_content
            }

            headers = {
                "api-key": api_key,
                "Content-Type": "application/json"
            }

            response = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code in (200, 201):
                logger.info(f"✅ تم إرسال الإيميل إلى {customer_email} عبر Brevo API")
                return True
            else:
                logger.error(f"❌ فشل إرسال الإيميل عبر Brevo API: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"❌ خطأ في إرسال الإيميل: {e}", exc_info=True)
            return False