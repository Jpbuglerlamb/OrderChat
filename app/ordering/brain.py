# app/ordering/brain.py
from __future__ import annotations

from typing import Any, Dict, Tuple

from .nlp import strip_filler_prefix, normalize_text, split_intents, parse_qty_prefix
from .menu import (
    build_menu_index,
    menu_synonyms,
    currency_symbol,
    all_category_names,
    find_item,
    find_category_name,   # NEW
    items_in_category,    # NEW
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
    lines = []
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

    # --- category selection (NEW: MUST be before add-items flow) ---
    # When the user taps/types "Soups", "Starters", etc.
    cat = find_category_name(menu, msg_raw, synonyms)
    if cat:
        items = items_in_category(menu, cat)
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