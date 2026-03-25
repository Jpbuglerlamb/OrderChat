# app/ordering/upsells.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from .nlp import normalize_text
from .menu import menu_synonyms


def _basket_names(cart: List[Dict[str, Any]]) -> Set[str]:
    out: Set[str] = set()
    for line in cart:
        name = str(line.get("name") or "").strip().lower()
        if name:
            out.add(name)
    return out


def _basket_names_normalized(cart: List[Dict[str, Any]], synonyms: Dict[str, str]) -> Set[str]:
    out: Set[str] = set()
    for line in cart:
        name = str(line.get("name") or "").strip()
        if not name:
            continue
        norm = normalize_text(name, synonyms)
        if norm:
            out.add(norm)
    return out


def _menu_name_lookup(menu: Dict[str, Any], synonyms: Dict[str, str]) -> Set[str]:
    idx = menu.get("_index") or {}
    searchable = idx.get("searchable_items") or []

    names: Set[str] = set()

    for row in searchable:
        raw_name = str(row.get("name") or "").strip()
        if raw_name:
            norm = normalize_text(raw_name, synonyms)
            if norm:
                names.add(norm)

        aliases = row.get("aliases") or []
        if isinstance(aliases, list):
            for alias in aliases:
                alias_str = str(alias or "").strip()
                if alias_str:
                    alias_norm = normalize_text(alias_str, synonyms)
                    if alias_norm:
                        names.add(alias_norm)

    return names


def _menu_display_name_lookup(menu: Dict[str, Any], synonyms: Dict[str, str]) -> Dict[str, str]:
    idx = menu.get("_index") or {}
    searchable = idx.get("searchable_items") or []

    lookup: Dict[str, str] = {}

    for row in searchable:
        display_name = str(row.get("name") or "").strip()
        if not display_name:
            continue

        display_norm = normalize_text(display_name, synonyms)
        if display_norm and display_norm not in lookup:
            lookup[display_norm] = display_name

        aliases = row.get("aliases") or []
        if isinstance(aliases, list):
            for alias in aliases:
                alias_str = str(alias or "").strip()
                if not alias_str:
                    continue
                alias_norm = normalize_text(alias_str, synonyms)
                if alias_norm and alias_norm not in lookup:
                    lookup[alias_norm] = display_name

    return lookup


def _first_existing(
    options: List[str],
    menu_names: Set[str],
    basket_names: Set[str],
    display_lookup: Dict[str, str],
    synonyms: Dict[str, str],
) -> Optional[str]:
    for opt in options:
        opt_norm = normalize_text(opt.strip(), synonyms)
        if not opt_norm:
            continue
        if opt_norm in menu_names and opt_norm not in basket_names:
            return display_lookup.get(opt_norm, opt.strip())
    return None


def _basket_has_any_keyword(basket_names: Set[str], keywords: List[str]) -> bool:
    for basket_name in basket_names:
        for kw in keywords:
            if kw in basket_name:
                return True
    return False


def get_upsell_suggestion(cart: List[Dict[str, Any]], menu: Dict[str, Any]) -> str | None:
    """
    Return one smart upsell suggestion based on basket contents.
    Only suggest items that actually exist on the menu and are not already in basket.
    """
    if not cart:
        return None

    synonyms = menu_synonyms(menu)

    basket_names_raw = _basket_names(cart)
    basket_names = _basket_names_normalized(cart, synonyms)
    menu_names = _menu_name_lookup(menu, synonyms)
    display_lookup = _menu_display_name_lookup(menu, synonyms)

    has_fish = "fish" in basket_names or "fish supper" in basket_names
    has_chips = "chips" in basket_names or "fries" in basket_names or "fish supper" in basket_names
    has_burger = _basket_has_any_keyword(basket_names_raw, ["burger"])
    has_pizza = _basket_has_any_keyword(basket_names_raw, ["pizza"])
    has_kebab = _basket_has_any_keyword(basket_names_raw, ["kebab", "doner"])
    has_drink = _basket_has_any_keyword(
        basket_names_raw,
        ["coke", "cola", "pepsi", "irn bru", "sprite", "fanta", "water", "drink"],
    )
    has_side = _basket_has_any_keyword(
        basket_names_raw,
        ["chips", "fries", "garlic bread", "spring rolls", "rice", "noodles"],
    )

    if has_fish and has_chips:
        extra = _first_existing(
            ["curry sauce", "gravy sauce", "peas", "coca cola", "irn bru"],
            menu_names,
            basket_names,
            display_lookup,
            synonyms,
        )
        if extra:
            return f"Would you like to add {extra} with that?"

    if has_burger and not has_side:
        extra = _first_existing(
            ["chips", "fries", "coca cola", "pepsi max"],
            menu_names,
            basket_names,
            display_lookup,
            synonyms,
        )
        if extra:
            return f"Want to add {extra} to make it more of a meal?"

    if has_pizza:
        extra = _first_existing(
            ["garlic bread", "coca cola", "pepsi max", "chips"],
            menu_names,
            basket_names,
            display_lookup,
            synonyms,
        )
        if extra:
            return f"Would you like to add {extra} with your pizza?"

    if has_kebab:
        extra = _first_existing(
            ["chips", "fries", "coca cola", "pepsi max"],
            menu_names,
            basket_names,
            display_lookup,
            synonyms,
        )
        if extra:
            return f"Would you like {extra} with that kebab?"

    if not has_drink:
        extra = _first_existing(
            ["coca cola", "pepsi max", "irn bru", "sprite", "still water"],
            menu_names,
            basket_names,
            display_lookup,
            synonyms,
        )
        if extra:
            return f"Would you like a drink with that? You could add {extra}."

    return None