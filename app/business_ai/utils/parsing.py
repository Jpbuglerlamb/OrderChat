from __future__ import annotations

import re
from typing import Any


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def parse_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)

    text = clean_text(value).replace("£", "").replace(",", "")
    if not text:
        return default

    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return default

    try:
        return float(match.group(0))
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    text = clean_text(value)
    if not text:
        return default

    match = re.search(r"-?\d+", text)
    if not match:
        return default

    try:
        return int(match.group(0))
    except Exception:
        return default


def normalise_created_at(value: str) -> str:
    text = clean_text(value)
    if " " in text and "T" not in text:
        return text.replace(" ", "T", 1)
    return text