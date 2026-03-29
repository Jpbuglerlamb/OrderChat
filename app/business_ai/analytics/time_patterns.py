#app/business_ai/analytics/time_patterns.py
from __future__ import annotations

from collections import defaultdict
from datetime import datetime


def compute_time_patterns(orders: list[dict]) -> dict:
    hour_counts = defaultdict(int)

    for order in orders:
        created_at = str(order.get("created_at", "")).strip()
        try:
            dt = datetime.fromisoformat(created_at)
        except Exception:
            continue

        hour_counts[dt.hour] += 1

    full_hours = {hour: hour_counts.get(hour, 0) for hour in range(24)}
    sorted_hours = sorted(full_hours.items(), key=lambda x: x[1], reverse=True)

    return {
        "hour_counts": full_hours,
        "busy_hours": sorted_hours[:3],
        "quiet_hours": sorted(full_hours.items(), key=lambda x: x[1])[:3],
    }