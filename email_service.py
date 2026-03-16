import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
            if not transaction_id:
                logger.error("❌ رقم المعاملة فارغ!")
                return False

            logger.info(f"📧 محاولة إرسال إيميل إلى {customer_email} للمعاملة {transaction_id}")

            bot_link = f"https://t.me/{Config.BOT_USERNAME}"
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'📄 معاملة جديدة: {transaction_id}'
            msg['From'] = Config.EMAIL_USER
            msg['To'] = customer_email

            html = f"""
            <html>
            <body dir="rtl">
                <p>رقم المعاملة: {transaction_id}</p>
                <p>رابط QR: <a href="{qr_page_url}">اضغط هنا لعرض QR</a></p>
                <p>رابط البوت: <a href="{bot_link}">@{Config.BOT_USERNAME}</a></p>
            </body>
            </html>
            """
            msg.attach(MIMEText(html, 'html'))

            with smtplib.SMTP(Config.EMAIL_HOST, Config.EMAIL_PORT) as server:
                server.starttls()
                server.login(Config.EMAIL_USER, Config.EMAIL_PASSWORD)
                server.send_message(msg)

            logger.info(f"✅ تم إرسال البريد بنجاح إلى {customer_email}")
            return True
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"❌ فشل المصادقة مع Gmail: {e}. تأكد من استخدام كلمة مرور تطبيق صحيحة (بدون مسافات)")
            return False
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع في إرسال البريد: {e}", exc_info=True)
            return False