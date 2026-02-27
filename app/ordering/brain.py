# app/ordering/brain.py
from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Tuple

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
# Suggestion memory (state)
# -------------------------
SUGGESTION_MAX = 5
SUGGESTION_TTL_TURNS = 2

_CONFIRM_WORDS = {
    "yes",
    "yeah",
    "yep",
    "ok",
    "okay",
    "sure",
    "sounds good",
    "go on",
    "that",
    "that one",
    "this",
    "this one",
    "it",
}

_PLAIN_NUM_RE = re.compile(r"^\s*(\d{1,2})\s*$")
_SELECT_NUM_RE = re.compile(r"\b(?:#|no\.?|number)\s*(\d{1,2})\b", re.IGNORECASE)

_ORDINAL_MAP = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}


def _tick_suggestions(state: Dict[str, Any]) -> None:
    """Decrease TTL each turn; expire suggestions."""
    s = state.get("suggestions")
    if not isinstance(s, dict):
        return
    ttl = int(s.get("ttl") or 0) - 1
    if ttl <= 0:
        state.pop("suggestions", None)
    else:
        s["ttl"] = ttl
        state["suggestions"] = s


def _set_suggestions(state: Dict[str, Any], items: List[Dict[str, Any]], reason: str) -> None:
    """Store a small list of candidates for follow-up messages like 'that' or '2'."""
    candidates = []
    for it in items[:SUGGESTION_MAX]:
        candidates.append(
            {
                "id": str(it.get("id") or ""),
                "name": str(it.get("name") or "").strip(),
            }
        )
    state["suggestions"] = {"reason": reason, "items": candidates, "ttl": SUGGESTION_TTL_TURNS}


def _suggestions_items(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    s = state.get("suggestions")
    if not isinstance(s, dict):
        return []
    items = s.get("items")
    return items if isinstance(items, list) else []


def _looks_like_selection(msg_norm: str) -> bool:
    if not msg_norm:
        return False
    if msg_norm in _CONFIRM_WORDS:
        return True
    if "that" in msg_norm or "this" in msg_norm:
        return True
    if _PLAIN_NUM_RE.match(msg_norm):
        return True
    if _SELECT_NUM_RE.search(msg_norm):
        return True
    for k in _ORDINAL_MAP:
        if k in msg_norm:
            return True
    return False


def _resolve_selection(msg_norm: str, candidates: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """
    Resolve follow-ups:
      - "1" / "number 2" / "second"
      - "that" (if only one candidate)
      - fuzzy match by candidate name
    Returns candidate dict {id,name} or None.
    """
    if not candidates:
        return None

    # 1) numeric direct: "2"
    m = _PLAIN_NUM_RE.match(msg_norm)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]

    # 2) numeric with word: "number 2"
    m2 = _SELECT_NUM_RE.search(msg_norm)
    if m2:
        idx = int(m2.group(1))
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]

    # 3) ordinal: "second"
    for k, idx in _ORDINAL_MAP.items():
        if k in msg_norm and 1 <= idx <= len(candidates):
            return candidates[idx - 1]

    # 4) "that/ok/yes" with a single candidate
    if len(candidates) == 1 and (msg_norm in _CONFIRM_WORDS or "that" in msg_norm or "this" in msg_norm):
        return candidates[0]

    # 5) try contains by name
    for c in candidates:
        nm = str(c.get("name") or "").lower().strip()
        if nm and (nm in msg_norm or msg_norm in nm):
            return c

    # 6) fuzzy match
    names = [str(c.get("name") or "").lower().strip() for c in candidates]
    best = difflib.get_close_matches(msg_norm, names, n=1, cutoff=0.55)
    if best:
        chosen = best[0]
        for c in candidates:
            if str(c.get("name") or "").lower().strip() == chosen:
                return c

    return None


# -------------------------
# Helpers (formatting)
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


def _format_suggestions_list(items: List[Dict[str, Any]], currency: str, intro: str) -> str:
    """
    A compact numbered list to encourage follow-up selection:
      1) Item (£x.xx)
      2) Item (£x.xx)
    """
    lines = [intro]
    for i, it in enumerate(items[:SUGGESTION_MAX], start=1):
        name = str(it.get("name") or "Item").strip()
        price = it.get("base_price")
        if price is None:
            lines.append(f"{i}) {name}")
        else:
            try:
                p = float(price or 0.0)
                lines.append(f"{i}) {name} ({currency}{p:.2f})")
            except Exception:
                lines.append(f"{i}) {name}")
    lines.append("Reply with a number (e.g. “1”) or say “I’ll have that”.")
    return "\n".join(lines)


def _clean_order_phrase(text: str) -> str:
    """
    Turn: "okay i'll have the black bean beef then"
    into: "black bean beef"
    """
    t = (text or "").strip()

    # leading filler
    t = re.sub(r"^(?:okay|ok|alright|right)\b[,\s]*", "", t, flags=re.I)
    t = re.sub(r"^(?:i\s*will|i'll|ill)\s+(?:have|get|take)\b[,\s]*", "", t, flags=re.I)
    t = re.sub(r"^(?:can\s+i\s+have|can\s+i\s+get|could\s+i\s+have|could\s+i\s+get)\b[,\s]*", "", t, flags=re.I)
    t = re.sub(r"^(?:give\s+me)\b[,\s]*", "", t, flags=re.I)

    # trailing fluff
    t = re.sub(r"\b(?:please|pls|plz)\b\.?$", "", t, flags=re.I).strip()
    t = re.sub(r"\b(?:then)\b\.?$", "", t, flags=re.I).strip()

    return t.strip()


