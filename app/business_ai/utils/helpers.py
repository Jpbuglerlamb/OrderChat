def simulate_price_increase(item_id, item_stats, increase=0.5):
    current_count = item_stats["counts"].get(item_id, 0)
    current_revenue = item_stats["revenue"].get(item_id, 0)

    if current_count == 0:
        return {
            "old_revenue": 0,
            "new_revenue": 0,
            "monthly_gain": 0
        }

    new_count = int(current_count * 0.95)
    new_revenue = new_count * ((current_revenue / current_count) + increase)

    monthly_gain = new_revenue - current_revenue

    return {
        "old_revenue": round(current_revenue, 2),
        "new_revenue": round(new_revenue, 2),
        "monthly_gain": round(monthly_gain, 2)
    }