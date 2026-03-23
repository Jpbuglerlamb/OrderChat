#app/business_ai/utils/pipeline.py
from app.business_ai.analytics.item_stats import compute_item_stats
from app.business_ai.analytics.order_stats import compute_order_stats
from app.business_ai.analytics.pairings import compute_pairings
from app.business_ai.analytics.time_patterns import compute_time_patterns
from app.business_ai.insights.formatter import format_insights
from app.business_ai.insights.rules import generate_insights
from app.business_ai.memory.builder import build_memory
from app.business_ai.data.normaliser import normalise_orders
from app.business_ai.data.validator import validate_orders


def run_pipeline(menu_data, orders):
    orders = normalise_orders(orders)
    errors = validate_orders(orders)

    if errors:
        return {
            "ok": False,
            "errors": errors,
            "insights": [],
            "formatted_insights": "Uploaded order history contains errors.",
        }

    item_stats = compute_item_stats(orders)
    order_stats = compute_order_stats(orders)
    pairings = compute_pairings(orders)
    time_patterns = compute_time_patterns(orders)

    memory = build_memory(
        menu_data=menu_data,
        orders=orders,
        item_stats=item_stats,
        order_stats=order_stats,
        pairings=pairings,
        time_patterns=time_patterns,
    )

    insights = generate_insights(memory)
    formatted = format_insights(insights)

    return {
        "ok": True,
        "menu_meta": menu_data.get("meta", {}),
        "memory": memory,
        "insights": insights,
        "formatted_insights": formatted,
    }