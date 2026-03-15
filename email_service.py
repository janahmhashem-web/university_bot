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
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'📄 معاملة جديدة: {transaction_id}'
            msg['From'] = Config.EMAIL_USER
            msg['To'] = customer_email

            bot_link = f"https://t.me/{Config.BOT_USERNAME}"
            transaction_link = f"{Config.WEB_APP_URL}/transaction/{transaction_id}"

            html = f"""
            <div dir="rtl" style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px;">
                <h2 style="color: #2c3e50;">مرحباً {customer_name}،</h2>
                <p>تم إنشاء معاملتك بنجاح. يمكنك متابعتها باستخدام المعلومات التالية:</p>
                <p><strong>رقم المعاملة:</strong> {transaction_id}</p>
                <p><strong>رابط متابعة المعاملة:</strong> <a href="{transaction_link}">اضغط هنا</a></p>
                <p><strong>بوت المتابعة:</strong> <a href="{bot_link}">@{Config.BOT_USERNAME}</a></p>
                <p><strong>رابط صورة QR:</strong> <a href="{qr_image_url}">اضغط هنا لعرض QR</a></p>
                <p style="margin-top: 30px;">مع الشكر،<br>فريق النظام</p>
            </div>
            """
            msg.attach(MIMEText(html, 'html'))

            with smtplib.SMTP(Config.EMAIL_HOST, Config.EMAIL_PORT) as server:
                server.starttls()
                server.login(Config.EMAIL_USER, Config.EMAIL_PASSWORD)
                server.send_message(msg)
            logger.info(f"✅ تم إرسال البريد إلى {customer_email} للمعاملة {transaction_id}")
            return True
        except Exception as e:
            logger.error(f"❌ فشل إرسال البريد: {e}")
            return False