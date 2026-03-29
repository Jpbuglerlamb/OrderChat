# app/business_ai/memory/builder.py
from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Any


def slugify_text(value: str) -> str:
    value = str(value or "").strip().lower()
    value = value.replace("&", "and")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def display_name_from_id(value: str) -> str:
    text = str(value or "").strip().replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.title() if text else ""


def build_menu_lookup(menu_data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    items = menu_data.get("items") or []

    item_lookup: dict[str, dict[str, Any]] = {}
    alias_lookup: dict[str, str] = {}

    for item in items:
        raw_item_id = str(item.get("id") or "").strip()
        raw_item_name = str(item.get("name") or "").strip()

        canonical_item_id = slugify_text(raw_item_id)
        canonical_item_name = slugify_text(raw_item_name)

        if canonical_item_id:
            item_lookup[canonical_item_id] = item
            alias_lookup[canonical_item_id] = canonical_item_id
            alias_lookup[canonical_item_id.replace("_", "")] = canonical_item_id

        if canonical_item_name and canonical_item_id:
            alias_lookup[canonical_item_name] = canonical_item_id
            alias_lookup[canonical_item_name.replace("_", "")] = canonical_item_id

    manual_aliases = {
        "coke_can": "coca_cola",
        "coke": "coca_cola",
        "coca_cola": "coca_cola",
        "sweet_sour_chicken": "sweet_sour_chicken",
        "sweet_and_sour_chicken": "sweet_sour_chicken",
        "hot_sour_soup": "hot_sour_soup",
        "hot_and_sour_soup": "hot_sour_soup",
        "chicken_chow_mein": "chicken_chow_mein",
        "egg_fried_rice": "egg_fried_rice",
        "spring_rolls": "spring_rolls_2",
    }

    for raw_alias, canonical in manual_aliases.items():
        canonical_slug = slugify_text(canonical)
        if canonical_slug in item_lookup:
            alias_lookup[slugify_text(raw_alias)] = canonical_slug

    return item_lookup, alias_lookup


def resolve_item_to_menu(
    order_item_id: str,
    item_lookup: dict[str, dict[str, Any]],
    alias_lookup: dict[str, str],
) -> dict[str, Any]:
    raw_item_id = str(order_item_id or "").strip()
    raw_slug = slugify_text(raw_item_id)

    if not raw_slug:
        return {
            "matched": False,
            "canonical_id": None,
            "menu_name": None,
            "raw_item_id": raw_item_id,
            "match_method": "empty",
        }

    if raw_slug in alias_lookup:
        canonical = alias_lookup[raw_slug]
        menu_item = item_lookup.get(canonical, {})
        return {
            "matched": True,
            "canonical_id": canonical,
            "menu_name": menu_item.get("name") or display_name_from_id(canonical),
            "raw_item_id": raw_item_id,
            "match_method": "exact",
        }

    compact_slug = raw_slug.replace("_", "")
    if compact_slug in alias_lookup:
        canonical = alias_lookup[compact_slug]
        menu_item = item_lookup.get(canonical, {})
        return {
            "matched": True,
            "canonical_id": canonical,
            "menu_name": menu_item.get("name") or display_name_from_id(canonical),
            "raw_item_id": raw_item_id,
            "match_method": "compact",
        }

    possible_keys = list(alias_lookup.keys())
    close = get_close_matches(raw_slug, possible_keys, n=1, cutoff=0.82)

    if close:
        canonical = alias_lookup[close[0]]
        menu_item = item_lookup.get(canonical, {})
        return {
            "matched": True,
            "canonical_id": canonical,
            "menu_name": menu_item.get("name") or display_name_from_id(canonical),
            "raw_item_id": raw_item_id,
            "match_method": "fuzzy",
        }

    return {
        "matched": False,
        "canonical_id": None,
        "menu_name": None,
        "raw_item_id": raw_item_id,
        "match_method": "unmatched",
    }


def build_ranked_item_rows(
    item_stats: dict[str, Any],
    item_lookup: dict[str, dict[str, Any]],
    alias_lookup: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    counts = item_stats.get("counts", {}) or {}
    revenue = item_stats.get("revenue", {}) or {}

    sorted_items = sorted(
        counts.items(),
        key=lambda x: x[1],
        reverse=True,
    )

    matched_items: list[dict[str, Any]] = []
    unmatched_items: list[dict[str, Any]] = []
    seen_canonical_ids: set[str] = set()

    for order_item_id, count in sorted_items:
        resolution = resolve_item_to_menu(order_item_id, item_lookup, alias_lookup)

        row = {
            "order_item_id": order_item_id,
            "display_name": resolution["menu_name"] or display_name_from_id(order_item_id),
            "count": int(count or 0),
            "revenue": round(float(revenue.get(order_item_id, 0) or 0), 2),
            "canonical_id": resolution["canonical_id"],
            "menu_name": resolution["menu_name"],
            "matched": bool(resolution["matched"]),
            "match_method": resolution["match_method"],
        }

        if row["matched"] and row["canonical_id"]:
            if row["canonical_id"] not in seen_canonical_ids:
                matched_items.append(row)
                seen_canonical_ids.add(row["canonical_id"])
        else:
            unmatched_items.append(row)

    return matched_items, unmatched_items


def pick_top_and_low_items(
    matched_items: list[dict[str, Any]],
    unmatched_items: list[dict[str, Any]],
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    source = matched_items if matched_items else unmatched_items

    top_items = [
        (row["canonical_id"] or row["order_item_id"], row["count"])
        for row in source[:3]
    ]

    low_items = [
        (row["canonical_id"] or row["order_item_id"], row["count"])
        for row in source[-3:]
    ] if source else []

    return top_items, low_items


def build_memory(
    menu_data: dict[str, Any],
    orders: list[dict[str, Any]],
    item_stats: dict[str, Any],
    order_stats: dict[str, Any],
    pairings: list,
    time_patterns: dict[str, Any],
) -> dict[str, Any]:
    item_lookup, alias_lookup = build_menu_lookup(menu_data)

    matched_items, unmatched_items = build_ranked_item_rows(
        item_stats=item_stats,
        item_lookup=item_lookup,
        alias_lookup=alias_lookup,
    )

    top_items, low_items = pick_top_and_low_items(matched_items, unmatched_items)

    total_items_sold = sum((item_stats.get("counts", {}) or {}).values())
    total_orders = int(order_stats.get("total_orders", 0) or 0)
    avg_order_value = round(float(order_stats.get("avg_order_value", 0) or 0), 2)
    avg_items_per_order = round(total_items_sold / total_orders, 2) if total_orders else 0.0

    warnings: list[str] = []
    if unmatched_items:
        sample = ", ".join(item["order_item_id"] for item in unmatched_items[:5])
        warnings.append(
            f"Some uploaded order items could not be matched to the current menu: {sample}."
        )

    return {
        "summary": {
            "order_count": total_orders,
            "total_items_sold": total_items_sold,
            "avg_order_value": avg_order_value,
            "avg_items_per_order": avg_items_per_order,
        },
        "top_items": top_items,
        "low_items": low_items,
        "top_pairings": pairings,
        "busy_hours": time_patterns.get("busy_hours", []),
        "quiet_hours": time_patterns.get("quiet_hours", []),
        "hour_counts": time_patterns.get("hour_counts", {}),
        "item_stats": item_stats,
        "matched_items": matched_items,
        "unmatched_items": unmatched_items,
        "warnings": warnings,
    }