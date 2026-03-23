#app/business_ai/analytics/item_stats.py
from collections import defaultdict

def compute_item_stats(orders):
    counts = defaultdict(int)
    revenue = defaultdict(float)

    for order in orders:
        for item in order["items"]:
            item_id = item["id"]
            counts[item_id] += item["quantity"]
            revenue[item_id] += item["price"] * item["quantity"]

    return {
        "counts": dict(counts),
        "revenue": dict(revenue),
    }