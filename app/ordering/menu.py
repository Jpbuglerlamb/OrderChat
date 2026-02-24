# app/ordering/menu.py
from __future__ import annotations

import re
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
    """
    Build a lookup index for fast matching:
      - items_by_id
      - name_to_item_syn (normalized item name -> item dict)
    """
    idx: Dict[str, Any] = {}
    items_by_id: Dict[str, Dict[str, Any]] = {}
    name_to_item_syn: Dict[str, Dict[str, Any]] = {}

    categories = menu.get("categories") or []
    if isinstance(categories, list):
        for c in categories:
            if not isinstance(c, dict):
                continue
            for it in (c.get("items") or []):
                if isinstance(it, dict):
                    _index_item(it, items_by_id, name_to_item_syn, synonyms)

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
    """
    Returns category display names from nested schema:
      menu["categories"] = [{ "name": "...", "items": [...] }, ...]
    """
    cats = menu.get("categories") or []
    out: List[str] = []
    if isinstance(cats, list):
        for c in cats:
            if isinstance(c, dict):
                n = str(c.get("name") or "").strip()
                if n:
                    out.append(n)
    return out


def find_category_name(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[str]:
    """
    Match user text to a category name (e.g. "soups" -> "Soups").
    Uses normalize_text + fuzzy matching.
    """
    if not text:
        return None

    cats = all_category_names(menu)
    if not cats:
        return None

    # normalized category name -> original display name
    cat_norm_to_display: Dict[str, str] = {}
    for c in cats:
        cn = normalize_text(c, synonyms)
        if cn:
            cat_norm_to_display[cn] = c

    q = normalize_text(text, synonyms)
    if not q:
        return None

    # exact normalized match
    if q in cat_norm_to_display:
        return cat_norm_to_display[q]

    # fuzzy normalized match
    best = fuzzy_best_key(list(cat_norm_to_display.keys()), q, cutoff=0.78)
    return cat_norm_to_display.get(best) if best else None

def extract_category_from_text(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[str]:
    """
    Find a category mentioned anywhere in the message.
    e.g. "what's in the starters" -> "Starters"
    """
    cats = all_category_names(menu)
    if not text or not cats:
        return None

    text_norm = normalize_text(text, synonyms)

    # exact containment match (fast + reliable)
    for c in cats:
        c_norm = normalize_text(c, synonyms)
        if c_norm and c_norm in text_norm:
            return c

    # singular/plural tolerance (starter -> starters, soup -> soups)
    for c in cats:
        c_norm = normalize_text(c, synonyms)
        if not c_norm:
            continue
        if c_norm.endswith("s") and c_norm[:-1] and c_norm[:-1] in text_norm:
            return c
        if (c_norm + "s") in text_norm:
            return c

    return None

def items_in_category(menu: Dict[str, Any], category_name: str, synonyms: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Return items for a category display name.

    Supports:
    1) Nested schema:
       categories: [{name, items:[...]}]
    2) Flat schema:
       categories: [{id,name}], items: [{category_id:<id>, ...}]
    """
    out: List[Dict[str, Any]] = []
    if not category_name:
        return out

    needle = normalize_text(category_name, synonyms)
    if not needle:
        return out

    cats = menu.get("categories") or []
    if not isinstance(cats, list):
        cats = []

    # --- A) Nested schema: only use if the category actually HAS an "items" list ---
    for c in cats:
        if not isinstance(c, dict):
            continue
        c_name = str(c.get("name") or "").strip()
        if not c_name:
            continue

        if normalize_text(c_name, synonyms) == needle:
            # Only treat as nested schema if "items" key exists and is a list
            if "items" in c and isinstance(c.get("items"), list):
                return [it for it in (c.get("items") or []) if isinstance(it, dict)]
            # Otherwise, it's flat schema category: do NOT return here
            break

    # --- B) Flat schema: resolve category id by name, then filter menu["items"] ---
    cat_id = None
    for c in cats:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        c_name = str(c.get("name") or "").strip()
        if cid and c_name and normalize_text(c_name, synonyms) == needle:
            cat_id = cid
            break

    if not cat_id:
        return out

    items = menu.get("items") or []
    if not isinstance(items, list):
        return out

    for it in items:
        if not isinstance(it, dict):
            continue
        it_cid = str(it.get("category_id") or it.get("categoryId") or "").strip()
        if it_cid == cat_id:
            out.append(it)

    return out

_LEADING_JOINERS_RE = re.compile(r"^(?:and|with)\s+", re.I)
_LEADING_ARTICLES_RE = re.compile(r"^(?:a|an|the)\s+", re.I)


def _query_variants(text: str, synonyms: Dict[str, str]) -> List[str]:
    """
    Generate tolerant query variants so phrases like:
      - "and a coke"
      - "with egg fried rice"
    still match menu items.
    """
    raw = (text or "").strip()
    if not raw:
        return []

    q0 = normalize_text(raw, synonyms)
    if not q0:
        return []

    variants = [q0]

    q1 = _LEADING_JOINERS_RE.sub("", q0).strip()
    if q1 and q1 not in variants:
        variants.append(q1)

    q2 = _LEADING_ARTICLES_RE.sub("", q1).strip()
    if q2 and q2 not in variants:
        variants.append(q2)

    # Extra: sometimes you get "and a coca cola" after synonym expansion
    q3 = _LEADING_ARTICLES_RE.sub("", _LEADING_JOINERS_RE.sub("", q0).strip()).strip()
    if q3 and q3 not in variants:
        variants.append(q3)

    return variants


def find_item(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[Dict[str, Any]]:
    idx = menu.get("_index") or {}
    lookup: Dict[str, Dict[str, Any]] = idx.get("name_to_item_syn") or {}
    if not text:
        return None

    variants = _query_variants(text, synonyms)
    if not variants:
        return None

    # 1) exact match any variant
    for q in variants:
        if q in lookup:
            return lookup[q]

    # 2) fuzzy match any variant (start strict, then slightly looser)
    keys = list(lookup.keys())
    for cutoff in (0.80, 0.78, 0.75):
        for q in variants:
            best = fuzzy_best_key(keys, q, cutoff=cutoff)
            if best:
                return lookup.get(best)

    return None