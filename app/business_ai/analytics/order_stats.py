# app/business_ai/analytics/order_stats.py
from __future__ import annotations

from collections import defaultdict
from datetime import datetime


def compute_order_stats(orders):
    total_orders = len(orders)
    total_value = 0.0
    total_items = 0
    single_item_orders = 0

    orders_by_day = defaultdict(int)

    for order in orders:
        order_total = float(order.get("total", 0) or 0)
        total_value += order_total

        items = order.get("items", []) or []
        item_count = sum(int(item.get("quantity", 0) or 0) for item in items)
        total_items += item_count

        if item_count <= 1:
            single_item_orders += 1

        created_at = str(order.get("created_at", "")).strip()
        try:
            dt = datetime.fromisoformat(created_at)
            day_name = dt.strftime("%A")
            orders_by_day[day_name] += 1
        except Exception:
            pass

    avg_order_value = total_value / total_orders if total_orders else 0
    avg_items_per_order = total_items / total_orders if total_orders else 0
    single_item_order_ratio = single_item_orders / total_orders if total_orders else 0

    sorted_days = sorted(orders_by_day.items(), key=lambda x: x[1], reverse=True)
    busiest_days = sorted_days[:3]
    quiet_days = sorted_days[-3:] if sorted_days else []

    return {
        "total_orders": total_orders,
        "total_revenue": round(total_value, 2),
        "avg_order_value": round(avg_order_value, 2),
        "avg_items_per_order": round(avg_items_per_order, 2),
        "single_item_order_ratio": round(single_item_order_ratio, 4),
        "single_item_order_percentage": round(single_item_order_ratio * 100, 1),
        "busiest_days": busiest_days,
        "quiet_days": quiet_days,
    }