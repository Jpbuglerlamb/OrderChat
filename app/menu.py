# app/menu.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_menu() -> Dict[str, Any]:
    """
    Loads menu.json from:
      data/<MENU_KEY>/menu.json

    MENU_KEY defaults to "hybrid".
    """
    menu_key = os.getenv("MENU_KEY", "hybrid").strip()
    menu_path = DATA_DIR / menu_key / "menu.json"

    if not menu_path.exists():
        available = [p.name for p in DATA_DIR.iterdir() if p.is_dir()]
        raise FileNotFoundError(f"Menu '{menu_key}' not found.\nAvailable menus: {available}")

    return json.loads(menu_path.read_text(encoding="utf-8"))


def list_categories(menu: Dict[str, Any]) -> List[Dict[str, str]]:
    cats = menu.get("categories") or []
    out: List[Dict[str, str]] = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        name = str(c.get("name") or "").strip()
        if cid and name:
            out.append({"id": cid, "name": name})
    return out


def find_item(menu: Dict[str, Any], item_id: str) -> Optional[Dict[str, Any]]:
    """
    Supports BOTH schemas:
      New: menu["items"] = [...]
      Old: menu["categories"][...]["items"] = [...]
    """
    target = (item_id or "").strip()
    if not target:
        return None

    # New schema: top-level items
    items = menu.get("items") or []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and (it.get("id") or "").strip() == target:
                return it

    # Back-compat: nested items inside categories
    cats = menu.get("categories") or []
    if isinstance(cats, list):
        for c in cats:
            if not isinstance(c, dict):
                continue
            for it in (c.get("items") or []):
                if isinstance(it, dict) and (it.get("id") or "").strip() == target:
                    return it

    return None
