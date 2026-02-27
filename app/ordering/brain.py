# app/ordering/brain.py
from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from .nlp import strip_filler_prefix, normalize_text, split_intents, parse_qty_prefix
from .menu import (
    build_menu_index,
    menu_synonyms,
    currency_symbol,
    all_category_names,
    find_item,
    find_category_name,
    items_in_category,
    extract_category_from_text,
)
from .cart import load_state, load_cart, dump_cart, dump_state, recalc_line_total, build_summary


def _format_category_items(cat_name: str, items: list[dict], currency: str) -> str:
    if not items:
        return f"{cat_name}: no items found."

    lines: list[str] = []
    for it in items[:12]:
        name = str(it.get("name") or "Item").strip()
        price = it.get("base_price")
        if price is None:
            lines.append(f"• {name}")
        else:
            try:
                p = float(price or 0.0)
                lines.append(f"• {name} ({currency}{p:.2f})")
            except Exception:
                lines.append(f"• {name}")

    more = ""
    if len(items) > 12:
        more = f"\n…and {len(items) - 12} more."

    return f"{cat_name}:\n" + "\n".join(lines) + more


_MENU_INTENTS = {
    "menu",
    "show menu",
    "show me the menu",
    "what do you have",
    "what have you got",
    "what do you sell",
    "what can i get",
    "what can i have",
    "what are the options",
    "options",
    "list",
    "list menu",
    "see menu",
    "show options",
    "show categories",
    "categories",
    "what's on the menu",
    "whats on the menu",
    "what is on the menu",
}

_BASKET_INTENTS = {
    "basket",
    "cart",
    "summary",
    "my order",
    "show basket",
    "show cart",
    "show my order",
    "what's in my basket",
    "whats in my basket",
    "what is in my basket",
}

_CAT_Q_PATTERNS = [
    re.compile(r"^(?:any|some)\s+(?P<cat>.+)$", re.IGNORECASE),
    re.compile(r"^(?:got|got any|have you got|do you have|have)\s+(?P<cat>.+)$", re.IGNORECASE),
    re.compile(r"^(?:any)\s+(?P<cat>.+?)\s+(?:available|today|now)$", re.IGNORECASE),
    re.compile(r"^(?:what|which)\s+(?P<cat>.+?)\s+(?:do you have|have you got|have)$", re.IGNORECASE),
]


_PROTEIN_Q_RE = re.compile(
    r"^(?:do you have|have you got|got any|any)\s+"
    r"(?P<kw>beef|chicken|pork|duck|lamb|prawn|shrimp|tofu|veg|vegetable|veggie)s?"
    r"\??$",
    re.IGNORECASE,
)


def _all_menu_item_names(menu_index: Dict[str, Any], menu_dict: Dict[str, Any]) -> list[dict]:
    """
    Return a list of items (dicts) from either:
    - the indexed menu (preferred), or
    - the raw menu JSON (fallback)
    """
    items: list[dict] = []

    # 1) Preferred: indexed menu often keeps a flat list
    for it in (menu_index.get("items") or []):
        if isinstance(it, dict):
            items.append(it)

    # 2) Fallback: raw JSON categories/items
    if not items:
        for c in (menu_dict.get("categories") or []):
            for it in (c.get("items") or []):
                if isinstance(it, dict):
                    items.append(it)

    return items


def _items_matching_keyword(menu_index: Dict[str, Any], menu_dict: Dict[str, Any], keyword: str) -> list[dict]:
    kw = (keyword or "").strip().lower()
    if not kw:
        return []

    hits: list[dict] = []
    for it in _all_menu_item_names(menu_index, menu_dict):
        name = str(it.get("name") or "").lower()
        if kw in name:
            hits.append(it)
    return hits


def _try_category_lookup(menu: Dict[str, Any], msg_norm: str, synonyms: Dict[str, str]) -> str | None:
    if not msg_norm:
        return None

    cat = extract_category_from_text(menu, msg_norm, synonyms) or find_category_name(menu, msg_norm, synonyms)
    if cat:
        return cat

    for pat in _CAT_Q_PATTERNS:
        m = pat.match(msg_norm)
        if not m:
            continue
        tail = (m.group("cat") or "").strip()
        tail = re.sub(r"\b(?:please|pls|plz)\b$", "", tail).strip()
        tail = re.sub(r"\b(?:do you have|have you got|have|got)\b$", "", tail).strip()
        if not tail:
            continue

        cat2 = extract_category_from_text(menu, tail, synonyms) or find_category_name(menu, tail, synonyms)
        if cat2:
            return cat2

    return None


