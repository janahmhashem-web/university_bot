import os
import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from config import Config

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_customer_email(customer_email, customer_name, transaction_id, qr_page_url):
        try:
            if not customer_email:
                logger.error("❌ البريد الإلكتروني فارغ!")
                return False

            logger.info(f"📧 محاولة إرسال إيميل عبر SendGrid إلى {customer_email}")

            # بناء المحتوى
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

            message = Mail(
                from_email=Config.EMAIL_USER,
                to_emails=customer_email,
                subject=f"📄 معاملة جديدة: {transaction_id}",
                html_content=html_content
            )

            sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
            response = sg.send(message)

            if response.status_code in [200, 201, 202]:
                logger.info(f"✅ تم إرسال الإيميل عبر SendGrid إلى {customer_email}")
                return True
            else:
                logger.error(f"❌ فشل SendGrid: {response.status_code} - {response.body}")
                return False

        except Exception as e:
            logger.error(f"❌ خطأ في الإيميل: {e}", exc_info=True)
            return False