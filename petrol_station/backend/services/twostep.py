"""2-step login codes (TOTP, RFC 6238) - the 6-digit codes of Google/Microsoft Authenticator and similar apps.

The app and the server share a random key; each 30 seconds both work out the same code from it.
A code is accepted once only, and one step either side is allowed for a phone clock that is a little off.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
ISSUER = "Kigala Energies"


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS
    return f"{number:0{DIGITS}d}"


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(secret, code, last_step=None, now=None):
    """The step the code belongs to if it is right and not used before, else None."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    now_step = current_step(now)
    for step in (now_step - 1, now_step, now_step + 1):
        if (last_step is None or step > last_step) and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def setup_link(secret, username):
    """otpauth:// link: tapping it on the phone, or scanning its QR code, adds the account to the app."""
    label = quote(f"{ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP_SECONDS}"


def qr_svg(text, size=220):
    """The link as a QR code picture (SVG), drawn on the server - no outside service sees the key."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    widget = QrCodeWidget(text)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    return renderSVG.drawToString(drawing)