def handle_message(
    message: str,
    items_json: str,
    menu_dict: Dict[str, Any],
    state_json: str = "{}",
) -> Tuple[str, str, str]:
    synonyms = menu_synonyms(menu_dict)
    menu = build_menu_index(menu_dict, synonyms)
    cur = currency_symbol(menu)

    raw = (message or "").strip()
    raw = strip_filler_prefix(raw)
    msg_norm = normalize_text(raw, synonyms)

    cart = load_cart(items_json)
    state = load_state(state_json)

    # Reset
    if msg_norm in {"reset", "clear", "start over", "new order"}:
        cart = []
        state = {}
        return "Cleared ✅ Starting fresh.", dump_cart(cart), dump_state(state)

    # Basket
    if (msg_norm in _BASKET_INTENTS) or ("my basket" in msg_norm):
        summary, _ = build_summary(cart, currency_symbol=cur)
        return summary, dump_cart(cart), dump_state(state)

    # Menu
    is_menu_intent = (msg_norm in _MENU_INTENTS) or ("menu" in msg_norm) or any(s in msg_norm for s in _MENU_INTENTS)
    if is_menu_intent:
        cats = all_category_names(menu)
        if cats:
            return "We have: " + ", ".join(cats), dump_cart(cart), dump_state(state)
        return "Tell me what you'd like.", dump_cart(cart), dump_state(state)

    # Protein/ingredient queries ("do you have beef?")
    m = _PROTEIN_Q_RE.match(msg_norm)
    if m:
        kw = (m.group("kw") or "").strip().lower()
        hits = _items_matching_keyword(menu, menu_dict, kw)
        if not hits:
            return f"I couldn’t find any {kw} dishes on this menu.", dump_cart(cart), dump_state(state)

        lines: list[str] = []
        for it in hits[:8]:
            nm = str(it.get("name") or "Item").strip()
            price = it.get("base_price")
            if price is None:
                lines.append(f"• {nm}")
            else:
                try:
                    lines.append(f"• {nm} ({cur}{float(price):.2f})")
                except Exception:
                    lines.append(f"• {nm}")

        more = ""
        if len(hits) > 8:
            more = f"\n…and {len(hits) - 8} more."

        return f"Yep, we have {kw} dishes:\n" + "\n".join(lines) + more, dump_cart(cart), dump_state(state)

    # Category browsing
    cat = _try_category_lookup(menu, msg_norm, synonyms)
    if cat:
        items = items_in_category(menu, cat, synonyms)
        return _format_category_items(cat, items, cur), dump_cart(cart), dump_state(state)

    # Remove (must be before add)
    if msg_norm.startswith("remove ") or msg_norm.startswith("delete "):
        target_text = msg_norm.split(" ", 1)[1].strip()
        target = find_item(menu, target_text, synonyms)
        if not target:
            return "Tell me which item to remove (e.g. “remove Egg Fried Rice”).", dump_cart(cart), dump_state(state)

        target_id = target.get("id")
        removed = False
        new_cart = []
        for ln in cart:
            if (not removed) and target_id and ln.get("item_id") == target_id:
                qty = int(ln.get("qty", 1) or 1)
                if qty > 1:
                    ln["qty"] = qty - 1
                    recalc_line_total(ln)
                    new_cart.append(ln)
                removed = True
                continue
            new_cart.append(ln)

        if not removed:
            return "That item isn’t in your basket.", dump_cart(cart), dump_state(state)

        cart = new_cart
        summary, _ = build_summary(cart, currency_symbol=cur)
        return "Removed ✅\n\n" + summary, dump_cart(cart), dump_state(state)

    # Add items (supports multiple)
    parts = split_intents(msg_norm)
    added = False

    for part in parts:
        qty, text = parse_qty_prefix(part)
        item = find_item(menu, text, synonyms)
        if not item:
            continue

        new_line = {
            "item_id": str(item.get("id", "")),
            "name": str(item.get("name", "Item")),
            "qty": qty,
            "base_price": float(item.get("base_price", 0.0) or 0.0),
            "choices": {},
            "extras": [],
            "line_total": 0.0,
        }
        recalc_line_total(new_line)
        cart.append(new_line)
        added = True

    if added:
        summary, _ = build_summary(cart, currency_symbol=cur)
        return "Added ✅\n\n" + summary, dump_cart(cart), dump_state(state)

    return "I didn’t catch that. Try 'menu' or an item name.", dump_cart(cart), dump_state(state)