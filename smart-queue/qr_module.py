"""
qr_module.py — QR code generation.
Generates a PNG QR code per appointment ID and saves it under static/qrcodes/.
"""
import os
import qrcode
from PIL import Image

QR_DIR = os.path.join(os.path.dirname(__file__), "static", "qrcodes")
os.makedirs(QR_DIR, exist_ok=True)


def generate_qr(record_id: str, base_url: str = "http://localhost:5000") -> str:
    """
    Generate a QR code that encodes the check-in URL for the given record ID.
    Returns the relative path to the saved image: 'static/qrcodes/<id>.png'
    """
    check_in_url = f"{base_url}/checkin/{record_id}"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(check_in_url)
    qr.make(fit=True)

    img: Image.Image = qr.make_image(fill_color="#6c47ff", back_color="#0f0f1a")
    path = os.path.join(QR_DIR, f"{record_id}.png")
    img.save(path)
    return f"qrcodes/{record_id}.png"
