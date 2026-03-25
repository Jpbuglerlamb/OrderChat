# app/ordering/brain.py
from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Tuple

from .aliases import expand_order_phrase
from .upsells import get_upsell_suggestion

from app.services.emailer import send_order_email
from .nlp import (
    normalize_text,
    split_intents,
    parse_qty_prefix,
    is_order_status_query,
    extract_status_target,
)
from .menu import (
    build_menu_index,
    menu_synonyms,
    currency_symbol,
    all_category_names,
    find_item,
    find_item_with_score,
    find_category_name,
    items_in_category,
    extract_category_from_text,
)
from .cart import (
    load_state,
    load_cart,
    dump_cart,
    dump_state,
    recalc_line_total,
    build_summary,
)

# -------------------------
# Suggestion + memory state
# -------------------------
SUGGESTION_MAX = 5
SUGGESTION_TTL_TURNS = 2
PENDING_CONFIRM_TTL_TURNS = 2

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
    "correct",
    "right",
}

_DENY_WORDS = {
    "no",
    "nah",
    "nope",
    "wrong",
    "not that",
    "different",
}

_REPEAT_WORDS = {
    "same again",
    "same",
    "again",
    "one more",
    "another one",
}

_RESET_WORDS = {"reset", "clear", "start over", "new order"}

_ADD_ON_PREFIX_RE = re.compile(r"^(?:and|with|plus)\s+(.+)$", re.IGNORECASE)
_REMOVE_PREFIX_RE = re.compile(r"^(?:remove|delete|take off|take away)\s+(.+)$", re.IGNORECASE)

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

_CONFIRM_INTENTS = {
    "confirm",
    "checkout",
    "place order",
    "place my order",
    "complete order",
    "done",
    "finish",
    "pay",
    "order now",
}

_KEYWORD_Q_PATTERNS = [
    re.compile(r"^(?:do you have|have you got|have|got)\s+(?P<kw>.+)$", re.I),
    re.compile(r"^(?:any|some)\s+(?P<kw>.+)$", re.I),
    re.compile(r"^(?:what about)\s+(?P<kw>.+)$", re.I),
    re.compile(r"^(?:anything|something|options)\s+(?:with|containing)\s+(?P<kw>.+)$", re.I),
]

_CAT_Q_PATTERNS = [
    re.compile(r"^(?:any|some)\s+(?P<cat>.+)$", re.IGNORECASE),
    re.compile(r"^(?:got|got any|have you got|do you have|have)\s+(?P<cat>.+)$", re.IGNORECASE),
    re.compile(r"^(?:any)\s+(?P<cat>.+?)\s+(?:available|today|now)$", re.IGNORECASE),
    re.compile(r"^(?:what|which)\s+(?P<cat>.+?)\s+(?:do you have|have you got|have)$", re.IGNORECASE),
]

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b")


def _extract_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text or "")
    return m.group(0).strip() if m else None


def _extract_phone(text: str) -> str | None:
    m = _PHONE_RE.search(text or "")
    if not m:
        return None
    p = re.sub(r"[^\d+]", "", m.group(0))
    return p if len(re.sub(r"\D", "", p)) >= 8 else None


def _is_cancel(text_norm: str) -> bool:
    return text_norm in {"cancel", "stop", "nevermind", "never mind", "back"}


# -------------------------
# Memory helpers
# -------------------------
def _tick_memory(state: Dict[str, Any]) -> None:
    suggestions = state.get("suggestions")
    if isinstance(suggestions, dict):
        ttl = int(suggestions.get("ttl") or 0) - 1
        if ttl <= 0:
            state.pop("suggestions", None)
        else:
            suggestions["ttl"] = ttl
            state["suggestions"] = suggestions

    pending_confirmation = state.get("pending_confirmation")
    if isinstance(pending_confirmation, dict):
        ttl = int(pending_confirmation.get("ttl") or 0) - 1
        if ttl <= 0:
            state.pop("pending_confirmation", None)
        else:
            pending_confirmation["ttl"] = ttl
            state["pending_confirmation"] = pending_confirmation


def _set_suggestions(state: Dict[str, Any], items: List[Dict[str, Any]], reason: str) -> None:
    candidates: List[Dict[str, Any]] = []
    for it in items[:SUGGESTION_MAX]:
        candidates.append(
            {
                "id": str(it.get("id") or ""),
                "name": str(it.get("name") or it.get("title") or it.get("item") or "").strip(),
                "base_price": it.get("base_price"),
            }
        )
    state["suggestions"] = {
        "reason": reason,
        "items": candidates,
        "ttl": SUGGESTION_TTL_TURNS,
    }


