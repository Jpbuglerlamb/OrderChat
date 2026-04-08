from __future__ import annotations

from typing import Any

from app.business_ai.utils.item_ids import build_menu_lookup, resolve_item_to_menu
from app.business_ai.utils.parsing import clean_text, parse_float, parse_int


def normalise_orders(
    orders: list[dict[str, Any]],
    menu_data: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    normalised: list[dict[str, Any]] = []
    unmatched_items: set[str] = set()

    item_lookup, alias_lookup = build_menu_lookup(menu_data)

    for order in orders or []:
        normalised_items: list[dict[str, Any]] = []

        for item in order.get("items", []) or []:
            raw_item_id = clean_text(item.get("id") or item.get("name"))
            resolution = resolve_item_to_menu(raw_item_id, item_lookup, alias_lookup)

            quantity = parse_int(item.get("quantity", 1), default=1)
            price = parse_float(item.get("price", 0), default=0.0)

            resolved_item_id = resolution.get("canonical_id") or ""
            matched = bool(resolution.get("matched"))

            if raw_item_id and not matched and alias_lookup:
                unmatched_items.add(raw_item_id)

            if not resolved_item_id:
                continue

            normalised_items.append(
                {
                    "id": resolved_item_id,
                    "quantity": quantity if quantity > 0 else 1,
                    "price": round(price, 2),
                    "matched": matched,
                    "raw_id": raw_item_id,
                    "match_method": resolution.get("match_method", "unknown"),
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

    return normalised, sorted(unmatched_items)