# app/ordering/menu_store.py
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Restaurant
from app.services.storage import get_json_file
from .menu import build_menu_index, menu_synonyms

_MENU_CACHE: Dict[str, Dict[str, Any]] = {}


def clear_menu_cache(slug: str | None = None) -> None:
    """
    Clear cached menu(s).
    Use clear_menu_cache(slug) after updating one restaurant menu,
    or clear_menu_cache() to wipe all cached menus.
    """
    global _MENU_CACHE

    if slug:
        _MENU_CACHE.pop((slug or "").strip().lower(), None)
    else:
        _MENU_CACHE.clear()


def _load_menu_from_storage(menu_json_path: str) -> Optional[Dict[str, Any]]:
    try:
        data = get_json_file(menu_json_path)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        print(f"[menu_store] Failed to load menu JSON from storage: {menu_json_path} ({e})")
        return None


def load_menu_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    slug = (slug or "").strip().lower()
    if not slug:
        return None

    # 1) cache hit
    if slug in _MENU_CACHE:
        return _MENU_CACHE[slug]

    db: Session = SessionLocal()
    try:
        restaurant = db.query(Restaurant).filter(Restaurant.slug == slug).first()
        if not restaurant:
            print(f"[menu_store] No restaurant found for slug: {slug}")
            return None

        menu_json_path = (restaurant.menu_json_path or "").strip()
        if not menu_json_path:
            print(f"[menu_store] Restaurant has no menu_json_path: {slug}")
            return None

        data = _load_menu_from_storage(menu_json_path)
        if not data:
            return None

        # Safety check: if meta.slug missing or wrong, force it to match the restaurant slug
        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}

        meta_slug = (meta.get("slug") or "").strip().lower()
        if meta_slug != slug:
            meta["slug"] = slug
            data["meta"] = meta

        syn = menu_synonyms(data)
        build_menu_index(data, syn)

        _MENU_CACHE[slug] = data
        return data

    except Exception as e:
        print(f"[menu_store] Failed to load menu for slug '{slug}': {e}")
        return None

    finally:
        db.close()