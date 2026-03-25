# app/ordering/menu.py
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .nlp import (
    default_synonyms,
    fuzzy_best_key,
    generate_aliases,
    normalize_text,
    score_candidate,
)

# -------------------------
# Constants
# -------------------------
_STRONG_ITEM_SCORE = 0.84
_DECENT_ITEM_SCORE = 0.74

_LEADING_JOINERS_RE = re.compile(r"^(?:and|with|plus)\s+", re.I)
_LEADING_ARTICLES_RE = re.compile(r"^(?:a|an|the)\s+", re.I)


# -------------------------
# Public helpers
# -------------------------
def currency_symbol(menu: Dict[str, Any]) -> str:
    cur = str(((menu.get("meta") or {}).get("currency") or "GBP")).upper()
    return "£" if cur == "GBP" else ""


def menu_synonyms(menu: Dict[str, Any]) -> Dict[str, str]:
    meta = menu.get("meta") or {}
    custom = meta.get("synonyms") or {}
    merged = default_synonyms()

    if isinstance(custom, dict):
        merged.update(
            {
                str(k).lower().strip(): str(v).lower().strip()
                for k, v in custom.items()
                if str(k).strip() and str(v).strip()
            }
        )

    return merged


def build_menu_index(menu: Dict[str, Any], synonyms: Dict[str, str]) -> Dict[str, Any]:
    """
    Build a richer menu index and attach it to menu["_index"].

    Index keys:
      - items_by_id
      - items_by_name_norm
      - name_to_item_syn
      - category_name_to_items
      - category_norm_to_name
      - searchable_items
    """
    items_by_id: Dict[str, Dict[str, Any]] = {}
    items_by_name_norm: Dict[str, Dict[str, Any]] = {}
    name_to_item_syn: Dict[str, Dict[str, Any]] = {}
    category_name_to_items: Dict[str, List[Dict[str, Any]]] = {}
    category_norm_to_name: Dict[str, str] = {}
    searchable_items: List[Dict[str, Any]] = []

    for category_name, item in _iter_menu_items(menu):
        if not isinstance(item, dict):
            continue

        item_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or item.get("title") or item.get("item") or "").strip()
        if not name:
            continue

        category_name = str(category_name or "").strip()
        name_norm = normalize_text(name, synonyms)
        category_norm = normalize_text(category_name, synonyms) if category_name else ""

        aliases = generate_aliases(name, synonyms)
        if name_norm and name_norm not in aliases:
            aliases.append(name_norm)

        searchable_row = {
            "item": item,
            "item_id": item_id,
            "name": name,
            "name_norm": name_norm,
            "aliases": sorted(set(a for a in aliases if a)),
            "category": category_name,
            "category_norm": category_norm,
            "price": item.get("base_price", item.get("price")),
        }
        searchable_items.append(searchable_row)

        if item_id:
            items_by_id[item_id] = item

        if name_norm and name_norm not in items_by_name_norm:
            items_by_name_norm[name_norm] = item

        if name_norm and name_norm not in name_to_item_syn:
            name_to_item_syn[name_norm] = item

        for alias in searchable_row["aliases"]:
            if alias and alias not in name_to_item_syn:
                name_to_item_syn[alias] = item

        if category_name:
            category_name_to_items.setdefault(category_name, []).append(item)
            if category_norm and category_norm not in category_norm_to_name:
                category_norm_to_name[category_norm] = category_name

    idx: Dict[str, Any] = {
        "items_by_id": items_by_id,
        "items_by_name_norm": items_by_name_norm,
        "name_to_item_syn": name_to_item_syn,
        "category_name_to_items": category_name_to_items,
        "category_norm_to_name": category_norm_to_name,
        "searchable_items": searchable_items,
    }

    menu["_index"] = idx
    return menu


def all_category_names(menu: Dict[str, Any]) -> List[str]:
    idx = menu.get("_index") or {}
    lookup = idx.get("category_name_to_items") or {}
    if lookup:
        return list(lookup.keys())

    seen = set()
    out: List[str] = []
    for category_name, _item in _iter_menu_items(menu):
        category_name = str(category_name or "").strip()
        if category_name and category_name not in seen:
            seen.add(category_name)
            out.append(category_name)
    return out