def _suggestions_items(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    s = state.get("suggestions")
    if not isinstance(s, dict):
        return []
    items = s.get("items")
    return items if isinstance(items, list) else []


def _set_pending_confirmation(state: Dict[str, Any], item: Dict[str, Any], qty: int, score: float) -> None:
    state["pending_confirmation"] = {
        "item_id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("title") or item.get("item") or "").strip(),
        "qty": max(1, int(qty or 1)),
        "score": float(score or 0.0),
        "ttl": PENDING_CONFIRM_TTL_TURNS,
    }


def _remember_last_added(state: Dict[str, Any], item: Dict[str, Any], qty: int) -> None:
    state["last_added_item"] = {
        "item_id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("title") or item.get("item") or "").strip(),
        "qty": max(1, int(qty or 1)),
    }


def _get_last_added_item(menu: Dict[str, Any], state: Dict[str, Any], synonyms: Dict[str, str]) -> Dict[str, Any] | None:
    last = state.get("last_added_item")
    if not isinstance(last, dict):
        return None

    item_id = str(last.get("item_id") or "").strip()
    item_name = str(last.get("name") or "").strip()

    if item_id:
        found = find_item(menu, item_id, synonyms)
        if found:
            return found

    if item_name:
        found = find_item(menu, item_name, synonyms)
        if found:
            return found

    return None


# -------------------------
# Suggestion selection helpers
# -------------------------
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
    if not candidates:
        return None

    m = _PLAIN_NUM_RE.match(msg_norm)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]

    m2 = _SELECT_NUM_RE.search(msg_norm)
    if m2:
        idx = int(m2.group(1))
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]

    for k, idx in _ORDINAL_MAP.items():
        if k in msg_norm and 1 <= idx <= len(candidates):
            return candidates[idx - 1]

    if len(candidates) == 1 and (msg_norm in _CONFIRM_WORDS or "that" in msg_norm or "this" in msg_norm):
        return candidates[0]

    for c in candidates:
        nm = str(c.get("name") or "").lower().strip()
        if nm and (nm in msg_norm or msg_norm in nm):
            return c

    names = [str(c.get("name") or "").lower().strip() for c in candidates]
    best = difflib.get_close_matches(msg_norm, names, n=1, cutoff=0.55)
    if best:
        chosen = best[0]
        for c in candidates:
            if str(c.get("name") or "").lower().strip() == chosen:
                return c

    return None


# -------------------------
# Text splitting / cleaning
# -------------------------
def _split_with_then_intents(msg_norm: str) -> List[str]:
    """
    Split on 'with' first, then use split_intents().
    This helps separate common order phrases like:
    'chicken burger with chips and coke'
    """
    s = (msg_norm or "").strip()
    if not s:
        return [""]

    chunks = [c.strip() for c in re.split(r"\s+\bwith\b\s+", s, flags=re.IGNORECASE) if c and c.strip()]

    parts: List[str] = []
    for c in chunks:
        parts.extend(split_intents(c))

    out: List[str] = []
    seen = set()
    for p in parts:
        p = (p or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)

    return out or [s]


