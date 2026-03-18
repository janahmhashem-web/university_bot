import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import Config

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_customer_email(customer_email, customer_name, transaction_id, qr_page_url):
        try:
            if not customer_email:
                logger.error("❌ البريد الإلكتروني فارغ!")
                return False

            logger.info(f"📧 محاولة إرسال إيميل عبر SMTP إلى {customer_email}")

            # بيانات SMTP الصحيحة من Brevo (للمصادقة فقط)
            smtp_host = "smtp-relay.brevo.com"
            smtp_port = 587
            smtp_user = "a527c3001@smtp-brevo.com"  # هذا ثابت ولا يتغير
            smtp_password = os.getenv("BREVO_API_KEY")  # مفتاح API

            # البريد الذي سيظهر للمستلم (يمكنك تغييره إلى بريدك)
            from_email = Config.EMAIL_USER  # janahmhashem@gmail.com

            # بناء الروابط
            bot_link = f"https://t.me/{Config.BOT_USERNAME}"
            transaction_link = f"{Config.WEB_APP_URL}/view/{transaction_id}"

            # محتوى HTML
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

            # إنشاء الرسالة
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"📄 معاملة جديدة: {transaction_id}"
            msg['From'] = from_email  # هنا سيظهر بريدك الشخصي
            msg['To'] = customer_email
            msg.attach(MIMEText(html_content, 'html'))

            # الاتصال والإرسال (نستخدم بيانات SMTP للمصادقة)
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)  # المصادقة ببيانات SMTP
                server.send_message(msg)

            logger.info(f"✅ تم إرسال الإيميل إلى {customer_email} من {from_email}")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("❌ فشل المصادقة: تحقق من مفتاح API")
            return False
        except Exception as e:
            logger.error(f"❌ خطأ في SMTP: {e}", exc_info=True)
            return False