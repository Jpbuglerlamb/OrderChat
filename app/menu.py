# app/menu.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _sanitize_menu_key(raw: str) -> str:
    k = (raw or "").strip().replace("\\", "/")
    k = k.split("/")[-1].strip()
    return k or "hybrid"


def load_menu() -> dict[str, Any]:
    menu_key = _sanitize_menu_key(os.getenv("MENU_KEY", "hybrid"))
    menu_path = DATA_DIR / menu_key / "menu.json"

    if not menu_path.exists():
        available = sorted([p.name for p in DATA_DIR.iterdir() if p.is_dir()])
        raise FileNotFoundError(
            f"Menu '{menu_key}' not found.\nAvailable menus: {available}"
        )

    try:
        return json.loads(menu_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {menu_path}: {e}") from e


def list_categories(menu: dict[str, Any]) -> list[dict[str, str]]:
    cats = menu.get("categories") or []
    out: list[dict[str, str]] = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        name = str(c.get("name") or "").strip()
        if cid and name:
            out.append({"id": cid, "name": name})
    return out


def find_item(menu: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    """Supports both schemas:
    - New: menu["items"] top-level
    - Back-compat: nested categories[*]["items"]
    """
    iid = (item_id or "").strip()
    if not iid:
        return None

    # New schema
    for it in (menu.get("items") or []):
        if isinstance(it, dict) and it.get("id") == iid:
            return it

    # Back-compat
    for cat in (menu.get("categories") or []):
        if not isinstance(cat, dict):
            continue
        for it in (cat.get("items") or []):
            if isinstance(it, dict) and it.get("id") == iid:
                return it

    return None
