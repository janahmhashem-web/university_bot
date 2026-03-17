import os
import aiohttp
import asyncio
import logging
from config import Config

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    async def send_customer_email(customer_email, customer_name, transaction_id, qr_page_url):
        """
        إرسال إيميل عبر Lemon Email API
        """
        try:
            if not customer_email:
                logger.error("❌ البريد الإلكتروني فارغ!")
                return False

            logger.info(f"📧 محاولة إرسال إيميل عبر Lemon Email إلى {customer_email}")

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

            lemon_api_url = f"{os.getenv('LEMON_EMAIL_URL')}/api/transactional/send"
            lemon_api_key = os.getenv('LEMON_EMAIL_API_KEY')

            payload = {
                "fromname": "نظام المعاملات",
                "fromemail": "no-reply@university-bot.com",
                "to": customer_email,
                "subject": f"📄 معاملة جديدة: {transaction_id}",
                "body": html_content
            }

            headers = {
                "Content-Type": "application/json",
                "X-Auth-APIKey": lemon_api_key
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(lemon_api_url, json=payload, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        logger.info(f"✅ تم إرسال الإيميل عبر Lemon Email إلى {customer_email}")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ فشل Lemon Email: {response.status} - {error_text}")
                        return False

        except asyncio.TimeoutError:
            logger.error("❌ مهلة الاتصال انتهت")
            return False
        except aiohttp.ClientError as e:
            logger.error(f"❌ خطأ في الاتصال: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع: {e}", exc_info=True)
            return False