# app/ordering/menu_store.py
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Restaurant
from app.services.storage import get_json_file
from .menu import build_menu_index, menu_synonyms

_MENU_CACHE: Dict[str, Dict[str, Any]] = {}


def _normalize_slug(slug: str | None) -> str:
    return str(slug or "").strip().lower()


def clear_menu_cache(slug: str | None = None) -> None:
    """
    Clear cached menu(s).

    Use:
      clear_menu_cache(slug)  -> clear one restaurant menu
      clear_menu_cache()      -> clear all cached menus
    """
    normalized = _normalize_slug(slug)

    if normalized:
        _MENU_CACHE.pop(normalized, None)
    else:
        _MENU_CACHE.clear()


def _load_menu_from_storage(menu_json_path: str) -> Optional[Dict[str, Any]]:
    path = str(menu_json_path or "").strip()
    if not path:
        return None

    try:
        data = get_json_file(path)
        if not isinstance(data, dict):
            print(f"[menu_store] Storage returned non-dict menu payload: {path}")
            return None
        return data
    except Exception as exc:
        print(f"[menu_store] Failed to load menu JSON from storage: {path} ({exc})")
        return None


def _get_restaurant_by_slug(db: Session, slug: str) -> Optional[Restaurant]:
    if not slug:
        return None
    return db.query(Restaurant).filter(Restaurant.slug == slug).first()


def _prepare_menu_data(data: Dict[str, Any], slug: str) -> Optional[Dict[str, Any]]:
    if not isinstance(data, dict):
        return None

    # Ensure meta exists and always matches the restaurant slug
    meta = data.get("meta")
    if not isinstance(meta, dict):
        meta = {}

    meta["slug"] = slug
    data["meta"] = meta

    try:
        synonyms = menu_synonyms(data)
        build_menu_index(data, synonyms)
    except Exception as exc:
        print(f"[menu_store] Failed to build menu index for slug '{slug}': {exc}")
        return None

    return data


def load_menu_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    normalized_slug = _normalize_slug(slug)
    if not normalized_slug:
        return None

    # 1) Cache hit
    cached = _MENU_CACHE.get(normalized_slug)
    if isinstance(cached, dict):
        return cached

    db: Session = SessionLocal()
    try:
        restaurant = _get_restaurant_by_slug(db, normalized_slug)
        if not restaurant:
            print(f"[menu_store] No restaurant found for slug: {normalized_slug}")
            return None

        menu_json_path = str(restaurant.menu_json_path or "").strip()
        if not menu_json_path:
            print(f"[menu_store] Restaurant has no menu_json_path: {normalized_slug}")
            return None

        raw_menu = _load_menu_from_storage(menu_json_path)
        if not raw_menu:
            return None

        prepared = _prepare_menu_data(raw_menu, normalized_slug)
        if not prepared:
            return None

        _MENU_CACHE[normalized_slug] = prepared
        return prepared

    except Exception as exc:
        print(f"[menu_store] Failed to load menu for slug '{normalized_slug}': {exc}")
        return None

    finally:
        db.close()