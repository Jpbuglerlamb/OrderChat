#app/business_ai/analytics/item_stats.py
from __future__ import annotations

from collections import defaultdict


def compute_item_stats(orders: list[dict]) -> dict:
    counts = defaultdict(int)
    revenue = defaultdict(float)

    for order in orders:
        for item in order.get("items", []) or []:
            item_id = item.get("id")
            if not item_id:
                continue

            quantity = int(item.get("quantity", 0) or 0)
            price = float(item.get("price", 0) or 0)

            counts[item_id] += quantity
            revenue[item_id] += price * quantity

    top_by_count = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    top_by_revenue = sorted(revenue.items(), key=lambda x: x[1], reverse=True)

    return {
        "counts": dict(counts),
        "revenue": {k: round(v, 2) for k, v in revenue.items()},
        "top_by_count": top_by_count[:10],
        "top_by_revenue": [(k, round(v, 2)) for k, v in top_by_revenue[:10]],
        "low_by_count": top_by_count[-10:] if top_by_count else [],
    }