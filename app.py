@app.route('/test-email')
def test_email():
    from services.email_service import EmailService
    result = EmailService.send_customer_email(
        customer_email='test@mailinator.com',
        customer_name='اختبار',
        transaction_id='TEST123',
        qr_page_url='https://example.com'
    )
    return '✅ تم الإرسال' if result else '❌ فشل'