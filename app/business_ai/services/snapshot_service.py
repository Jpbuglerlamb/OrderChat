from __future__ import annotations

import json
from datetime import datetime
from sqlalchemy.orm import Session

from app.models import Restaurant
from app.services.storage import get_json_file
from app.business_ai.pipeline import run_pipeline
from app.services.order_analytics_service import get_saved_orders_for_restaurant


def recompute_and_store_optimiser_snapshot(
    db: Session,
    restaurant: Restaurant,
) -> dict:
    if not restaurant.menu_json_path:
        result = {
            "ok": False,
            "error": "No menu connected",
            "insights": [],
            "formatted_insights": "",
            "order_count": 0,
            "unmatched_items": [],
        }
    else:
        try:
            menu_data = get_json_file(restaurant.menu_json_path)
            saved_orders = get_saved_orders_for_restaurant(db, restaurant)
            result = run_pipeline(menu_data, saved_orders)
        except Exception as exc:
            result = {
                "ok": False,
                "error": f"Snapshot recompute failed: {str(exc)}",
                "insights": [],
                "formatted_insights": "",
                "order_count": 0,
                "unmatched_items": [],
            }

    restaurant.optimiser_snapshot_json = json.dumps(result)
    restaurant.optimiser_last_updated = datetime.utcnow()

    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)

    return result

def get_saved_optimiser_snapshot(restaurant: Restaurant) -> dict:
    if not restaurant.optimiser_snapshot_json:
        return {
            "ok": False,
            "error": "No optimiser snapshot available yet",
            "insights": [],
            "formatted_insights": "",
            "order_count": 0,
            "unmatched_items": [],
        }

    try:
        return json.loads(restaurant.optimiser_snapshot_json)
    except Exception:
        return {
            "ok": False,
            "error": "Saved optimiser snapshot is invalid",
            "insights": [],
            "formatted_insights": "",
            "order_count": 0,
            "unmatched_items": [],
        }