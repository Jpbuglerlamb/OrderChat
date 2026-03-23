from collections import defaultdict
from itertools import combinations

def compute_pairings(orders):
    pair_counts = defaultdict(int)

    for order in orders:
        item_ids = [item["id"] for item in order["items"]]

        # get all pairs in this order
        for pair in combinations(sorted(item_ids), 2):
            pair_counts[pair] += 1

    # sort by frequency
    sorted_pairs = sorted(pair_counts.items(), key=lambda x: x[1], reverse=True)

    return sorted_pairs[:5]  # top 5 pairings