import qrcode
import io
import base64

class QRGenerator:
    @staticmethod
    def generate_qr(data, box_size=10, border=4):
        """
        توليد رمز QR كصورة بصيغة Base64
        
        المعاملات:
            data (str): البيانات التي سيتم ترميزها في QR (رابط، نص، إلخ)
            box_size (int): حجم كل مربع في الـ QR (افتراضي 10)
            border (int): حجم الحدود حول الـ QR (افتراضي 4)
        
        العائد:
            str: الصورة بتنسيق Base64 (يمكن استخدامها مباشرة في HTML كـ data:image/png;base64,...)
        """
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=box_size,
            border=border,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')
