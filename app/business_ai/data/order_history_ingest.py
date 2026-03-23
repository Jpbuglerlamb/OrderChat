import json

def import_orders_from_json_file(path):
    with open(path) as f:
        data = json.load(f)

    if isinstance(data, dict) and "orders" in data:
        orders = data["orders"]
    elif isinstance(data, list):
        orders = data
    else:
        raise ValueError("Uploaded order history must be a list of orders or {'orders': [...]}")

    return orders