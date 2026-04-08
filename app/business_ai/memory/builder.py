from __future__ import annotations

from typing import Any

from app.business_ai.utils.item_ids import (
    build_menu_lookup,
    display_name_from_id,
    resolve_item_to_menu,
)


def build_ranked_item_rows(
    item_stats: dict[str, Any],
    menu_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    counts = item_stats.get("counts", {}) or {}
    revenue = item_stats.get("revenue", {}) or {}

    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)

    item_lookup, alias_lookup = build_menu_lookup(menu_data)

    matched_items: list[dict[str, Any]] = []
    unmatched_items: list[dict[str, Any]] = []
    seen_canonical_ids: set[str] = set()

    for order_item_id, count in sorted_items:
        resolution = resolve_item_to_menu(order_item_id, item_lookup, alias_lookup)

        canonical_id = resolution.get("canonical_id")
        row = {
            "order_item_id": order_item_id,
            "display_name": resolution.get("menu_name") or display_name_from_id(order_item_id),
            "count": int(count or 0),
            "revenue": round(float(revenue.get(order_item_id, 0) or 0), 2),
            "canonical_id": canonical_id,
            "menu_name": resolution.get("menu_name"),
            "matched": bool(resolution.get("matched")),
            "match_method": resolution.get("match_method", "unknown"),
        }

        if row["matched"] and canonical_id:
            if canonical_id not in seen_canonical_ids:
                matched_items.append(row)
                seen_canonical_ids.add(canonical_id)
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
    matched_items, unmatched_items = build_ranked_item_rows(
        item_stats=item_stats,
        menu_data=menu_data,
    )

    top_items, low_items = pick_top_and_low_items(matched_items, unmatched_items)

    total_items_sold = sum((item_stats.get("counts", {}) or {}).values())
    total_orders = int(order_stats.get("total_orders", 0) or 0)
    avg_order_value = round(float(order_stats.get("avg_order_value", 0) or 0), 2)
    avg_items_per_order = round(float(order_stats.get("avg_items_per_order", 0) or 0), 2)

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