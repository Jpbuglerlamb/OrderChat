# app/business_ai/data/normaliser.py
from __future__ import annotations

from typing import Any
import re


def canonicalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("&", "and")
    value = value.replace("_", " ")
    value = value.replace("-", " ")
    value = re.sub(r"[^a-z0-9\s]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


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


def build_menu_lookup(menu_data: dict[str, Any] | None) -> dict[str, str]:
    lookup: dict[str, str] = {}

    if not menu_data:
        return lookup

    for item in menu_data.get("items", []) or []:
        item_id = clean_text(item.get("id"))
        item_name = clean_text(item.get("name"))

        if not item_id:
            continue

        variants = {
            canonicalize_text(item_id),
            canonicalize_text(item_name),
        }

        # extra variant: remove common filler words
        simplified_name = canonicalize_text(item_name)
        simplified_name = re.sub(
            r"\b(size|large|small|regular|portion|meal|deal|sauce|can|bottle)\b",
            "",
            simplified_name,
        )
        simplified_name = re.sub(r"\s+", " ", simplified_name).strip()
        if simplified_name:
            variants.add(simplified_name)

        for variant in variants:
            if variant:
                lookup[variant] = item_id

    return lookup


def resolve_item_id(raw_item_id: str, menu_lookup: dict[str, str]) -> tuple[str, bool]:
    raw_item_id = clean_text(raw_item_id)
    canonical = canonicalize_text(raw_item_id)

    if not canonical:
        return "", False

    # exact canonical match
    matched_id = menu_lookup.get(canonical)
    if matched_id:
        return matched_id, True

    # containment fallback
    for lookup_key, lookup_item_id in menu_lookup.items():
        if canonical in lookup_key or lookup_key in canonical:
            return lookup_item_id, True

    # token overlap fallback
    raw_tokens = set(canonical.split())
    best_match = None
    best_score = 0

    for lookup_key, lookup_item_id in menu_lookup.items():
        lookup_tokens = set(lookup_key.split())
        overlap = len(raw_tokens & lookup_tokens)

        if overlap > best_score:
            best_score = overlap
            best_match = lookup_item_id

    if best_match and best_score >= 2:
        return best_match, True

    fallback = canonical.replace(" ", "_")
    return fallback, False


def normalise_orders(
    orders: list[dict[str, Any]],
    menu_data: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    normalised: list[dict[str, Any]] = []
    unmatched_items: set[str] = set()

    menu_lookup = build_menu_lookup(menu_data)

    for order in orders or []:
        normalised_items = []

        for item in order.get("items", []) or []:
            raw_item_id = clean_text(item.get("id") or item.get("name"))
            resolved_item_id, matched = resolve_item_id(raw_item_id, menu_lookup)

            quantity = parse_int(item.get("quantity", 1), default=1)
            price = parse_float(item.get("price", 0), default=0.0)

            if raw_item_id and not matched and menu_lookup:
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