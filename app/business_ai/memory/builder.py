def build_memory(item_stats, order_stats, pairings, time_patterns):
    sorted_items = sorted(
        item_stats["counts"].items(),
        key=lambda x: x[1],
        reverse=True
    )

    return {
        "top_items": sorted_items[:3],
        "low_items": sorted_items[-3:],
        "avg_order_value": order_stats["avg_order_value"],
        "top_pairings": pairings,
        "busy_hours": time_patterns["busy_hours"],
        "quiet_hours": time_patterns["quiet_hours"],
        "item_stats": item_stats
    }