def _clean_order_phrase(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^(?:okay|ok|alright|right)\b[,\s]*", "", t, flags=re.I)
    t = re.sub(r"^(?:i\s*will|i'll|ill)\s+(?:have|get|take)\b[,\s]*", "", t, flags=re.I)
    t = re.sub(
        r"^(?:can\s+i\s+have|can\s+i\s+get|could\s+i\s+have|could\s+i\s+get)\b[,\s]*",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"^(?:give\s+me)\b[,\s]*", "", t, flags=re.I)
    t = re.sub(r"\b(?:please|pls|plz)\b\.?$", "", t, flags=re.I).strip()
    t = re.sub(r"\b(?:then)\b\.?$", "", t, flags=re.I).strip()
    return t.strip()


def _strip_add_on_prefix(text: str) -> str:
    t = (text or "").strip()
    m = _ADD_ON_PREFIX_RE.match(t)
    if m:
        return (m.group(1) or "").strip()
    return t


# -------------------------
# Formatting helpers
# -------------------------
def _format_category_items(cat_name: str, items: List[Dict[str, Any]], currency: str) -> str:
    if not items:
        return f"{cat_name}: no items found."

    lines: List[str] = []
    for it in items[:12]:
        name = str(it.get("name") or it.get("title") or it.get("item") or "Item").strip()
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
    lines = [intro]
    for i, it in enumerate(items[:SUGGESTION_MAX], start=1):
        name = str(it.get("name") or it.get("title") or it.get("item") or "Item").strip()
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


# -------------------------
# Keyword matching
# -------------------------
def _all_items_flat(menu: Dict[str, Any]) -> List[Dict[str, Any]]:
    flat: List[Dict[str, Any]] = []

    for c in (menu.get("categories") or []):
        for key in ("items", "menu_items", "products"):
            items = c.get(key)
            if isinstance(items, list):
                flat.extend([it for it in items if isinstance(it, dict)])

    if not flat:
        for key in ("items", "menu_items", "products"):
            items = menu.get(key)
            if isinstance(items, list):
                flat.extend([it for it in items if isinstance(it, dict)])

    if not flat:
        for sec in (menu.get("sections") or []):
            for key in ("items", "menu_items", "products"):
                items = sec.get(key)
                if isinstance(items, list):
                    flat.extend([it for it in items if isinstance(it, dict)])

    return flat


def _extract_browse_keyword(text: str) -> str | None:
    s = (text or "").strip().lower()
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s)

    if not s:
        return None

    patterns = [
        r"^what\s+(.+?)\s+do\s+you\s+have$",
        r"^which\s+(.+?)\s+do\s+you\s+have$",
        r"^show\s+me\s+(.+)$",
        r"^any\s+(.+)$",
        r"^some\s+(.+)$",
        r"^do\s+you\s+have\s+any\s+(.+)$",
        r"^what\s+(.+?)\s+have\s+you\s+got$",
        r"^what\s+(.+?)\s+is\s+there$",
    ]

    for pat in patterns:
        m = re.match(pat, s, flags=re.IGNORECASE)
        if not m:
            continue

        kw = (m.group(1) or "").strip()
        kw = re.sub(
            r"\b(dishes|dish|items|item|options|option|stuff|meals|meal)\b$",
            "",
            kw,
            flags=re.IGNORECASE,
        ).strip()
        kw = re.sub(r"^(some|any|a|an|the)\s+", "", kw, flags=re.IGNORECASE).strip()

        if kw:
            return kw

    return None


def _item_text_blob(it: Dict[str, Any]) -> str:
    name = str(it.get("name") or it.get("title") or it.get("item") or "").strip()
    desc = str(it.get("description") or it.get("desc") or "").strip()
    return (name + " " + desc).strip()


def _keyword_matches(menu: Dict[str, Any], keyword: str, synonyms: Dict[str, str]) -> List[Dict[str, Any]]:
    kw_raw = (keyword or "").strip().lower()
    if not kw_raw:
        return []

    kw_norm = normalize_text(kw_raw, synonyms).lower().strip()
    kw_tokens = set(kw_norm.split())

    hits: List[Dict[str, Any]] = []

    for it in _all_items_flat(menu):
        blob_raw = _item_text_blob(it).lower()
        if not blob_raw:
            continue

        blob_norm = normalize_text(blob_raw, synonyms).lower()
        blob_tokens = set(blob_norm.split())

        if kw_raw in blob_raw:
            hits.append(it)
            continue

        if kw_norm and kw_norm in blob_norm:
            hits.append(it)
            continue

        if kw_tokens and kw_tokens.issubset(blob_tokens):
            hits.append(it)
            continue

    return hits


def _try_keyword_query(msg_norm: str) -> str | None:
    for pat in _KEYWORD_Q_PATTERNS:
        m = pat.match(msg_norm or "")
        if not m:
            continue
        kw = (m.group("kw") or "").strip()
        kw = re.sub(r"^(?:some|any|a|an|the)\s+", "", kw, flags=re.I).strip()
        kw = re.sub(r"^(?:with|containing)\s+", "", kw, flags=re.I).strip()
        kw = re.sub(r"\b(?:dishes|dish|options|stuff|meals)\b$", "", kw, flags=re.I).strip()
        kw = re.sub(r"\b(?:please|pls|plz)\b$", "", kw, flags=re.I).strip()
        kw = re.sub(r"\?$", "", kw).strip()
        if kw:
            return kw
    return None


