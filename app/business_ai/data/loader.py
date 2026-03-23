import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

def load_menu():
    path = BASE_DIR / "sample_data" / "menu.json"
    with open(path) as f:
        return json.load(f)

def load_orders():
    path = BASE_DIR / "sample_data" / "orders.json"
    with open(path) as f:
        return json.load(f)