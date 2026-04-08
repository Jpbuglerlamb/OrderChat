# app/business_ai/insights/rules.py
from __future__ import annotations

from app.business_ai.insights.types import Insight
from app.business_ai.utils.helpers import simulate_price_increase


def _safe_confidence(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return round(value, 2)


def generate_insights(memory: dict) -> list[Insight]:
    insights: list[Insight] = []

    top_items = memory.get("top_items", []) or []
    low_items = memory.get("low_items", []) or []
    top_pairings = memory.get("top_pairings", []) or []
    quiet_hours = memory.get("quiet_hours", []) or []
    warnings = memory.get("warnings", []) or []

    summary = memory.get("summary", {}) or {}
    avg_order_value = float(summary.get("avg_order_value", 0) or 0)
    avg_items_per_order = float(summary.get("avg_items_per_order", 0) or 0)
    item_stats = memory.get("item_stats", {}) or {}

    if top_items:
        top_item_id, top_count = top_items[0]
        insights.append(
            {
                "type": "top_seller",
                "priority": "high",
                "title": "Top seller identified",
                "summary": f"'{top_item_id}' is one of your strongest sellers with {top_count} units sold.",
                "action": "Consider a careful price test or feature it more prominently in the ordering flow.",
                "evidence": {
                    "item_id": top_item_id,
                    "units_sold": top_count,
                    "confidence": _safe_confidence(0.86),
                },
            }
        )

    if low_items:
        low_item_id, low_count = low_items[0]
        insights.append(
            {
                "type": "underperformer",
                "priority": "medium",
                "title": "Underperforming item detected",
                "summary": f"'{low_item_id}' is one of your weakest matched items with only {low_count} units sold.",
                "action": "Try repositioning it, bundling it, renaming it, or removing it to simplify the menu.",
                "evidence": {
                    "item_id": low_item_id,
                    "units_sold": low_count,
                    "confidence": _safe_confidence(0.74),
                },
            }
        )

    if top_pairings:
        pair, pair_count = top_pairings[0]
        item_a, item_b = pair
        insights.append(
            {
                "type": "pairing_opportunity",
                "priority": "high",
                "title": "Strong pairing opportunity",
                "summary": f"Customers often buy '{item_a}' with '{item_b}' together ({pair_count} orders).",
                "action": "Create a combo, meal deal, or checkout prompt around this pairing.",
                "evidence": {
                    "pair": [item_a, item_b],
                    "pair_count": pair_count,
                    "confidence": _safe_confidence(0.83),
                },
            }
        )

    if avg_order_value < 15:
        insights.append(
            {
                "type": "basket_growth",
                "priority": "high",
                "title": "Average order value has room to grow",
                "summary": f"Average order value is currently £{avg_order_value:.2f}.",
                "action": "Push add-ons, meal deals, and suggested extras before checkout to lift basket size.",
                "evidence": {
                    "avg_order_value": round(avg_order_value, 2),
                    "threshold": 15.0,
                    "confidence": _safe_confidence(0.80),
                },
            }
        )

    if avg_items_per_order < 2.5:
        insights.append(
            {
                "type": "upsell_opportunity",
                "priority": "medium",
                "title": "Low items per order",
                "summary": f"Customers buy {avg_items_per_order:.2f} items per order on average.",
                "action": "Prompt drinks, sides, desserts, or sauces before checkout.",
                "evidence": {
                    "avg_items_per_order": round(avg_items_per_order, 2),
                    "threshold": 2.5,
                    "confidence": _safe_confidence(0.78),
                },
            }
        )

    if quiet_hours:
        quiet_hour, quiet_count = quiet_hours[0]
        insights.append(
            {
                "type": "quiet_period",
                "priority": "medium",
                "title": "Quiet trading window found",
                "summary": f"Order activity is low around {quiet_hour:02d}:00 with {quiet_count} recorded orders.",
                "action": "Test a timed offer or promotion during this quieter period.",
                "evidence": {
                    "hour": quiet_hour,
                    "orders": quiet_count,
                    "confidence": _safe_confidence(0.72),
                },
            }
        )

    if top_items:
        top_item_id, _ = top_items[0]
        sim = simulate_price_increase(top_item_id, item_stats)

        if sim.get("old_revenue", 0) > 0:
            insights.append(
                {
                    "type": "price_test",
                    "priority": "medium",
                    "title": "Potential pricing opportunity",
                    "summary": (
                        f"A £0.50 increase on '{top_item_id}' could produce an estimated "
                        f"extra £{sim['monthly_gain']:.2f} over this period."
                    ),
                    "action": "Run a short pricing test and monitor order volume before making it permanent.",
                    "evidence": {
                        "item_id": top_item_id,
                        "old_revenue": sim["old_revenue"],
                        "new_revenue": sim["new_revenue"],
                        "estimated_gain": sim["monthly_gain"],
                        "confidence": _safe_confidence(0.64),
                    },
                }
            )

    for warning in warnings:
        insights.append(
            {
                "type": "warning",
                "priority": "low",
                "title": "Data quality warning",
                "summary": str(warning),
                "action": "Review menu matching and uploaded order naming to improve optimiser accuracy.",
                "evidence": {
                    "confidence": _safe_confidence(0.95),
                },
            }
        )

    return insights