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


# -------------------------
# Helpers
# -------------------------
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


def _clean_order_phrase(text: str) -> str:
    """
    Turn: "okay i'll have the black bean beef then"
    into: "black bean beef"
    """
    t = (text or "").strip()

    # common leading phrases
    t = re.sub(r"^(?:okay|ok|alright|right)\b[,\s]*", "", t, flags=re.I)
    t = re.sub(r"^(?:i\s*will|i'll|ill)\s+(?:have|get|take)\b[,\s]*", "", t, flags=re.I)
    t = re.sub(r"^(?:can\s+i\s+have|can\s+i\s+get|could\s+i\s+have|could\s+i\s+get)\b[,\s]*", "", t, flags=re.I)
    t = re.sub(r"^(?:give\s+me)\b[,\s]*", "", t, flags=re.I)

    # trailing fluff
    t = re.sub(r"\b(?:please|pls|plz)\b\.?$", "", t, flags=re.I).strip()
    t = re.sub(r"\b(?:then)\b\.?$", "", t, flags=re.I).strip()

    return t.strip()


def _all_items_flat(menu: Dict[str, Any]) -> list[dict]:
    """
    menu is the indexed menu returned by build_menu_index.
    We want a flat list of items with name/base_price/id.
    """
    flat: list[dict] = []
    for c in (menu.get("categories") or []):
        for it in (c.get("items") or []):
            flat.append(it)
    return flat


def _keyword_matches(menu: Dict[str, Any], keyword: str) -> list[dict]:
    """
    Simple substring match against item names.
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return []
    hits: list[dict] = []
    for it in _all_items_flat(menu):
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        if kw in name.lower():
            hits.append(it)
    return hits


def _format_keyword_results(keyword: str, hits: list[dict], currency: str) -> str:
    if not hits:
        return f"I couldn’t find any **{keyword}** dishes on this menu."

    lines = []
    for it in hits[:8]:
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

    extra = ""
    if len(hits) > 8:
        extra = f"\n…and {len(hits) - 8} more."

    return f"Yep, we have {keyword} dishes:\n" + "\n".join(lines) + extra


# Natural language command aliases
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

# question-shaped "do you have X" patterns (X could be a category OR keyword like beef)
_KEYWORD_Q_PATTERNS = [
    re.compile(r"^(?:do you have|have you got|have|got)\s+(?P<kw>.+)$", re.I),
    re.compile(r"^(?:any|some)\s+(?P<kw>.+)$", re.I),
]


_CAT_Q_PATTERNS = [
    re.compile(r"^(?:any|some)\s+(?P<cat>.+)$", re.IGNORECASE),
    re.compile(r"^(?:got|got any|have you got|do you have|have)\s+(?P<cat>.+)$", re.IGNORECASE),
    re.compile(r"^(?:any)\s+(?P<cat>.+?)\s+(?:available|today|now)$", re.IGNORECASE),
    re.compile(r"^(?:what|which)\s+(?P<cat>.+?)\s+(?:do you have|have you got|have)$", re.IGNORECASE),
]


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


def _try_keyword_query(menu: Dict[str, Any], msg_norm: str) -> str | None:
    """
    If it looks like "do you have beef", return "beef".
    We'll only use this if it is NOT a real category (categories are handled earlier).
    """
    for pat in _KEYWORD_Q_PATTERNS:
        m = pat.match(msg_norm or "")
        if not m:
            continue
        kw = (m.group("kw") or "").strip()
        kw = re.sub(r"\b(?:dishes|dish|options|stuff|meals)\b$", "", kw, flags=re.I).strip()
        kw = re.sub(r"\b(?:please|pls|plz)\b$", "", kw, flags=re.I).strip()
        kw = re.sub(r"\?$", "", kw).strip()
        if kw:
            return kw
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
    raw = _clean_order_phrase(raw)
    msg_norm = normalize_text(raw, synonyms)

    cart = load_cart(items_json)
    state = load_state(state_json)

    # If we previously suggested items, allow quick follow-ups
    suggested = state.get("suggested_items") if isinstance(state, dict) else None
    if isinstance(suggested, list) and suggested:
        # If user message matches one suggested item (substring), add it
        for name in suggested:
            if not isinstance(name, str):
                continue
            if name and name.lower() in raw.lower():
                item = find_item(menu, name, synonyms)
                if item:
                    new_line = {
                        "item_id": str(item.get("id", "")),
                        "name": str(item.get("name", "Item")),
                        "qty": 1,
                        "base_price": float(item.get("base_price", 0.0) or 0.0),
                        "choices": {},
                        "extras": [],
                        "line_total": 0.0,
                    }
                    recalc_line_total(new_line)
                    cart.append(new_line)
                    state["suggested_items"] = []  # clear
                    summary, _ = build_summary(cart, currency_symbol=cur)
                    return "Added ✅\n\n" + summary, dump_cart(cart), dump_state(state)

    # reset
    if msg_norm in {"reset", "clear", "start over", "new order"}:
        cart = []
        state = {}
        return "Cleared ✅ Starting fresh.", dump_cart(cart), dump_state(state)

    # basket
    if (msg_norm in _BASKET_INTENTS) or ("my basket" in msg_norm):
        summary, _ = build_summary(cart, currency_symbol=cur)
        return summary, dump_cart(cart), dump_state(state)

    # menu
    is_menu_intent = (msg_norm in _MENU_INTENTS) or ("menu" in msg_norm)
    if is_menu_intent:
        cats = all_category_names(menu)
        if cats:
            return "We have: " + ", ".join(cats), dump_cart(cart), dump_state(state)
        return "Tell me what you'd like.", dump_cart(cart), dump_state(state)

    # category browsing
    cat = _try_category_lookup(menu, msg_norm, synonyms)
    if cat:
        items = items_in_category(menu, cat, synonyms)
        return _format_category_items(cat, items, cur), dump_cart(cart), dump_state(state)

    # keyword/ingredient query like "do you have beef?"
    kw = _try_keyword_query(menu, msg_norm)
    if kw:
        # If keyword happens to be a real category, category handler already caught it above.
        hits = _keyword_matches(menu, kw)
        reply = _format_keyword_results(kw, hits, cur)

        # remember suggestions so follow-up "I'll have the black bean beef" can be matched
        if hits:
            state["suggested_items"] = [str(it.get("name") or "") for it in hits[:12] if it.get("name")]
        else:
            state["suggested_items"] = []

        return reply, dump_cart(cart), dump_state(state)

    # remove
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

    # add items (supports multiple)
    parts = split_intents(msg_norm)

    added = False
    for part in parts:
        qty, text = parse_qty_prefix(part)
        text = _clean_order_phrase(text)
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