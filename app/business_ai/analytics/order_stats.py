def compute_order_stats(orders):
    total_value = 0
    total_orders = len(orders)

    for order in orders:
        total_value += order["total"]

    avg_order_value = total_value / total_orders if total_orders else 0

    return {
        "total_orders": total_orders,
        "avg_order_value": round(avg_order_value, 2),
    }