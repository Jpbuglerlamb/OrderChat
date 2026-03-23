#app/business_ai/analytics/time_patterns.py
from collections import defaultdict
from datetime import datetime

def compute_time_patterns(orders):
    hour_counts = defaultdict(int)

    for order in orders:
        dt = datetime.fromisoformat(order["created_at"])
        hour_counts[dt.hour] += 1

    sorted_hours = sorted(hour_counts.items(), key=lambda x: x[1], reverse=True)

    return {
        "busy_hours": sorted_hours[:3],
        "quiet_hours": sorted_hours[-3:]
    }