#app/business_ai/services/order_history_vision.py
from __future__ import annotations

from typing import Any


def extract_order_history_from_image_with_ai(
    *,
    image_bytes: bytes,
    filename: str,
) -> list[dict[str, Any]]:
    """
    Future AI-powered extraction from screenshots / printed reports / photos.

    For now this is intentionally disabled so JPG/PNG uploads
    fail cleanly with a helpful message instead of fake results.
    """
    raise RuntimeError(
        "Image order-history extraction is not implemented yet. "
        "Please upload JSON, CSV, or PDF files."
    )