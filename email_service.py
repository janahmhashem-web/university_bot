import os
import resend
import logging
from config import Config

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_customer_email(customer_email, customer_name, transaction_id, qr_page_url):
        """
        إرسال إيميل عبر Resend API.
        بعد توثيق النطاق (مثل universitybot-production.up.railway.app)،
        يمكن الإرسال إلى أي بريد إلكتروني.
        """
        try:
            if not customer_email:
                logger.error("❌ البريد الإلكتروني فارغ!")
                return False

            # تهيئة Resend بالمفتاح
            resend.api_key = os.getenv("RESEND_API_KEY")

            logger.info(f"📧 محاولة إرسال إيميل عبر Resend إلى {customer_email}")

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

            # استخدام النطاق الموثق الخاص بك
            # بعد توثيق النطاق، استخدم بريداً على هذا النطاق (مثل noreply@universitybot-production.up.railway.app)
            from_email = f"نظام المعاملات <noreply@{Config.WEB_APP_URL.replace('https://', '')}>"

            # إرسال الإيميل
            params = {
                "from": from_email,
                "to": [customer_email],
                "subject": f"📄 معاملة جديدة: {transaction_id}",
                "html": html_content,
            }

            response = resend.Emails.send(params)
            logger.info(f"✅ تم إرسال الإيميل عبر Resend إلى {customer_email} (ID: {response['id']})")
            return True

        except resend.exceptions.ResendError as e:
            logger.error(f"❌ خطأ Resend: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع: {e}", exc_info=True)
            return False