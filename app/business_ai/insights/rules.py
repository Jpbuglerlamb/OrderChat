from app.business_ai.utils.helpers import simulate_price_increase


def generate_insights(memory):
    insights = []

    if memory["top_items"]:
        top = memory["top_items"][0][0]
        insights.append(
            f"🔥 '{top}' is one of your strongest sellers. Test a +£0.50 price increase carefully and monitor demand."
        )

    if memory["low_items"]:
        low = memory["low_items"][0][0]
        insights.append(
            f"⚠️ '{low}' is underperforming. Try a promotion, reposition it, or remove it to simplify the menu."
        )

    if memory["top_pairings"]:
        pair = memory["top_pairings"][0][0]
        insights.append(
            f"💰 Customers often buy '{pair[0]}' with '{pair[1]}'. Consider a combo or meal deal around that pairing."
        )

    if memory["avg_order_value"] < 15:
        insights.append(
            f"📈 Average order value is £{memory['avg_order_value']:.2f}, which suggests there is room for stronger upsells, meal deals, or add-on prompts."
        )

    if memory.get("avg_items_per_order", 0) < 2.5:
        insights.append(
            f"🥡 Customers buy only {memory['avg_items_per_order']:.2f} items per order on average. Push drinks, sides, or dessert add-ons before checkout."
        )

    if memory["quiet_hours"]:
        hour = memory["quiet_hours"][0][0]
        insights.append(
            f"⏰ Low activity around {hour}:00. Consider promotions or timed offers during that quieter window."
        )

    if memory["top_items"]:
        top_item = memory["top_items"][0][0]
        sim = simulate_price_increase(top_item, memory["item_stats"])

        if sim["old_revenue"] > 0:
            insights.append(
                f"💸 Increasing '{top_item}' by £0.50 could generate an estimated extra £{sim['monthly_gain']} over this period."
            )

    for warning in memory.get("warnings", []):
        insights.append(f"🧩 {warning}")

    return insights