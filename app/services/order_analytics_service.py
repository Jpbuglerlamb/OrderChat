from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import Order, Restaurant


def db_orders_to_pipeline_orders(db_orders: list[Order]) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []

    for order in db_orders:
        try:
            raw_items = json.loads(order.items_json or "[]")
        except Exception:
            raw_items = []

        if not isinstance(raw_items, list):
            raw_items = []

        items: list[dict[str, Any]] = []
        total = 0.0

        for item in raw_items:
            if not isinstance(item, dict):
                continue

            item_id = str(item.get("id") or item.get("item_id") or "").strip()
            if not item_id:
                continue

            try:
                quantity = int(item.get("quantity") or 1)
            except Exception:
                quantity = 1

            try:
                price = float(item.get("price") or item.get("base_price") or 0.0)
            except Exception:
                price = 0.0

            items.append(
                {
                    "id": item_id,
                    "quantity": quantity,
                    "price": price,
                }
            )
            total += quantity * price

        if not items:
            continue

        orders.append(
            {
                "id": f"db_order_{order.id}",
                "created_at": order.created_at.isoformat() if order.created_at else "",
                "items": items,
                "total": round(total, 2),
            }
        )

    return orders


def get_saved_orders_for_restaurant(db: Session, restaurant: Restaurant) -> list[dict[str, Any]]:
    db_orders = (
        db.query(Order)
        .filter(Order.restaurant_slug == restaurant.slug)
        .filter(Order.status == "confirmed")
        .all()
    )
    return db_orders_to_pipeline_orders(db_orders)