# -------------------------
# Category lookup
# -------------------------
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


# -------------------------
# Cart + email helpers
# -------------------------
def _add_item_to_cart(
    cart: List[Dict[str, Any]],
    item: Dict[str, Any],
    qty: int,
    choices: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    new_line = {
        "item_id": str(item.get("id", "")),
        "name": str(item.get("name") or item.get("title") or item.get("item") or "Item"),
        "qty": max(1, int(qty or 1)),
        "base_price": float(item.get("base_price", 0.0) or 0.0),
        "choices": choices or {},
        "extras": [],
        "line_total": 0.0,
    }
    recalc_line_total(new_line)
    cart.append(new_line)
    return new_line


def _find_cart_line_by_item_id(cart: List[Dict[str, Any]], item_id: str) -> Dict[str, Any] | None:
    for ln in reversed(cart):
        if str(ln.get("item_id") or "") == str(item_id or ""):
            return ln
    return None


def _find_cart_line_by_name(cart: List[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    target = (name or "").strip().lower()
    if not target:
        return None

    for ln in reversed(cart):
        ln_name = str(ln.get("name") or "").strip().lower()
        if ln_name == target or target in ln_name or ln_name in target:
            return ln

    names = [str(ln.get("name") or "").strip().lower() for ln in cart]
    best = difflib.get_close_matches(target, names, n=1, cutoff=0.6)
    if best:
        chosen = best[0]
        for ln in reversed(cart):
            if str(ln.get("name") or "").strip().lower() == chosen:
                return ln

    return None


def _remove_one_from_line(cart: List[Dict[str, Any]], line: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    removed = False
    new_cart: List[Dict[str, Any]] = []

    target_item_id = str(line.get("item_id") or "")
    target_name = str(line.get("name") or "").strip().lower()

    for ln in cart:
        same_id = target_item_id and str(ln.get("item_id") or "") == target_item_id
        same_name = str(ln.get("name") or "").strip().lower() == target_name

        if not removed and (same_id or same_name):
            qty = int(ln.get("qty", 1) or 1)
            if qty > 1:
                ln["qty"] = qty - 1
                recalc_line_total(ln)
                new_cart.append(ln)
            removed = True
            continue

        new_cart.append(ln)

    return new_cart, removed


def _try_add_expanded_phrase(
    cart: List[Dict[str, Any]],
    state: Dict[str, Any],
    menu: Dict[str, Any],
    text: str,
    qty: int,
    synonyms: Dict[str, str],
) -> Tuple[bool, str | None]:
    """
    Expand phrases like 'fish supper' into multiple menu items,
    but only if the exact phrase is not already a real menu item.
    """
    exact_item = find_item(menu, text, synonyms)
    if exact_item:
        return False, None

    expanded = expand_order_phrase(text, synonyms)
    if not expanded:
        return False, None

    added_any = False

    for part in expanded:
        part_name = str(part.get("name") or "").strip()
        part_qty = max(1, int(part.get("qty") or 1)) * max(1, int(qty or 1))

        item = find_item(menu, part_name, synonyms)
        if not item:
            continue

        modifier_reply = _begin_modifier_flow(state, item, qty=part_qty)
        if modifier_reply:
            return added_any, modifier_reply

        _add_item_to_cart(cart, item, qty=part_qty)
        _remember_last_added(state, item, part_qty)
        added_any = True

    return added_any, None


def _business_order_email(menu_dict: Dict[str, Any]) -> str:
    meta = menu_dict.get("meta") or {}
    if isinstance(meta, dict):
        for k in ("order_email", "email", "contact_email"):
            v = meta.get(k)
            if isinstance(v, str) and "@" in v:
                return v.strip()
    return "orders@example.com"


def _send_business_order_email(menu_dict: Dict[str, Any], summary: str, total: float, currency: str) -> None:
    to_email = _business_order_email(menu_dict)
    subject = f"New order received ({currency}{float(total or 0.0):.2f})"
    body = "New order:\n\n" + (summary or "")
    send_order_email(to_email=to_email, subject=subject, body=body)


# -------------------------
# Modifier helpers
# -------------------------
def _required_modifiers(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    mods = item.get("modifiers") or []
    if not isinstance(mods, list):
        return []
    return [m for m in mods if isinstance(m, dict) and m.get("required")]


def _modifier_prompt_text(mod: Dict[str, Any]) -> str:
    prompt = str(mod.get("prompt") or "Choose an option:").strip()
    options = mod.get("options") or []
    lines = [prompt]
    for opt in options:
        opt_str = str(opt).strip()
        if opt_str:
            lines.append(f"• {opt_str}")
    return "\n".join(lines)


def _match_modifier_option(user_text: str, options: List[str], synonyms: Dict[str, str]) -> str | None:
    raw = (user_text or "").strip()
    if not raw:
        return None

    norm = normalize_text(raw, synonyms)

    for opt in options:
        opt_str = str(opt).strip()
        opt_norm = normalize_text(opt_str, synonyms)
        if norm == opt_norm or norm in opt_norm or opt_norm in norm:
            return opt_str

    normalized_options = [normalize_text(str(opt).strip(), synonyms) for opt in options if str(opt).strip()]
    best = difflib.get_close_matches(norm, normalized_options, n=1, cutoff=0.6)
    if best:
        chosen_norm = best[0]
        for opt in options:
            opt_str = str(opt).strip()
            if normalize_text(opt_str, synonyms) == chosen_norm:
                return opt_str

    return None


def _begin_modifier_flow(state: Dict[str, Any], item: Dict[str, Any], qty: int) -> str | None:
    mods = _required_modifiers(item)
    if not mods:
        return None

    state["pending_item"] = {
        "item_id": str(item.get("id") or ""),
        "qty": max(1, int(qty or 1)),
        "modifier_index": 0,
        "choices": {},
    }
    return _modifier_prompt_text(mods[0])


# -------------------------
# Re-usable action helpers
# -------------------------
def _add_resolved_item(
    cart: List[Dict[str, Any]],
    state: Dict[str, Any],
    item: Dict[str, Any],
    qty: int,
    cur: str,
) -> Tuple[str, str, str] | None:
    modifier_reply = _begin_modifier_flow(state, item, qty=qty)
    if modifier_reply:
        return modifier_reply, dump_cart(cart), dump_state(state)

    _add_item_to_cart(cart, item, qty=qty)
    _remember_last_added(state, item, qty)
    summary, _ = build_summary(cart, currency_symbol=cur)
    return "Added ✅\n\n" + summary, dump_cart(cart), dump_state(state)


def _submit_order(
    cart: List[Dict[str, Any]],
    state: Dict[str, Any],
    menu_dict: Dict[str, Any],
    cur: str,
) -> Tuple[str, str, str]:
    state.pop("suggestions", None)
    state.pop("checkout_stage", None)
    state.pop("pending_confirmation", None)

    summary, total = build_summary(cart, currency_symbol=cur)

    try:
        _send_business_order_email(
            menu_dict,
            summary=(
                f"Customer: {state.get('customer_name', '')}\n"
                f"Email: {state.get('customer_email', '')}\n"
                f"Phone: {state.get('customer_phone', '')}\n\n"
                + (summary or "")
            ),
            total=float(total or 0.0),
            currency=cur,
        )
    except Exception:
        pass

    state["order_submitted"] = True
    return "Order placed\n\n" + summary, dump_cart(cart), dump_state(state)


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
    msg_norm = normalize_text(raw, synonyms)

    cart = load_cart(items_json)
    state = load_state(state_json)

    _tick_memory(state)

    # 0) Pending modifier flow
    pending = state.get("pending_item")
    if isinstance(pending, dict):
        if _is_cancel(msg_norm):
            state.pop("pending_item", None)
            return "No problem. I cancelled that item. What would you like instead?", dump_cart(cart), dump_state(state)

        item_id = str(pending.get("item_id") or "").strip()
        qty = max(1, int(pending.get("qty") or 1))
        modifier_index = int(pending.get("modifier_index") or 0)
        choices = dict(pending.get("choices") or {})

        item = find_item(menu, item_id, synonyms)
        if not item:
            state.pop("pending_item", None)
            return "That item is no longer available. Please choose something else.", dump_cart(cart), dump_state(state)

        mods = _required_modifiers(item)
        if not mods or modifier_index >= len(mods):
            _add_item_to_cart(cart, item, qty=qty, choices=choices)
            _remember_last_added(state, item, qty)
            state.pop("pending_item", None)
            summary, _ = build_summary(cart, currency_symbol=cur)
            return "Added ✅\n\n" + summary, dump_cart(cart), dump_state(state)

        current_mod = mods[modifier_index]
        options = [str(x).strip() for x in (current_mod.get("options") or []) if str(x).strip()]
        selected = _match_modifier_option(raw, options, synonyms)

        if not selected:
            return _modifier_prompt_text(current_mod), dump_cart(cart), dump_state(state)

        mod_key = str(current_mod.get("key") or f"modifier_{modifier_index}").strip()
        choices[mod_key] = selected

        modifier_index += 1
        pending["modifier_index"] = modifier_index
        pending["choices"] = choices
        state["pending_item"] = pending

        if modifier_index < len(mods):
            next_mod = mods[modifier_index]
            return _modifier_prompt_text(next_mod), dump_cart(cart), dump_state(state)

        _add_item_to_cart(cart, item, qty=qty, choices=choices)
        _remember_last_added(state, item, qty)
        state.pop("pending_item", None)
        summary, _ = build_summary(cart, currency_symbol=cur)
        return "Added ✅\n\n" + summary, dump_cart(cart), dump_state(state)

    # 1) Pending low-confidence confirmation
    pending_confirmation = state.get("pending_confirmation")
    if isinstance(pending_confirmation, dict):
        if msg_norm in _CONFIRM_WORDS:
            item = None
            item_id = str(pending_confirmation.get("item_id") or "").strip()
            item_name = str(pending_confirmation.get("name") or "").strip()
            qty = max(1, int(pending_confirmation.get("qty") or 1))

            if item_id:
                item = find_item(menu, item_id, synonyms)
            if not item and item_name:
                item = find_item(menu, item_name, synonyms)

            state.pop("pending_confirmation", None)

            if item:
                result = _add_resolved_item(cart, state, item, qty, cur)
                if result:
                    return result

            return "That item is no longer available. Please choose something else.", dump_cart(cart), dump_state(state)

        if msg_norm in _DENY_WORDS:
            state.pop("pending_confirmation", None)
            return "No problem. Try another item.", dump_cart(cart), dump_state(state)

    # 2) Suggestion follow-up selection
    candidates = _suggestions_items(state)
    if candidates and _looks_like_selection(msg_norm):
        chosen = _resolve_selection(msg_norm, candidates)
        if chosen:
            item = None
            cid = str(chosen.get("id") or "").strip()
            cname = str(chosen.get("name") or "").strip()

            if cid:
                item = find_item(menu, cid, synonyms)
            if not item and cname:
                item = find_item(menu, cname, synonyms)

            if item:
                state.pop("suggestions", None)
                result = _add_resolved_item(cart, state, item, 1, cur)
                if result:
                    return result

        if len(candidates) > 1 and (msg_norm in _CONFIRM_WORDS or "that" in msg_norm or "this" in msg_norm):
            lines = ["Which one would you like? Reply with a number:"]
            for i, c in enumerate(candidates[:SUGGESTION_MAX], start=1):
                lines.append(f"{i}) {c.get('name')}")
            return "\n".join(lines), dump_cart(cart), dump_state(state)

    # 3) Reset
    if msg_norm in _RESET_WORDS:
        return "Cleared ✅ Starting fresh.", dump_cart([]), dump_state({})

    # 4) Repeat last item
    if msg_norm in _REPEAT_WORDS:
        last_item = _get_last_added_item(menu, state, synonyms)
        if last_item:
            result = _add_resolved_item(cart, state, last_item, 1, cur)
            if result:
                reply, new_cart_json, new_state_json = result
                reply = reply.replace("Added ✅", "Added another one ✅")
                return reply, new_cart_json, new_state_json

    # 5) Basket
    if (msg_norm in _BASKET_INTENTS) or ("my basket" in msg_norm):
        summary, _ = build_summary(cart, currency_symbol=cur)
        return summary, dump_cart(cart), dump_state(state)

    # 6) Status query
    if is_order_status_query(raw):
        target = extract_status_target(raw)
        if state.get("order_submitted"):
            if target:
                return (
                    f"Order update ✅\n\nI can’t live-track the kitchen yet, but your order is placed. (You asked about: {target}.)",
                    dump_cart(cart),
                    dump_state(state),
                )
            return (
                "Order update ✅\n\nI can’t live-track the kitchen yet, but your order is placed and the restaurant has received it.",
                dump_cart(cart),
                dump_state(state),
            )
        return (
            "I don’t have an active order to track yet. If you meant your basket, type “basket”.",
            dump_cart(cart),
            dump_state(state),
        )

    # 7) Confirm / checkout
    if msg_norm in _CONFIRM_INTENTS:
        if not cart:
            return "Your basket is empty. Add something first 🙂", dump_cart(cart), dump_state(state)

        if not str(state.get("customer_name") or "").strip():
            state["checkout_stage"] = "need_name"
            return "Got it ✅ What name should I put on the order?", dump_cart(cart), dump_state(state)

        if not (str(state.get("customer_email") or "").strip() or str(state.get("customer_phone") or "").strip()):
            state["checkout_stage"] = "need_contact"
            return "Nice. What’s best: an email address or phone number?", dump_cart(cart), dump_state(state)

        return _submit_order(cart, state, menu_dict, cur)

    # 8) Checkout flow capture
    stage = str(state.get("checkout_stage") or "")
    if stage:
        if _is_cancel(msg_norm):
            state.pop("checkout_stage", None)
            return "No worries. Back to ordering. What would you like next?", dump_cart(cart), dump_state(state)

        raw_text = (message or "").strip()

        if stage == "need_name":
            if _extract_email(raw_text) or _extract_phone(raw_text):
                return "I need the name first 🙂 What name should I put on the order?", dump_cart(cart), dump_state(state)

            name = raw_text[:60].strip()
            if not name:
                return "What name should I put on the order?", dump_cart(cart), dump_state(state)

            state["customer_name"] = name
            state["checkout_stage"] = "need_contact"
            return f"Nice, {name}. What’s best: an email address or phone number?", dump_cart(cart), dump_state(state)

        if stage == "need_contact":
            email = _extract_email(raw_text)
            phone = _extract_phone(raw_text)

            if email:
                state["customer_email"] = email
            if phone and not state.get("customer_phone"):
                state["customer_phone"] = phone

            if not (state.get("customer_email") or state.get("customer_phone")):
                return "Could you send an email address or phone number? (Either one is fine.)", dump_cart(cart), dump_state(state)

            state.pop("checkout_stage", None)
            return _submit_order(cart, state, menu_dict, cur)

    # 9) Menu
    if (msg_norm in _MENU_INTENTS) or ("menu" in msg_norm):
        cats = all_category_names(menu)
        if cats:
            return "We have: " + ", ".join(cats), dump_cart(cart), dump_state(state)
        return "Tell me what you'd like.", dump_cart(cart), dump_state(state)

    # 10) Browse keyword query like "what chicken do you have?"
    browse_kw = _extract_browse_keyword(raw)
    if browse_kw:
        hits = _keyword_matches(menu, browse_kw, synonyms)
        if hits:
            _set_suggestions(state, hits, reason=f"browse:{browse_kw}")
            return (
                _format_suggestions_list(hits, cur, f"Here are the {browse_kw} options:"),
                dump_cart(cart),
                dump_state(state),
            )
        return (
            f"I couldn’t find any {browse_kw} options on this menu.",
            dump_cart(cart),
            dump_state(state),
        )

    # 11) Category browsing
    cat = _try_category_lookup(menu, msg_norm, synonyms)
    if cat:
        items = items_in_category(menu, cat, synonyms)
        if items:
            _set_suggestions(state, items, reason=f"category:{cat}")
            return _format_suggestions_list(items, cur, f"{cat}:"), dump_cart(cart), dump_state(state)
        return _format_category_items(cat, items, cur), dump_cart(cart), dump_state(state)

    # 12) Keyword query like "do you have beef?"
    kw = _try_keyword_query(msg_norm)
    if kw:
        hits = _keyword_matches(menu, kw, synonyms)
        if hits:
            _set_suggestions(state, hits, reason=f"keyword:{kw}")
            reply = _format_suggestions_list(hits, cur, f"Yep, we have {kw} dishes:")
        else:
            reply = f"I couldn’t find any {kw} dishes on this menu."
        return reply, dump_cart(cart), dump_state(state)

    # 13) Remove
    remove_match = _REMOVE_PREFIX_RE.match(msg_norm)
    if remove_match:
        target_text = (remove_match.group(1) or "").strip()

        if target_text in {"one", "one item", "last one", "last item"}:
            if not cart:
                return "Your basket is already empty.", dump_cart(cart), dump_state(state)

            last_line = cart[-1]
            cart, removed = _remove_one_from_line(cart, last_line)
            if not removed:
                return "That item isn’t in your basket.", dump_cart(cart), dump_state(state)

            summary, _ = build_summary(cart, currency_symbol=cur)
            return "Removed ✅\n\n" + summary, dump_cart(cart), dump_state(state)

        target = find_item(menu, target_text, synonyms)
        line = None

        if target:
            target_id = str(target.get("id") or "")
            line = _find_cart_line_by_item_id(cart, target_id)

        if not line:
            line = _find_cart_line_by_name(cart, target_text)

        if not line:
            return "Tell me which item to remove (e.g. “remove Egg Fried Rice”).", dump_cart(cart), dump_state(state)

        cart, removed = _remove_one_from_line(cart, line)
        if not removed:
            return "That item isn’t in your basket.", dump_cart(cart), dump_state(state)

        summary, _ = build_summary(cart, currency_symbol=cur)
        return "Removed ✅\n\n" + summary, dump_cart(cart), dump_state(state)

    # 14) Add items (multi-item natural language)
    msg_for_parts = _strip_add_on_prefix(msg_norm)
    parts = _split_with_then_intents(msg_for_parts)

    added_any = False
    matched_count = 0
    low_confidence_item = None
    low_confidence_score = 0.0
    low_confidence_qty = 1

    for part in parts:
        qty, text = parse_qty_prefix(part)
        text = (text or "").strip()
        if not text:
            continue

        text_clean = _clean_order_phrase(text)

        expanded_added, modifier_reply = _try_add_expanded_phrase(
            cart=cart,
            state=state,
            menu=menu,
            text=text_clean,
            qty=qty,
            synonyms=synonyms,
        )
        if modifier_reply:
            return modifier_reply, dump_cart(cart), dump_state(state)

        if expanded_added:
            added_any = True
            matched_count += 1
            continue

        candidates = [
            text_clean,
            normalize_text(text_clean, synonyms),
            text,
            normalize_text(text, synonyms),
        ]

        best_item = None
        best_score = 0.0

        for candidate in candidates:
            if not candidate:
                continue
            item, score = find_item_with_score(menu, candidate, synonyms)
            if score > best_score:
                best_item = item
                best_score = score

        if best_item and best_score >= 0.84:
            modifier_reply = _begin_modifier_flow(state, best_item, qty=qty)
            if modifier_reply:
                return modifier_reply, dump_cart(cart), dump_state(state)

            _add_item_to_cart(cart, best_item, qty=qty)
            _remember_last_added(state, best_item, qty)
            added_any = True
            matched_count += 1
            continue

        if best_item and best_score >= 0.68:
            if best_score > low_confidence_score:
                low_confidence_item = best_item
                low_confidence_score = best_score
                low_confidence_qty = qty

    if added_any:
        summary, _ = build_summary(cart, currency_symbol=cur)
        upsell = get_upsell_suggestion(cart, menu)

        if matched_count > 1:
            base_reply = "Added ✅ (multiple items)\n\n" + summary
        else:
            base_reply = "Added ✅\n\n" + summary

        if upsell:
            base_reply += "\n\n" + upsell

        return base_reply, dump_cart(cart), dump_state(state)

    if low_confidence_item:
        _set_pending_confirmation(state, low_confidence_item, qty=low_confidence_qty, score=low_confidence_score)
        return (
            f"Did you mean {low_confidence_item['name']}? Reply yes or no.",
            dump_cart(cart),
            dump_state(state),
        )

    # 15) Offer keyword suggestions for short inputs
    if msg_norm and len(msg_norm.split()) <= 2:
        hits = _keyword_matches(menu, msg_norm, synonyms)
        if hits:
            _set_suggestions(state, hits, reason=f"keyword:{msg_norm}")
            return (
                _format_suggestions_list(hits, cur, f"Here are {msg_norm} options:"),
                dump_cart(cart),
                dump_state(state),
            )

    return (
        "I didn’t catch that. You can try:\n"
        "• menu\n"
        "• basket\n"
        "• what chicken do you have\n"
        "• show me drinks\n"
        "• sweet and sour chicken\n"
        "• remove egg fried rice\n"
        "• confirm",
        dump_cart(cart),
        dump_state(state),
    )