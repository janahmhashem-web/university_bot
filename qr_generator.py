import qrcode
from io import BytesIO
import base64
from datetime import datetime
from config import Config

class QRGenerator:
    @staticmethod
    def generate_qr(data, size=300):
        qr = qrcode.QRCode(
            version=1,
            box_size=10,
            border=5,
            error_correction=qrcode.constants.ERROR_CORRECT_L
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img = img.resize((size, size))

        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        return img_base64

    @staticmethod
    def save_qr_to_sheet(sheet_client, transaction_id, link, manager_values):
        timestamp = datetime.now().isoformat()
        name = manager_values.get('اسم صاحب المعاملة الثلاثي', '')
        qr_base64 = QRGenerator.generate_qr(link)
        sheet_client.append_row(Config.SHEET_QR, [
            timestamp, name, transaction_id, link, f'=IMAGE("data:image/png;base64,{qr_base64}")', link
        ])
        return qr_base64