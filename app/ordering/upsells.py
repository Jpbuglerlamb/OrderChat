from __future__ import annotations

from typing import Any, Dict, List


def _basket_names(cart: List[Dict[str, Any]]) -> set[str]:
    out = set()
    for line in cart:
        name = str(line.get("name") or "").strip().lower()
        if name:
            out.add(name)
    return out


def _menu_name_lookup(menu: Dict[str, Any]) -> set[str]:
    idx = menu.get("_index") or {}
    searchable = idx.get("searchable_items") or []
    names = set()

    for row in searchable:
        nm = str(row.get("name") or "").strip().lower()
        if nm:
            names.add(nm)

    return names


def _first_existing(options: List[str], menu_names: set[str], basket_names: set[str]) -> str | None:
    for opt in options:
        o = opt.strip().lower()
        if o in menu_names and o not in basket_names:
            return opt
    return None


def get_upsell_suggestion(cart: List[Dict[str, Any]], menu: Dict[str, Any]) -> str | None:
    """
    Return one smart upsell suggestion based on basket contents.
    Only suggest items that actually exist on the menu and are not already in basket.
    """
    basket_names = _basket_names(cart)
    menu_names = _menu_name_lookup(menu)

    has_fish = "fish" in basket_names or "fish supper" in basket_names
    has_chips = "chips" in basket_names or "fries" in basket_names or "fish supper" in basket_names
    has_burger = any("burger" in n for n in basket_names)
    has_pizza = any("pizza" in n for n in basket_names)
    has_kebab = any("kebab" in n or "doner" in n for n in basket_names)

    if has_fish and has_chips:
        extra = _first_existing(
            ["curry sauce", "gravy sauce", "peas", "can of coke", "coca cola", "irn bru"],
            menu_names,
            basket_names,
        )
        if extra:
            return f"Would you like to add {extra} with that?"

    if has_burger and not has_chips:
        extra = _first_existing(["chips", "fries"], menu_names, basket_names)
        if extra:
            return f"Want to add {extra} to make it more of a meal?"

    if has_pizza:
        extra = _first_existing(
            ["garlic bread", "coca cola", "pepsi max", "chips"],
            menu_names,
            basket_names,
        )
        if extra:
            return f"Would you like to add {extra} with your pizza?"

    if has_kebab:
        extra = _first_existing(
            ["chips", "fries", "coca cola", "pepsi max"],
            menu_names,
            basket_names,
        )
        if extra:
            return f"Would you like {extra} with that kebab?"

    return None