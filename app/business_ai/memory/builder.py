#app/business_ai/insights/formatter.py
from __future__ import annotations

import re
from difflib import get_close_matches


def slugify_text(value: str) -> str:
    value = str(value or "").strip().lower()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def build_menu_lookup(menu_data: dict) -> tuple[dict[str, dict], dict[str, str]]:
    items = menu_data.get("items") or []

    item_lookup: dict[str, dict] = {}
    alias_lookup: dict[str, str] = {}

    for item in items:
        item_id = slugify_text(item.get("id") or "")
        item_name = slugify_text(item.get("name") or "")

        if item_id:
            item_lookup[item_id] = item
            alias_lookup[item_id] = item_id

        if item_name and item_id:
            alias_lookup[item_name] = item_id

    # Manual aliases for common real-world naming drift
    manual_aliases = {
        "coke_can": "coca_cola",
        "coca_cola": "coca_cola",
        "sweet_sour_chicken": "sweet_sour_chicken",
        "hot_sour_soup": "hot_sour_soup",
        "chicken_chow_mein": "chicken_chow_mein",
        "egg_fried_rice": "egg_fried_rice",
        "spring_rolls": "spring_rolls_2",
    }

    for raw_alias, canonical in manual_aliases.items():
        if canonical in item_lookup:
            alias_lookup[raw_alias] = canonical

    return item_lookup, alias_lookup


def resolve_item_to_menu(order_item_id: str, item_lookup: dict[str, dict], alias_lookup: dict[str, str]) -> tuple[str | None, str | None]:
    raw = slugify_text(order_item_id)

    if raw in alias_lookup:
        canonical = alias_lookup[raw]
        return canonical, item_lookup[canonical].get("name")

    possible_keys = list(alias_lookup.keys())
    close = get_close_matches(raw, possible_keys, n=1, cutoff=0.82)

    if close:
        canonical = alias_lookup[close[0]]
        return canonical, item_lookup[canonical].get("name")

    return None, None


def build_memory(menu_data, orders, item_stats, order_stats, pairings, time_patterns):
    sorted_items = sorted(
        item_stats["counts"].items(),
        key=lambda x: x[1],
        reverse=True
    )

    item_lookup, alias_lookup = build_menu_lookup(menu_data)

    matched_items = []
    unmatched_items = []

    seen = set()
    for item_id, count in sorted_items:
        canonical_id, menu_name = resolve_item_to_menu(item_id, item_lookup, alias_lookup)

        row = {
            "order_item_id": item_id,
            "count": count,
            "canonical_id": canonical_id,
            "menu_name": menu_name,
        }

        if canonical_id:
            if canonical_id not in seen:
                matched_items.append(row)
                seen.add(canonical_id)
        else:
            unmatched_items.append(row)

    top_items = [(row["order_item_id"], row["count"]) for row in matched_items[:3]]
    low_items = [(row["order_item_id"], row["count"]) for row in matched_items[-3:]] if matched_items else sorted_items[-3:]

    warnings = []
    if unmatched_items:
        sample = ", ".join(item["order_item_id"] for item in unmatched_items[:5])
        warnings.append(
            f"Some uploaded order items could not be matched to the current menu: {sample}."
        )

    total_items_sold = sum(item_stats["counts"].values())
    total_orders = order_stats["total_orders"] or 0
    avg_items_per_order = round(total_items_sold / total_orders, 2) if total_orders else 0.0

    return {
        "top_items": top_items,
        "low_items": low_items,
        "avg_order_value": order_stats["avg_order_value"],
        "avg_items_per_order": avg_items_per_order,
        "top_pairings": pairings,
        "busy_hours": time_patterns["busy_hours"],
        "quiet_hours": time_patterns["quiet_hours"],
        "item_stats": item_stats,
        "matched_items": matched_items,
        "unmatched_items": unmatched_items,
        "warnings": warnings,
    }