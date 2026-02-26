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
    """
    Simple chat-friendly category rendering.
    Keeps it lightweight so it works in your demo immediately.
    """
    if not items:
        return f"{cat_name}: no items found."

    # show up to 12 items (avoid massive walls of text)
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


# Natural language command aliases (people don’t speak like buttons)
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


# Question-shaped category queries:
# "any rice", "got noodles", "do you have drinks", "what sides do you have"
_CAT_Q_PATTERNS = [
    re.compile(r"^(?:any|some)\s+(?P<cat>.+)$", re.IGNORECASE),
    re.compile(r"^(?:got|got any|have you got|do you have|have)\s+(?P<cat>.+)$", re.IGNORECASE),
    re.compile(r"^(?:any)\s+(?P<cat>.+?)\s+(?:available|today|now)$", re.IGNORECASE),
    re.compile(r"^(?:what|which)\s+(?P<cat>.+?)\s+(?:do you have|have you got|have)$", re.IGNORECASE),
]


def _try_category_lookup(menu: Dict[str, Any], msg_norm: str, synonyms: Dict[str, str]) -> str | None:
    """
    Try to interpret the message as a category request.
    Uses msg_norm (normalized) to avoid punctuation/and/& issues.
    Handles "any rice" / "got noodles" / "what sides do you have".
    """
    if not msg_norm:
        return None

    # 1) direct category extraction ("sides", "rice and noodles", etc.)
    cat = extract_category_from_text(menu, msg_norm, synonyms) or find_category_name(menu, msg_norm, synonyms)
    if cat:
        return cat

    # 2) question-shaped category queries
    for pat in _CAT_Q_PATTERNS:
        m = pat.match(msg_norm)
        if not m:
            continue
        tail = (m.group("cat") or "").strip()

        # strip common trailing helper words from tail
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

    # --- commands ---
    if msg_norm in {"reset", "clear", "start over", "new order"}:
        cart = []
        state = {}
        return "Cleared ✅ Starting fresh.", dump_cart(cart), dump_state(state)

    # Basket intent (expanded)
    if (msg_norm in _BASKET_INTENTS) or ("my basket" in msg_norm):
        summary, _ = build_summary(cart, currency_symbol=cur)
        return summary, dump_cart(cart), dump_state(state)

    # Menu intent (expanded)
    is_menu_intent = (msg_norm in _MENU_INTENTS) or ("menu" in msg_norm)
    if is_menu_intent:
        cats = all_category_names(menu)
        if cats:
            return "We have: " + ", ".join(cats), dump_cart(cart), dump_state(state)
        return "Tell me what you'd like.", dump_cart(cart), dump_state(state)

    # --- category browsing ---
    cat = _try_category_lookup(menu, msg_norm, synonyms)
    if cat:
        items = items_in_category(menu, cat, synonyms)
        return _format_category_items(cat, items, cur), dump_cart(cart), dump_state(state)

    # --- remove (MUST be before add flow) ---
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

    # --- add items (supports multiple) ---
    # split based on normalized message so "&/+/,/and lists" are handled consistently
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