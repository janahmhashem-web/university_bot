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

            logger.info(f"📧 محاولة إرسال إيميل إلى {customer_email}")

            bot_link = f"https://t.me/{Config.BOT_USERNAME}"
            transaction_link = f"{Config.WEB_APP_URL}/view/{transaction_id}"

            msg = MIMEMultipart('alternative')
            msg['Subject'] = f'📄 معاملة جديدة: {transaction_id}'
            msg['From'] = Config.EMAIL_USER
            msg['To'] = customer_email

            html = f"""
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
            msg.attach(MIMEText(html, 'html'))

            with smtplib.SMTP(Config.EMAIL_HOST, Config.EMAIL_PORT, timeout=10) as server:
                server.starttls()
                server.login(Config.EMAIL_USER, Config.EMAIL_PASSWORD)
                server.send_message(msg)

            logger.info(f"✅ تم إرسال البريد إلى {customer_email}")
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("❌ فشل المصادقة. تحقق من كلمة المرور.")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"❌ خطأ SMTP: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ خطأ عام: {e}", exc_info=True)
            return False