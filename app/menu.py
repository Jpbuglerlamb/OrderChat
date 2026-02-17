import json
from pathlib import Path
import os

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def load_menu() -> dict:
    # Read menu key from .env
    menu_key = os.getenv("MENU_KEY", "hybrid").strip()

    menu_path = DATA_DIR / menu_key / "menu.json"

    if not menu_path.exists():
        available = [p.name for p in DATA_DIR.iterdir() if p.is_dir()]
        raise FileNotFoundError(
            f"Menu '{menu_key}' not found.\nAvailable menus: {available}"
        )

    return json.loads(menu_path.read_text(encoding="utf-8"))


def list_categories(menu: dict) -> list[dict]:
    return [{"id": c["id"], "name": c["name"]} for c in menu.get("categories", [])]

def find_item(menu: dict, item_id: str) -> dict | None:
    for cat in menu.get("categories", []):
        for item in cat.get("items", []):
            if item.get("id") == item_id:
                return item
    return None
