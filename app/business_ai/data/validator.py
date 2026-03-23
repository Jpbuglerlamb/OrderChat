#app/business_ai/data/validator.py
from datetime import datetime


def validate_orders(orders):
    errors = []

    if not isinstance(orders, list):
        return ["Orders payload must be a list."]

    if not orders:
        return ["No valid orders were found in the uploaded file."]

    for index, order in enumerate(orders, start=1):
        order_id = str(order.get("id", "")).strip()
        created_at = str(order.get("created_at", "")).strip()
        items = order.get("items", [])
        total = order.get("total", None)

        if not order_id:
            errors.append(f"Order #{index}: missing id")

        if not created_at:
            errors.append(f"Order #{index}: missing created_at")
        else:
            try:
                datetime.fromisoformat(created_at)
            except Exception:
                errors.append(f"Order #{index}: invalid created_at '{created_at}'")

        if not isinstance(items, list) or not items:
            errors.append(f"Order #{index}: items must be a non-empty list")
        else:
            for item_index, item in enumerate(items, start=1):
                item_id = str(item.get("id", "")).strip()
                quantity = item.get("quantity", None)
                price = item.get("price", None)

                if not item_id:
                    errors.append(f"Order #{index}, item #{item_index}: missing item id")

                try:
                    if int(quantity) <= 0:
                        errors.append(f"Order #{index}, item #{item_index}: quantity must be > 0")
                except Exception:
                    errors.append(f"Order #{index}, item #{item_index}: invalid quantity")

                try:
                    if float(price) < 0:
                        errors.append(f"Order #{index}, item #{item_index}: price must be >= 0")
                except Exception:
                    errors.append(f"Order #{index}, item #{item_index}: invalid price")

        try:
            float(total)
        except Exception:
            errors.append(f"Order #{index}: invalid total")

    return errors