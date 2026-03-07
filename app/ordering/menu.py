from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .nlp import (
    default_synonyms,
    fuzzy_best_key,
    generate_aliases,
    normalize_text,
    score_candidate,
)

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
    Build a richer menu index:
      - items_by_id
      - category_name_to_items
      - name_to_item_syn
      - searchable_items: list of enriched item records for scoring
    """
    idx: Dict[str, Any] = {}
    items_by_id: Dict[str, Dict[str, Any]] = {}
    name_to_item_syn: Dict[str, Dict[str, Any]] = {}
    category_name_to_items: Dict[str, List[Dict[str, Any]]] = {}
    searchable_items: List[Dict[str, Any]] = []

    for category_name, item in _iter_menu_items(menu):
        iid = str(item.get("id") or "").strip()
        nm = str(item.get("name") or "").strip()
        if not nm:
            continue

        name_norm = normalize_text(nm, synonyms)
        aliases = generate_aliases(nm, synonyms)

        enriched = {
            "item": item,
            "item_id": iid,
            "name": nm,
            "name_norm": name_norm,
            "aliases": aliases,
            "category": category_name,
            "category_norm": normalize_text(category_name, synonyms) if category_name else "",
            "price": item.get("price"),
        }
        searchable_items.append(enriched)

        if iid:
            items_by_id[iid] = item

        if name_norm:
            name_to_item_syn[name_norm] = item

        for alias in aliases:
            if alias and alias not in name_to_item_syn:
                name_to_item_syn[alias] = item

        if category_name:
            category_name_to_items.setdefault(category_name, []).append(item)

    idx["items_by_id"] = items_by_id
    idx["name_to_item_syn"] = name_to_item_syn
    idx["category_name_to_items"] = category_name_to_items
    idx["searchable_items"] = searchable_items

    menu["_index"] = idx
    return menu


def _iter_menu_items(menu: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Supports:
    1) nested schema:
       menu["categories"] = [{name, items:[...]}]
    2) flat schema:
       menu["categories"] = [{id, name}], menu["items"] = [{category_id:..., ...}]
    """
    out: List[Tuple[str, Dict[str, Any]]] = []

    categories = menu.get("categories") or []
    items = menu.get("items") or []

    if isinstance(categories, list):
        # nested
        nested_found = False
        for c in categories:
            if not isinstance(c, dict):
                continue
            cat_name = str(c.get("name") or "").strip()
            cat_items = c.get("items")
            if isinstance(cat_items, list):
                nested_found = True
                for it in cat_items:
                    if isinstance(it, dict):
                        out.append((cat_name, it))

        if nested_found:
            return out

        # flat
        cat_id_to_name: Dict[str, str] = {}
        for c in categories:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id") or "").strip()
            cname = str(c.get("name") or "").strip()
            if cid and cname:
                cat_id_to_name[cid] = cname

        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                cid = str(it.get("category_id") or it.get("categoryId") or "").strip()
                out.append((cat_id_to_name.get(cid, ""), it))

    return out


def all_category_names(menu: Dict[str, Any]) -> List[str]:
    seen = set()
    out: List[str] = []
    for category_name, _item in _iter_menu_items(menu):
        if category_name and category_name not in seen:
            seen.add(category_name)
            out.append(category_name)
    return out


def find_category_name(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[str]:
    if not text:
        return None

    cats = all_category_names(menu)
    if not cats:
        return None

    cat_norm_to_display: Dict[str, str] = {}
    for c in cats:
        cn = normalize_text(c, synonyms)
        if cn:
            cat_norm_to_display[cn] = c

    q = normalize_text(text, synonyms)
    if not q:
        return None

    if q in cat_norm_to_display:
        return cat_norm_to_display[q]

    best = fuzzy_best_key(list(cat_norm_to_display.keys()), q, cutoff=0.76)
    return cat_norm_to_display.get(best) if best else None


def extract_category_from_text(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[str]:
    cats = all_category_names(menu)
    if not text or not cats:
        return None

    text_norm = normalize_text(text, synonyms)

    for c in cats:
        c_norm = normalize_text(c, synonyms)
        if c_norm and c_norm in text_norm:
            return c

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
    if not category_name:
        return []

    idx = menu.get("_index") or {}
    lookup = idx.get("category_name_to_items") or {}
    if category_name in lookup:
        return lookup[category_name]

    needle = normalize_text(category_name, synonyms)
    for display_name, items in lookup.items():
        if normalize_text(display_name, synonyms) == needle:
            return items

    return []


_LEADING_JOINERS_RE = re.compile(r"^(?:and|with)\s+", re.I)
_LEADING_ARTICLES_RE = re.compile(r"^(?:a|an|the)\s+", re.I)


def _query_variants(text: str, synonyms: Dict[str, str]) -> List[str]:
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

    q3 = _LEADING_ARTICLES_RE.sub("", _LEADING_JOINERS_RE.sub("", q0).strip()).strip()
    if q3 and q3 not in variants:
        variants.append(q3)

    return variants


def find_item(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Best-match item finder using:
      1. exact alias match
      2. scored candidate ranking
    """
    idx = menu.get("_index") or {}
    lookup: Dict[str, Dict[str, Any]] = idx.get("name_to_item_syn") or {}
    searchable_items: List[Dict[str, Any]] = idx.get("searchable_items") or []

    if not text:
        return None

    variants = _query_variants(text, synonyms)
    if not variants:
        return None

    # 1) exact alias hit
    for q in variants:
        if q in lookup:
            return lookup[q]

    # 2) scored ranking
    best_item: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for q in variants:
        for row in searchable_items:
            score = score_candidate(q, row["aliases"], row["name_norm"])
            if score > best_score:
                best_score = score
                best_item = row["item"]

    # Thresholds:
    # 0.84 strong
    # 0.74 decent shorthand
    # below that gets risky
    if best_score >= 0.74:
        return best_item

    return None


def find_item_with_score(
    menu: Dict[str, Any],
    text: str,
    synonyms: Dict[str, str],
) -> Tuple[Optional[Dict[str, Any]], float]:
    idx = menu.get("_index") or {}
    searchable_items: List[Dict[str, Any]] = idx.get("searchable_items") or []

    variants = _query_variants(text, synonyms)
    if not variants:
        return None, 0.0

    best_item: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for q in variants:
        for row in searchable_items:
            score = score_candidate(q, row["aliases"], row["name_norm"])
            if score > best_score:
                best_score = score
                best_item = row["item"]

    return best_item, best_score