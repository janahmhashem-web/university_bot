import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
from config import Config

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_customer_email(customer_email, customer_name, transaction_id, qr_image_url):
        try:
            if not customer_email or not transaction_id:
                logger.error("❌ بيانات الإيميل ناقصة: البريد أو ID فارغ")
                return False

            bot_link = f"https://t.me/{Config.BOT_USERNAME}"
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'📄 معاملة جديدة: {transaction_id}'
            msg['From'] = Config.EMAIL_USER
            msg['To'] = customer_email

            html = f"""
            <html>
            <body dir="rtl">
                <p>رقم المعاملة: {transaction_id}</p>
                <p>رابط QR: <a href="{qr_image_url}">اضغط هنا</a></p>
                <p>رابط البوت: <a href="{bot_link}">@{Config.BOT_USERNAME}</a></p>
            </body>
            </html>
            """
            msg.attach(MIMEText(html, 'html'))

            with smtplib.SMTP(Config.EMAIL_HOST, Config.EMAIL_PORT) as server:
                server.starttls()
                server.login(Config.EMAIL_USER, Config.EMAIL_PASSWORD)
                server.send_message(msg)

            logger.info(f"✅ تم إرسال البريد إلى {customer_email} للمعاملة {transaction_id}")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("❌ فشل المصادقة مع Gmail. تأكد من استخدام كلمة مرور تطبيق (بدون مسافات)")
            return False
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع في إرسال البريد: {e}")
            return False