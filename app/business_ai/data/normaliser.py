#app/business_ai/data/normaliser.py
from __future__ import annotations

from typing import Any


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def parse_float(value: Any, default: float = 0.0) -> float:
    text = clean_text(value).replace("£", "").replace(",", "")
    if not text:
        return default

    try:
        return float(text)
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    text = clean_text(value)
    if not text:
        return default

    try:
        return int(float(text))
    except Exception:
        return default


def normalise_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []

    for order in orders or []:
        normalised_items = []

        for item in order.get("items", []) or []:
            item_id = clean_text(item.get("id"))
            quantity = parse_int(item.get("quantity", 1), default=1)
            price = parse_float(item.get("price", 0), default=0.0)

            normalised_items.append(
                {
                    "id": item_id,
                    "quantity": quantity if quantity > 0 else 1,
                    "price": round(price, 2),
                }
            )

        total = parse_float(order.get("total", 0), default=0.0)

        if total <= 0 and normalised_items:
            total = sum(item["quantity"] * item["price"] for item in normalised_items)

        normalised.append(
            {
                "id": clean_text(order.get("id")),
                "created_at": clean_text(order.get("created_at")),
                "items": normalised_items,
                "total": round(total, 2),
            }
        )

    return normalised