def find_category_name(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[str]:
    if not text:
        return None

    idx = menu.get("_index") or {}
    category_norm_to_name: Dict[str, str] = idx.get("category_norm_to_name") or {}

    if not category_norm_to_name:
        cats = all_category_names(menu)
        for c in cats:
            cn = normalize_text(c, synonyms)
            if cn and cn not in category_norm_to_name:
                category_norm_to_name[cn] = c

    q = normalize_text(text, synonyms)
    if not q:
        return None

    # exact
    if q in category_norm_to_name:
        return category_norm_to_name[q]

    # singular/plural tolerance
    singular = _singularize(q)
    plural = _pluralize(q)

    for candidate in {singular, plural}:
        if candidate and candidate in category_norm_to_name:
            return category_norm_to_name[candidate]

    # fuzzy
    best = fuzzy_best_key(list(category_norm_to_name.keys()), q, cutoff=0.76)
    if best:
        return category_norm_to_name.get(best)

    return None


def extract_category_from_text(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[str]:
    if not text:
        return None

    idx = menu.get("_index") or {}
    category_norm_to_name: Dict[str, str] = idx.get("category_norm_to_name") or {}
    if not category_norm_to_name:
        for c in all_category_names(menu):
            cn = normalize_text(c, synonyms)
            if cn and cn not in category_norm_to_name:
                category_norm_to_name[cn] = c

    text_norm = normalize_text(text, synonyms)
    if not text_norm:
        return None

    # direct containment
    for c_norm, display_name in category_norm_to_name.items():
        if c_norm and c_norm in text_norm:
            return display_name

    # singular/plural containment
    for c_norm, display_name in category_norm_to_name.items():
        if not c_norm:
            continue
        singular = _singularize(c_norm)
        plural = _pluralize(c_norm)
        if singular and singular in text_norm:
            return display_name
        if plural and plural in text_norm:
            return display_name

    return None


def items_in_category(menu: Dict[str, Any], category_name: str, synonyms: Dict[str, str]) -> List[Dict[str, Any]]:
    if not category_name:
        return []

    idx = menu.get("_index") or {}
    lookup = idx.get("category_name_to_items") or {}
    category_norm_to_name = idx.get("category_norm_to_name") or {}

    if category_name in lookup:
        return lookup[category_name]

    needle = normalize_text(category_name, synonyms)
    if not needle:
        return []

    display_name = category_norm_to_name.get(needle)
    if display_name and display_name in lookup:
        return lookup[display_name]

    singular = _singularize(needle)
    plural = _pluralize(needle)

    for candidate in {singular, plural}:
        if candidate and candidate in category_norm_to_name:
            dn = category_norm_to_name[candidate]
            return lookup.get(dn, [])

    return []


def find_item(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Best-match item finder using:
      1) direct ID hit
      2) exact alias hit
      3) scored candidate ranking
    """
    idx = menu.get("_index") or {}
    items_by_id: Dict[str, Dict[str, Any]] = idx.get("items_by_id") or {}
    lookup: Dict[str, Dict[str, Any]] = idx.get("name_to_item_syn") or {}
    searchable_items: List[Dict[str, Any]] = idx.get("searchable_items") or []

    if not text:
        return None

    raw = str(text).strip()
    if not raw:
        return None

    # direct ID
    if raw in items_by_id:
        return items_by_id[raw]

    variants = _query_variants(raw, synonyms)
    if not variants:
        return None

    # exact alias hit
    for q in variants:
        if q in lookup:
            return lookup[q]

    # scored ranking
    best_item: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for q in variants:
        for row in searchable_items:
            score = score_candidate(q, row["aliases"], row["name_norm"])
            score = _boost_item_score_if_category_and_name_align(q, row, score)
            if score > best_score:
                best_score = score
                best_item = row["item"]

    if best_score >= _DECENT_ITEM_SCORE:
        return best_item

    return None


def find_item_with_score(
    menu: Dict[str, Any],
    text: str,
    synonyms: Dict[str, str],
) -> Tuple[Optional[Dict[str, Any]], float]:
    idx = menu.get("_index") or {}
    items_by_id: Dict[str, Dict[str, Any]] = idx.get("items_by_id") or {}
    searchable_items: List[Dict[str, Any]] = idx.get("searchable_items") or []

    raw = str(text or "").strip()
    if not raw:
        return None, 0.0

    if raw in items_by_id:
        return items_by_id[raw], 1.0

    variants = _query_variants(raw, synonyms)
    if not variants:
        return None, 0.0

    best_item: Optional[Dict[str, Any]] = None
    best_score = 0.0

    for q in variants:
        for row in searchable_items:
            score = score_candidate(q, row["aliases"], row["name_norm"])
            score = _boost_item_score_if_category_and_name_align(q, row, score)
            if score > best_score:
                best_score = score
                best_item = row["item"]

    return best_item, best_score


# -------------------------
# Internal helpers
# -------------------------
def _iter_menu_items(menu: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """
    Supports:
    1) nested schema:
       menu["categories"] = [{name, items:[...]}]
    2) flat schema:
       menu["categories"] = [{id, name}], menu["items"] = [{category_id:..., ...}]
    3) section schema:
       menu["sections"] = [{name, items:[...]}]
    4) loose top-level arrays:
       menu["items"], menu["menu_items"], menu["products"]
    """
    out: List[Tuple[str, Dict[str, Any]]] = []

    # 1) nested categories
    categories = menu.get("categories") or []
    if isinstance(categories, list):
        nested_found = False
        for c in categories:
            if not isinstance(c, dict):
                continue
            cat_name = str(c.get("name") or c.get("title") or "").strip()

            for key in ("items", "menu_items", "products"):
                cat_items = c.get(key)
                if isinstance(cat_items, list):
                    nested_found = True
                    for it in cat_items:
                        if isinstance(it, dict):
                            out.append((cat_name, it))

        if nested_found:
            return out

    # 2) flat category_id → name
    if isinstance(categories, list):
        cat_id_to_name: Dict[str, str] = {}
        for c in categories:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id") or "").strip()
            cname = str(c.get("name") or c.get("title") or "").strip()
            if cid and cname:
                cat_id_to_name[cid] = cname

        for key in ("items", "menu_items", "products"):
            top_items = menu.get(key)
            if isinstance(top_items, list):
                for it in top_items:
                    if not isinstance(it, dict):
                        continue
                    cid = str(it.get("category_id") or it.get("categoryId") or "").strip()
                    out.append((cat_id_to_name.get(cid, ""), it))

        if out:
            return out

    # 3) sections
    sections = menu.get("sections") or []
    if isinstance(sections, list):
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            sec_name = str(sec.get("name") or sec.get("title") or "").strip()
            for key in ("items", "menu_items", "products"):
                sec_items = sec.get(key)
                if isinstance(sec_items, list):
                    for it in sec_items:
                        if isinstance(it, dict):
                            out.append((sec_name, it))

    if out:
        return out

    # 4) loose top-level items
    for key in ("items", "menu_items", "products"):
        loose_items = menu.get(key)
        if isinstance(loose_items, list):
            for it in loose_items:
                if isinstance(it, dict):
                    out.append(("", it))

    return out


def _query_variants(text: str, synonyms: Dict[str, str]) -> List[str]:
    raw = str(text or "").strip()
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

    # singular/plural variants
    for q in list(variants):
        singular = _singularize(q)
        plural = _pluralize(q)

        if singular and singular not in variants:
            variants.append(singular)
        if plural and plural not in variants:
            variants.append(plural)

    return variants


def _singularize(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return ""

    if t.endswith("ies") and len(t) > 3:
        return t[:-3] + "y"
    if t.endswith("ses") and len(t) > 3:
        return t[:-2]
    if t.endswith("s") and not t.endswith("ss") and len(t) > 1:
        return t[:-1]
    return t


def _pluralize(text: str) -> str:
    t = str(text or "").strip()
    if not t:
        return ""

    if t.endswith("y") and len(t) > 1 and t[-2] not in "aeiou":
        return t[:-1] + "ies"
    if t.endswith(("s", "x", "z", "ch", "sh")):
        return t + "es"
    if not t.endswith("s"):
        return t + "s"
    return t


def _boost_item_score_if_category_and_name_align(query: str, row: Dict[str, Any], score: float) -> float:
    """
    Mildly boost score when the query resembles either:
      - the item name directly
      - the item category combined with part of the item name
    Example:
      query="chicken"
      item="Salt and Pepper Chicken"
      category="Mains"
    """
    q = str(query or "").strip()
    if not q:
        return score

    name_norm = str(row.get("name_norm") or "").strip()
    category_norm = str(row.get("category_norm") or "").strip()

    if q == name_norm:
        return max(score, 1.0)

    if q and name_norm and q in name_norm:
        score = max(score, 0.90)

    if q and category_norm and q == category_norm:
        score = max(score, 0.78)

    return min(score, 1.0)