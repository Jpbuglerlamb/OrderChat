#app/business_ai/data/loader.py
from __future__ import annotations

import json
from pathlib import Path

from app.business_ai.data.order_history_ingest import ingest_order_history_file_to_dataset
from app.business_ai.data.normaliser import normalise_orders
from app.business_ai.data.validator import validate_orders

BASE_DIR = Path(__file__).resolve().parents[1]


def load_menu():
    path = BASE_DIR / "sample_data" / "menu.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_orders(filename: str = "orders.json"):
    path = BASE_DIR / "sample_data" / filename

    with open(path, "rb") as f:
        dataset = ingest_order_history_file_to_dataset(
            file_bytes=f.read(),
            filename=path.name,
        )

    orders = normalise_orders(dataset["orders"])
    errors = validate_orders(orders)

    if errors:
        raise ValueError("Invalid sample order data: " + " | ".join(errors))

    return orders