import qrcode
from io import BytesIO
import base64
from datetime import datetime
from config import Config

class QRGenerator:
    @staticmethod
    def generate_qr(data, size=300):
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img = img.resize((size, size))
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()

    @staticmethod
    def get_qr_url(data):
        return f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={data}"

    @staticmethod
    def save_qr_to_sheet(sheet_client, transaction_id, link, manager_values):
        timestamp = datetime.now().isoformat()
        name = manager_values.get('اسم صاحب المعاملة الثلاثي', '')
        qr_url = QRGenerator.get_qr_url(link)
        sheet_client.append_row(Config.SHEET_QR, [
            timestamp, name, transaction_id, link, f'=IMAGE("{qr_url}")', qr_url
        ])
        return qr_url