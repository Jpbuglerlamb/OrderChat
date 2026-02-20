# app/ordering/menu.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .nlp import normalize_text, fuzzy_best_key, default_synonyms


def currency_symbol(menu: Dict[str, Any]) -> str:
    cur = ((menu.get("meta") or {}).get("currency") or "GBP").upper()
    return "£" if cur == "GBP" else ""


def menu_synonyms(menu: Dict[str, Any]) -> Dict[str, str]:
    meta = menu.get("meta") or {}
    custom = meta.get("synonyms") or {}
    merged = default_synonyms()
    if isinstance(custom, dict):
        merged.update({str(k).lower(): str(v).lower() for k, v in custom.items()})
    return merged


def build_menu_index(menu: Dict[str, Any], synonyms: Dict[str, str]) -> Dict[str, Any]:
    idx: Dict[str, Any] = {}
    items_by_id: Dict[str, Dict[str, Any]] = {}
    name_to_item_syn: Dict[str, Dict[str, Any]] = {}

    # categories (optional)
    categories = menu.get("categories") or []
    if isinstance(categories, list):
        for c in categories:
            if not isinstance(c, dict):
                continue
            # back-compat nested items
            for it in (c.get("items") or []):
                if isinstance(it, dict):
                    _index_item(it, items_by_id, name_to_item_syn, synonyms)

    # flat items
    items = menu.get("items") or []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                _index_item(it, items_by_id, name_to_item_syn, synonyms)

    idx["items_by_id"] = items_by_id
    idx["name_to_item_syn"] = name_to_item_syn
    menu["_index"] = idx
    return menu


def _index_item(
    it: Dict[str, Any],
    items_by_id: Dict[str, Dict[str, Any]],
    name_to_item_syn: Dict[str, Dict[str, Any]],
    synonyms: Dict[str, str],
) -> None:
    iid = str(it.get("id") or "").strip()
    nm = str(it.get("name") or "").strip()
    if iid:
        items_by_id[iid] = it
    if nm:
        key = normalize_text(nm, synonyms)
        if key:
            name_to_item_syn[key] = it


def all_category_names(menu: Dict[str, Any]) -> List[str]:
    cats = menu.get("categories") or []
    out: List[str] = []
    if isinstance(cats, list):
        for c in cats:
            if isinstance(c, dict):
                n = str(c.get("name") or "").strip()
                if n:
                    out.append(n)
    return out


def find_item(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[Dict[str, Any]]:
    idx = menu.get("_index") or {}
    lookup = idx.get("name_to_item_syn") or {}
    if not text:
        return None

    q = normalize_text(text, synonyms)
    if q in lookup:
        return lookup[q]

    keys = list(lookup.keys())
    best = fuzzy_best_key(keys, q, cutoff=0.78)
    return lookup.get(best) if best else None