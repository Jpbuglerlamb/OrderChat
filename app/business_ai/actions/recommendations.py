# app/business_ai/actions/recommendations.py
from __future__ import annotations

from typing import Any, Literal, TypedDict


RecommendationPriority = Literal["high", "medium", "low"]


class Recommendation(TypedDict):
    type: str
    priority: RecommendationPriority
    title: str
    summary: str
    action: str
    reason: str
    source_insight_type: str
    evidence: dict[str, Any]


def _safe_priority(value: str) -> RecommendationPriority:
    if value == "high":
        return "high"
    if value == "medium":
        return "medium"
    return "low"


def _priority_rank(priority: str) -> int:
    ranks = {
        "high": 0,
        "medium": 1,
        "low": 2,
    }
    return ranks.get(priority, 99)


def _confidence_of(insight: dict[str, Any]) -> float:
    evidence = insight.get("evidence", {}) or {}
    try:
        return float(evidence.get("confidence", 0))
    except Exception:
        return 0.0


def _recommendation_from_insight(insight: dict[str, Any]) -> Recommendation | None:
    insight_type = str(insight.get("type", "")).strip()
    priority = _safe_priority(str(insight.get("priority", "low")).strip())
    title = str(insight.get("title", "")).strip()
    summary = str(insight.get("summary", "")).strip()
    action = str(insight.get("action", "")).strip()
    evidence = insight.get("evidence", {}) or {}

    if not insight_type:
        return None

    if insight_type == "top_seller":
        item_id = str(evidence.get("item_id", "")).strip()
        units_sold = evidence.get("units_sold", 0)

        return {
            "type": "price_or_feature_top_seller",
            "priority": "high",
            "title": f"Use your top seller more aggressively",
            "summary": f"'{item_id}' is already performing strongly.",
            "action": f"Feature '{item_id}' more prominently or test a small price increase.",
            "reason": f"This item sold {units_sold} units and is one of your strongest performers.",
            "source_insight_type": insight_type,
            "evidence": evidence,
        }

    if insight_type == "underperformer":
        item_id = str(evidence.get("item_id", "")).strip()
        units_sold = evidence.get("units_sold", 0)

        return {
            "type": "fix_or_remove_underperformer",
            "priority": priority,
            "title": "Fix or simplify a weak item",
            "summary": f"'{item_id}' is lagging behind other matched items.",
            "action": f"Test promoting, renaming, bundling, or removing '{item_id}'.",
            "reason": f"This item has sold only {units_sold} units in the analysed period.",
            "source_insight_type": insight_type,
            "evidence": evidence,
        }

    if insight_type == "pairing_opportunity":
        pair = evidence.get("pair", [])
        pair_count = evidence.get("pair_count", 0)

        if isinstance(pair, list) and len(pair) >= 2:
            item_a, item_b = str(pair[0]), str(pair[1])
        else:
            item_a, item_b = "Item A", "Item B"

        return {
            "type": "create_combo",
            "priority": "high",
            "title": "Create or push a proven combo",
            "summary": f"Customers already buy '{item_a}' with '{item_b}' together.",
            "action": f"Create a combo, meal deal, or checkout upsell for '{item_a}' + '{item_b}'.",
            "reason": f"This pairing appeared together in {pair_count} orders.",
            "source_insight_type": insight_type,
            "evidence": evidence,
        }

    if insight_type == "basket_growth":
        avg_order_value = evidence.get("avg_order_value", 0)

        return {
            "type": "grow_basket_value",
            "priority": "high",
            "title": "Increase average basket value",
            "summary": f"Average order value is currently £{avg_order_value:.2f}.",
            "action": "Add stronger add-ons, meal deals, and pre-checkout upsell prompts.",
            "reason": "Basket value is below the current target threshold.",
            "source_insight_type": insight_type,
            "evidence": evidence,
        }

    if insight_type == "upsell_opportunity":
        avg_items_per_order = evidence.get("avg_items_per_order", 0)

        return {
            "type": "push_add_ons",
            "priority": priority,
            "title": "Push extra items before checkout",
            "summary": f"Customers are buying only {avg_items_per_order:.2f} items per order on average.",
            "action": "Prompt sauces, drinks, sides, or desserts before checkout.",
            "reason": "Low item count per basket usually means missed upsell opportunities.",
            "source_insight_type": insight_type,
            "evidence": evidence,
        }

    if insight_type == "quiet_period":
        hour = evidence.get("hour", 0)
        orders = evidence.get("orders", 0)

        return {
            "type": "target_quiet_window",
            "priority": priority,
            "title": "Target a quiet trading window",
            "summary": f"Orders are weak around {int(hour):02d}:00.",
            "action": f"Run a timed promotion or featured item push around {int(hour):02d}:00.",
            "reason": f"Only {orders} orders were recorded for that hour in the analysed data.",
            "source_insight_type": insight_type,
            "evidence": evidence,
        }

    if insight_type == "price_test":
        item_id = str(evidence.get("item_id", "")).strip()
        estimated_gain = evidence.get("estimated_gain", 0)

        return {
            "type": "run_price_test",
            "priority": priority,
            "title": "Run a short price test",
            "summary": f"'{item_id}' may support a modest price increase.",
            "action": f"Test a £0.50 increase on '{item_id}' and watch order volume closely.",
            "reason": f"The current pricing simulation estimates an extra £{estimated_gain:.2f}.",
            "source_insight_type": insight_type,
            "evidence": evidence,
        }

    if insight_type == "warning":
        return {
            "type": "clean_data_quality",
            "priority": "low",
            "title": "Clean up data quality",
            "summary": title or "Some order items are not mapping cleanly to the menu.",
            "action": "Review menu aliases and uploaded item naming for cleaner optimiser results.",
            "reason": summary or "Data quality issues reduce recommendation accuracy.",
            "source_insight_type": insight_type,
            "evidence": evidence,
        }

    return {
        "type": f"follow_up_{insight_type}",
        "priority": priority,
        "title": title or "Follow-up action",
        "summary": summary or "A follow-up action is available.",
        "action": action or "Review this insight and decide the next operational step.",
        "reason": "This recommendation was generated from an existing structured insight.",
        "source_insight_type": insight_type,
        "evidence": evidence,
    }


def _dedupe_recommendations(
    recommendations: list[Recommendation],
) -> list[Recommendation]:
    seen: set[tuple[str, str]] = set()
    deduped: list[Recommendation] = []

    for rec in recommendations:
        key = (
            str(rec.get("type", "")).strip(),
            str(rec.get("action", "")).strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rec)

    return deduped


def _sort_recommendations(
    recommendations: list[Recommendation],
) -> list[Recommendation]:
    return sorted(
        recommendations,
        key=lambda rec: (
            _priority_rank(str(rec.get("priority", ""))),
            -float((rec.get("evidence", {}) or {}).get("confidence", 0) or 0),
            str(rec.get("title", "")),
        ),
    )


def generate_recommendations(
    insights: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[Recommendation]:
    recommendations: list[Recommendation] = []

    for insight in insights:
        rec = _recommendation_from_insight(insight)
        if rec is not None:
            recommendations.append(rec)

    recommendations = _dedupe_recommendations(recommendations)
    recommendations = _sort_recommendations(recommendations)

    if limit > 0:
        recommendations = recommendations[:limit]

    return recommendations