def _all_items_flat(menu: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten indexed menu into an item list."""
    flat: list[dict] = []
    for c in (menu.get("categories") or []):
        for it in (c.get("items") or []):
            if isinstance(it, dict):
                flat.append(it)
    return flat


def _keyword_matches(menu: Dict[str, Any], keyword: str) -> List[Dict[str, Any]]:
    """Substring match in name + (optional) description."""
    kw = (keyword or "").strip().lower()
    if not kw:
        return []
    hits: list[dict] = []
    for it in _all_items_flat(menu):
        name = str(it.get("name") or "").strip()
        desc = str(it.get("description") or "").strip()
        blob = (name + " " + desc).lower().strip()
        if kw and blob and kw in blob:
            hits.append(it)
    return hits


# -------------------------
# Natural language intents
# -------------------------
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


def _try_keyword_query(msg_norm: str) -> str | None:
    """
    If it looks like "do you have beef", return "beef".
    Only used if NOT a category.
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


def _add_item_to_cart(cart: List[Dict[str, Any]], item: Dict[str, Any], qty: int) -> None:
    new_line = {
        "item_id": str(item.get("id", "")),
        "name": str(item.get("name", "Item")),
        "qty": max(1, int(qty or 1)),
        "base_price": float(item.get("base_price", 0.0) or 0.0),
        "choices": {},
        "extras": [],
        "line_total": 0.0,
    }
    recalc_line_total(new_line)
    cart.append(new_line)


# -------------------------
# Main entry
# -------------------------
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

    # Normalize text for intent matching
    msg_norm = normalize_text(raw, synonyms)

    cart = load_cart(items_json)
    state = load_state(state_json)

    # Expire old suggestions each turn
    _tick_suggestions(state)

    # 0) Follow-up selection resolution (this is the "intelligent" part)
    candidates = _suggestions_items(state)
    if candidates and _looks_like_selection(msg_norm):
        chosen = _resolve_selection(msg_norm, candidates)
        if chosen and chosen.get("id"):
            # resolve to actual menu item by id or name
            item = find_item(menu, str(chosen["id"]), synonyms) or find_item(menu, str(chosen["name"]), synonyms)
            if item:
                _add_item_to_cart(cart, item, qty=1)
                state.pop("suggestions", None)
                summary, _ = build_summary(cart, currency_symbol=cur)
                return "Added ✅\n\n" + summary, dump_cart(cart), dump_state(state)

        # user said "ok/that" but multiple candidates
        if len(candidates) > 1 and msg_norm in _CONFIRM_WORDS.union({"that"}):
            lines = ["Which one would you like? Reply with a number:"]
            for i, c in enumerate(candidates[:SUGGESTION_MAX], start=1):
                lines.append(f"{i}) {c.get('name')}")
            return "\n".join(lines), dump_cart(cart), dump_state(state)

    # 1) Reset
    if msg_norm in {"reset", "clear", "start over", "new order"}:
        cart = []
        state = {}
        return "Cleared ✅ Starting fresh.", dump_cart(cart), dump_state(state)

    # 2) Basket
    if (msg_norm in _BASKET_INTENTS) or ("my basket" in msg_norm):
        summary, _ = build_summary(cart, currency_symbol=cur)
        return summary, dump_cart(cart), dump_state(state)

    # 3) Menu
    is_menu_intent = (msg_norm in _MENU_INTENTS) or ("menu" in msg_norm)
    if is_menu_intent:
        cats = all_category_names(menu)
        if cats:
            return "We have: " + ", ".join(cats), dump_cart(cart), dump_state(state)
        return "Tell me what you'd like.", dump_cart(cart), dump_state(state)

    # 4) Category browsing
    cat = _try_category_lookup(menu, msg_norm, synonyms)
    if cat:
        items = items_in_category(menu, cat, synonyms)
        # Store suggestions for follow-up selection
        if items:
            _set_suggestions(state, items, reason=f"category:{cat}")
        reply = _format_category_items(cat, items, cur)
        if items:
            reply += "\n\nWhich one would you like? You can reply “1”, “2”, or “that”."
        return reply, dump_cart(cart), dump_state(state)

    # 5) Keyword query like "do you have beef?"
    kw = _try_keyword_query(msg_norm)
    if kw:
        hits = _keyword_matches(menu, kw)
        if hits:
            _set_suggestions(state, hits, reason=f"keyword:{kw}")
            reply = _format_suggestions_list(hits, cur, f"Yep, we have {kw} dishes:")
        else:
            reply = f"I couldn’t find any {kw} dishes on this menu."
        return reply, dump_cart(cart), dump_state(state)

    # 6) Remove
    if msg_norm.startswith("remove ") or msg_norm.startswith("delete "):
        target_text = msg_norm.split(" ", 1)[1].strip()
        target = find_item(menu, target_text, synonyms)
        if not target:
            return "Tell me which item to remove (e.g. “remove Egg Fried Rice”).", dump_cart(cart), dump_state(state)

        target_id = str(target.get("id") or "")
        removed = False
        new_cart = []
        for ln in cart:
            if (not removed) and target_id and str(ln.get("item_id") or "") == target_id:
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

    # 7) Add items (supports multiple)
    parts = split_intents(msg_norm)

    added = False
    for part in parts:
        qty, text = parse_qty_prefix(part)
        text = _clean_order_phrase(text)
        text_norm = normalize_text(text, synonyms)

        item = find_item(menu, text_norm, synonyms) or find_item(menu, text, synonyms)
        if not item:
            continue

        _add_item_to_cart(cart, item, qty=qty)
        added = True

    if added:
        summary, _ = build_summary(cart, currency_symbol=cur)
        return "Added ✅\n\n" + summary, dump_cart(cart), dump_state(state)

    return "I didn’t catch that. Try 'menu', ask for a category, or type an item name.", dump_cart(cart), dump_state(state)