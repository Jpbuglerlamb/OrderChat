# app/business_ai/pipeline.py
from __future__ import annotations

from app.business_ai.actions.formatter import format_recommendations
from app.business_ai.actions.recommendations import generate_recommendations
from app.business_ai.analytics.item_stats import compute_item_stats
from app.business_ai.analytics.order_stats import compute_order_stats
from app.business_ai.analytics.pairings import compute_pairings
from app.business_ai.analytics.time_patterns import compute_time_patterns
from app.business_ai.data.normaliser import normalise_orders
from app.business_ai.data.validator import validate_orders
from app.business_ai.insights.formatter import format_insights
from app.business_ai.insights.rules import generate_insights
from app.business_ai.memory.builder import build_memory


def _priority_rank(priority: str) -> int:
    ranking = {
        "high": 0,
        "medium": 1,
        "low": 2,
    }
    return ranking.get(priority, 99)


def _confidence_value(insight: dict) -> float:
    evidence = insight.get("evidence", {}) or {}
    try:
        return float(evidence.get("confidence", 0) or 0)
    except Exception:
        return 0.0


def _sort_insights(insights: list[dict]) -> list[dict]:
    return sorted(
        insights,
        key=lambda x: (
            _priority_rank(str(x.get("priority", ""))),
            -_confidence_value(x),
            str(x.get("title", "")),
        ),
    )


def run_pipeline(menu_data: dict, orders: list[dict]) -> dict:
    normalised_orders, unmatched_items = normalise_orders(orders, menu_data=menu_data)
    errors = validate_orders(normalised_orders)

    if errors:
        return {
            "ok": False,
            "errors": errors,
            "insights": [],
            "formatted_insights": "Uploaded order history contains errors.",
            "recommendations": [],
            "formatted_recommendations": "No recommendations available.",
            "unmatched_items": unmatched_items,
            "order_count": len(normalised_orders),
        }

    if not normalised_orders:
        insights = [
            {
                "type": "empty_dataset",
                "priority": "low",
                "title": "No order history analysed yet",
                "summary": "No valid order history has been analysed yet.",
                "action": "Upload past orders to unlock menu and sales insights.",
                "evidence": {"confidence": 1.0},
            }
        ]
        recommendations = generate_recommendations(insights, limit=3)

        return {
            "ok": True,
            "menu_meta": menu_data.get("meta", {}),
            "memory": {},
            "analytics": {
                "item_stats": {},
                "order_stats": {},
                "pairings": [],
                "time_patterns": {},
            },
            "insights": insights,
            "formatted_insights": format_insights(insights),
            "recommendations": recommendations,
            "formatted_recommendations": format_recommendations(recommendations),
            "unmatched_items": unmatched_items,
            "order_count": 0,
        }

    item_stats = compute_item_stats(normalised_orders)
    order_stats = compute_order_stats(normalised_orders)
    pairings = compute_pairings(normalised_orders)
    time_patterns = compute_time_patterns(normalised_orders)

    memory = build_memory(
        menu_data=menu_data,
        orders=normalised_orders,
        item_stats=item_stats,
        order_stats=order_stats,
        pairings=pairings,
        time_patterns=time_patterns,
    )

    if unmatched_items:
        sample = ", ".join(sorted(set(unmatched_items))[:10])
        warning = (
            "Some uploaded order items could not be matched to the current menu: "
            + sample
            + ("." if len(set(unmatched_items)) <= 10 else "...")
        )

        existing_warnings = memory.get("warnings", []) or []
        if warning not in existing_warnings:
            existing_warnings.append(warning)
            memory["warnings"] = existing_warnings

    insights = _sort_insights(generate_insights(memory))
    recommendations = generate_recommendations(insights, limit=3)

    return {
        "ok": True,
        "menu_meta": menu_data.get("meta", {}),
        "memory": memory,
        "analytics": {
            "item_stats": item_stats,
            "order_stats": order_stats,
            "pairings": pairings,
            "time_patterns": time_patterns,
        },
        "insights": insights,
        "formatted_insights": format_insights(insights),
        "recommendations": recommendations,
        "formatted_recommendations": format_recommendations(recommendations),
        "unmatched_items": unmatched_items,
        "order_count": len(normalised_orders),
    }