# إرسال الإيميل
customer_email = new_row.get('البريد الإلكتروني')  # تأكد من الاسم الصحيح
customer_name = new_row.get('اسم صاحب المعاملة الثلاثي')
logger.info(f"📧 قراءة البريد من الشيت: '{customer_email}' للمعاملة {transaction_id}")
if transaction_id and customer_email:
    try:
        qr_page_link = f"{Config.WEB_APP_URL}/qr/{transaction_id}"
        success = EmailService.send_customer_email(
            customer_email,
            customer_name,
            transaction_id,
            qr_page_link
        )
        if success:
            logger.info(f"📧 تم إرسال إيميل للمعاملة {transaction_id}")
        else:
            logger.error(f"❌ فشل إرسال الإيميل للمعاملة {transaction_id}")
    except Exception as e:
        logger.error(f"❌ استثناء أثناء إرسال الإيميل: {e}")
else:
    logger.warning(f"⚠️ لا يمكن إرسال الإيميل: ID={transaction_id}, email={customer_email}")