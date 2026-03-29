#app/business_ai/analytics/pairings.py
from __future__ import annotations

from collections import defaultdict
from itertools import combinations


def compute_pairings(orders: list[dict]) -> list[tuple[tuple[str, str], int]]:
    pair_counts = defaultdict(int)

    for order in orders:
        unique_item_ids = sorted(
            {
                str(item.get("id", "")).strip()
                for item in order.get("items", []) or []
                if str(item.get("id", "")).strip()
            }
        )

        for pair in combinations(unique_item_ids, 2):
            pair_counts[pair] += 1

    sorted_pairs = sorted(pair_counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_pairs[:10]