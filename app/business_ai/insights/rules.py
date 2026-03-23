from app.utils.helpers import simulate_price_increase

def generate_insights(memory):
    insights = []

    if memory["top_items"]:
        top = memory["top_items"][0][0]
        insights.append(
            f"🔥 '{top}' is driving your revenue. Test a +£0.50 price increase and monitor demand."
        )

    if memory["low_items"]:
        low = memory["low_items"][0][0]
        insights.append(
            f"⚠️ '{low}' is underperforming. Try a promotion or remove it to simplify the menu."
        )

    if memory["top_pairings"]:
        pair = memory["top_pairings"][0][0]
        insights.append(
            f"💰 Create a combo deal: '{pair[0]}' + '{pair[1]}' to increase average order value."
        )

    if memory["avg_order_value"] < 15:
        insights.append(
            "📈 Customers are spending relatively little. Introduce upsells like drinks or sides."
        )

    if memory["quiet_hours"]:
        hour = memory["quiet_hours"][0][0]
        insights.append(
            f"⏰ Low activity around {hour}:00. Consider promotions during this time."
        )

    if memory["top_items"]:
        top_item = memory["top_items"][0][0]

        sim = simulate_price_increase(top_item, memory["item_stats"])

        insights.append(
            f"💸 Increasing '{top_item}' by £0.50 could generate an extra £{sim['monthly_gain']} over this period."
        )

    return insights