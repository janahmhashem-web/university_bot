import os
import requests
import logging
from config import Config

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_customer_email(customer_email, customer_name, transaction_id, qr_page_url):
        try:
            if not customer_email:
                logger.error("❌ البريد الإلكتروني فارغ!")
                return False

            logger.info(f"📧 محاولة إرسال إيميل عبر Brevo إلى {customer_email}")

            # بناء الروابط
            bot_link = f"https://t.me/{Config.BOT_USERNAME}"
            transaction_link = f"{Config.WEB_APP_URL}/view/{transaction_id}"

            # محتوى HTML للإيميل
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

            # إعداد API Brevo
            url = "https://api.brevo.com/v3/smtp/email"
            headers = {
                "accept": "application/json",
                "api-key": os.getenv("BREVO_API_KEY"),
                "content-type": "application/json"
            }
            payload = {
                "sender": {"email": Config.EMAIL_USER, "name": "نظام المعاملات"},
                "to": [{"email": customer_email, "name": customer_name}],
                "subject": f"📄 معاملة جديدة: {transaction_id}",
                "htmlContent": html_content
            }

            response = requests.post(url, json=payload, headers=headers, timeout=10)

            if response.status_code == 201:
                logger.info(f"✅ تم إرسال الإيميل عبر Brevo إلى {customer_email}")
                return True
            else:
                logger.error(f"❌ فشل Brevo: {response.status_code} - {response.text}")
                return False

        except requests.exceptions.Timeout:
            logger.error("❌ مهلة الاتصال انتهت")
            return False
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع: {e}", exc_info=True)
            return False