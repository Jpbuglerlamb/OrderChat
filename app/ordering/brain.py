# app/ordering/brain.py
from __future__ import annotations

from typing import Any, Dict, Tuple

from .nlp import strip_filler_prefix, normalize_text, split_intents, parse_qty_prefix
from .menu import build_menu_index, menu_synonyms, currency_symbol, all_category_names, find_item
from .cart import load_state, load_cart, dump_cart, dump_state, recalc_line_total, build_summary


def handle_message(
    message: str,
    items_json: str,
    menu_dict: Dict[str, Any],
    state_json: str = "{}",
) -> Tuple[str, str, str]:
    synonyms = menu_synonyms(menu_dict)
    menu = build_menu_index(menu_dict, synonyms)
    cur = currency_symbol(menu)

    msg_raw = (message or "").strip()
    msg_raw = strip_filler_prefix(msg_raw)
    msg_norm = normalize_text(msg_raw, synonyms)

    cart = load_cart(items_json)
    state = load_state(state_json)

    # --- commands ---
    if msg_norm in {"reset", "clear", "start over", "new order"}:
        cart = []
        state = {}
        return "Cleared ✅ Starting fresh.", dump_cart(cart), dump_state(state)

    if msg_norm in {"basket", "cart", "summary", "my order"}:
        summary, _ = build_summary(cart, currency_symbol=cur)
        return summary, dump_cart(cart), dump_state(state)

    if msg_norm in {"menu", "show menu"} or "menu" == msg_norm:
        cats = all_category_names(menu)
        if cats:
            return "We have: " + ", ".join(cats), dump_cart(cart), dump_state(state)
        return "Tell me what you'd like.", dump_cart(cart), dump_state(state)

    # --- add items (supports multiple) ---
    parts = split_intents(normalize_text(msg_raw, synonyms))

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