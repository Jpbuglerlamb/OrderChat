from app.business_ai.data.loader import load_menu, load_orders
from app.business_ai.analytics.item_stats import compute_item_stats
from app.business_ai.analytics.order_stats import compute_order_stats
from app.business_ai.analytics.pairings import compute_pairings
from app.business_ai.analytics.time_patterns import compute_time_patterns
from app.business_ai.memory.builder import build_memory
from app.business_ai.insights.rules import generate_insights


def run_pipeline():
    menu = load_menu()
    orders = load_orders()

    item_stats = compute_item_stats(orders)
    order_stats = compute_order_stats(orders)
    pairings = compute_pairings(orders)
    time_patterns = compute_time_patterns(orders)

    memory = build_memory(item_stats, order_stats, pairings, time_patterns)
    insights = generate_insights(memory)

    return {
        "menu_meta": menu.get("meta", {}),
        "insights": insights,
        "memory": memory,
    }