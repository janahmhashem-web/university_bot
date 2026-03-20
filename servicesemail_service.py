import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_customer_email(customer_email, customer_name, transaction_id, qr_page_url):
        """إرسال بريد إلكتروني للعميل عبر Brevo SMTP"""
        try:
            if not customer_email:
                logger.error("❌ البريد الإلكتروني فارغ!")
                return False

            smtp_server = "smtp-relay.brevo.com"
            smtp_port = 587
            smtp_username = os.getenv("EMAIL_USER", "janahmhashem@gmail.com")
            smtp_password = os.getenv("BREVO_SMTP_KEY")

            if not smtp_password:
                logger.error("❌ BREVO_SMTP_KEY غير مضبوط في المتغيرات البيئية")
                return False

            from_email = smtp_username

            # بناء الروابط
            bot_link = f"https://t.me/{os.getenv('BOT_USERNAME', 'mtu_jit_bot')}"
            transaction_link = f"{os.getenv('WEB_APP_URL')}/view/{transaction_id}"

            html_content = f"""
            <html>
            <body dir="rtl">
                <p>مرحباً {customer_name}،</p>
                <p>تم إنشاء معاملة جديدة برقم: <strong>{transaction_id}</strong></p>
                <p>لعرض تفاصيل المعاملة: <a href="{transaction_link}">اضغط هنا</a></p>
                <p>لعرض رمز QR: <a href="{qr_page_url}">اضغط هنا</a></p>
                <p>لمتابعة المعاملة عبر البوت: <a href="{bot_link}">@{os.getenv('BOT_USERNAME')}</a></p>
                <p>مع الشكر،</p>
                <p>فريق النظام</p>
            </body>
            </html>
            """

            msg = MIMEMultipart()
            msg['From'] = from_email
            msg['To'] = customer_email
            msg['Subject'] = f"📄 معاملة جديدة: {transaction_id}"
            msg.attach(MIMEText(html_content, 'html'))

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(msg)

            logger.info(f"✅ تم إرسال الإيميل إلى {customer_email}")
            return True

        except Exception as e:
            logger.error(f"❌ فشل إرسال الإيميل: {e}", exc_info=True)
            return False