#app/services/qr_service.py
from __future__ import annotations

import io

import qrcode


def build_restaurant_public_url(base_url: str, slug: str) -> str:
    return f"{base_url.rstrip('/')}/r/{slug}"


def generate_qr_png_bytes(url: str) -> bytes:
    img = qrcode.make(url)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()