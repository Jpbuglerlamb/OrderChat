# app/ordering.py
from __future__ import annotations

import difflib
import json
import re
from typing import Any, Dict, List, Tuple, Optional


# ----------------------------
# Lightweight "human tolerance"
# ----------------------------

_DEFAULT_SYNONYMS: Dict[str, str] = {
    # common UK takeaway phrasing
    "chips": "fries",
    "chip": "fries",
    "coke": "coca cola",
    "cola": "coca cola",
    "water": "still water",
    # common typos
    "donner": "doner",
    "pepperonni": "pepperoni",
    "margarita": "margherita",
    # common drink variants
    "coca-cola": "coca cola",
    "cocacola": "coca cola",
}

_GREETINGS = {
    "hi", "hello", "hey", "yo", "hiya", "sup",
    "alright", "alright mate", "mate", "boss", "bossman",
    "morning", "evening",
}

_NO_EXTRAS = {
    "no extras", "no extra", "no", "none", "nah", "no thanks", "no thank you", "no thx"
}

_CONTINUE_ORDER = {
    "and a drink", "a drink", "drink", "something to drink",
    "and a side", "a side", "side", "sides",
    "and dessert", "dessert", "something sweet",
    "anything else", "something else", "add another", "another", "also",
    "yes", "yeah", "yep", "ok", "okay",
}

# Concept → dataset naming aliases
CATEGORY_ALIASES = {
    "drinks": ["drinks", "beverages", "soft drinks", "hot drinks"],
    "sides": ["sides", "extras", "side dishes", "small plates"],
    "desserts": ["desserts", "sweet", "sweets", "pudding"],
}

# Split multi-intent like: "latte and brownie", "wrap, fries & coke"
_SPLIT_RE = re.compile(r"\s*(?:,|&|\+| and )\s*", re.IGNORECASE)

# qty prefixes: "2x burger", "3× latte"
_QTY_RE = re.compile(r"^\s*(\d+)\s*[x×]\s*(.+?)\s*$", re.IGNORECASE)

# basic punctuation strip for matching
_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")

# token to protect "and/&" inside item phrases (so the splitter doesn't break them)
_AND_TOKEN = " __AND__ "

# Modifier option price deltas like "Large (+£2.00)" or "(+2.00)"
_PRICE_DELTA_RE = re.compile(
    r"\(\s*\+\s*(?:£\s*)?([0-9]+(?:\.[0-9]+)?)\s*\)",
    re.IGNORECASE,
)


# ----------------------------
# Text helpers
# ----------------------------

def _normalize_text(s: str, synonyms: Dict[str, str]) -> str:
    s = (s or "").strip().lower()
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()

    for k, v in (synonyms or {}).items():
        if not k:
            continue
        s = re.sub(rf"\b{re.escape(str(k).lower())}\b", str(v).lower(), s)

    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_filler_prefix(raw: str) -> str:
    """
    Remove polite / chatty prefixes so the splitter sees clean order phrases.
    Example:
      "Hello, I'd like X, and Y please"
       -> "X, and Y"
    """
    s = (raw or "").strip()

    # common "order intent" fluff
    s = re.sub(r"^\s*(hi|hello|hey)\b[,\s]*", "", s, flags=re.IGNORECASE)
    s = re.sub(
        r"^\s*(i\s*would\s*like|i'?d\s*like|can\s*i\s*get|could\s*i\s*get|may\s*i\s*have)\b[,\s]*",
        "",
        s,
        flags=re.IGNORECASE,
    )

    # trailing please (keeps inside item names safe)
    s = re.sub(r"\bplease\b\s*$", "", s, flags=re.IGNORECASE)

    return s.strip()


def _protect_and_phrases(raw_lower: str, phrases: List[str]) -> str:
    """
    Replaces "and" / "&" inside protected phrases with __AND__ token
    so splitting on ' and ' or '&' doesn't break them.
    """
    s = raw_lower
    for ph in phrases:
        ph = (ph or "").strip().lower()
        if not ph:
            continue

        variants = {ph, ph.replace(" & ", " and "), ph.replace(" and ", " & ")}
        for v in variants:
            if " and " not in v and " & " not in v:
                continue
            protected = v.replace(" and ", _AND_TOKEN.strip()).replace(" & ", _AND_TOKEN.strip())
            s = s.replace(v, protected)
    return s


def _unprotect_and(s: str) -> str:
    return (s or "").replace(_AND_TOKEN.strip(), "and")


def _parse_qty_prefix(msg: str) -> Tuple[int, str]:
    m = _QTY_RE.match(msg or "")
    if not m:
        return 1, (msg or "").strip()
    qty = max(1, int(m.group(1)))
    rest = (m.group(2) or "").strip()
    return qty, rest


def _split_intents(msg_norm: str) -> List[str]:
    parts = [p.strip() for p in _SPLIT_RE.split(msg_norm) if p and p.strip()]
    parts = [_unprotect_and(p) for p in parts]
    return parts or [_unprotect_and(msg_norm)]


def _is_greeting_only(msg_norm: str) -> bool:
    return bool(msg_norm) and msg_norm in _GREETINGS


def _fuzzy_best_key(keys: List[str], query: str, cutoff: float = 0.72) -> Optional[str]:
    if not query or not keys:
        return None
    q = query.strip().lower()

    if q in keys:
        return q

    for k in keys:
        if q and q in k:
            return k

    matches = difflib.get_close_matches(q, keys, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def _extract_price_delta(choice_text: str) -> float:
    if not choice_text:
        return 0.0
    m = _PRICE_DELTA_RE.search(choice_text)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except Exception:
        return 0.0


# ----------------------------
# Menu indexing (new schema + back-compat)
# ----------------------------

def _currency_symbol(menu: Dict[str, Any]) -> str:
    cur = ((menu.get("meta") or {}).get("currency") or "GBP").upper()
    return "£" if cur == "GBP" else ""


def _menu_synonyms(menu: Dict[str, Any]) -> Dict[str, str]:
    meta = menu.get("meta") or {}
    custom = meta.get("synonyms") or {}
    merged = dict(_DEFAULT_SYNONYMS)
    if isinstance(custom, dict):
        merged.update({str(k).lower(): str(v).lower() for k, v in custom.items()})
    return merged


def _build_menu_index(menu: Dict[str, Any], synonyms: Dict[str, str]) -> Dict[str, Any]:
    """
    New schema:
      - categories: [{id, name}]
      - items: [{id, name, category_id, base_price, modifiers[], extras[] }]

    Back-compat:
      - categories: [{name, items:[{id,name,base_price, options{}, extras[] }]}]
    """
    idx: Dict[str, Any] = {}

    cats_by_id: Dict[str, Dict[str, Any]] = {}
    cats_by_name_raw: Dict[str, Dict[str, Any]] = {}
    cats_by_name_norm: Dict[str, Dict[str, Any]] = {}
    cats_by_name_norm_syn: Dict[str, Dict[str, Any]] = {}

    items_by_id: Dict[str, Dict[str, Any]] = {}

    # Item name indexes (multiple “views”)
    items_by_name_raw: Dict[str, Dict[str, Any]] = {}
    items_by_name_norm: Dict[str, Dict[str, Any]] = {}
    items_by_name_norm_syn: Dict[str, Dict[str, Any]] = {}

    def _index_item(it: Dict[str, Any]) -> None:
        iid = (it.get("id") or "").strip()
        nm = (it.get("name") or "").strip()
        if iid:
            items_by_id[iid] = it
        if nm:
            items_by_name_raw[nm.lower()] = it
            items_by_name_norm[_normalize_text(nm, {})] = it
            items_by_name_norm_syn[_normalize_text(nm, synonyms)] = it

    categories = menu.get("categories") or []
    if isinstance(categories, list):
        for c in categories:
            if not isinstance(c, dict):
                continue
            cid = (c.get("id") or "").strip()
            cname = (c.get("name") or "").strip()
            if cid:
                cats_by_id[cid] = c
            if cname:
                cats_by_name_raw[cname.lower()] = c
                cats_by_name_norm[_normalize_text(cname, {})] = c
                cats_by_name_norm_syn[_normalize_text(cname, synonyms)] = c

            # back-compat nested items
            for it in (c.get("items") or []):
                if isinstance(it, dict):
                    _index_item(it)

    items = menu.get("items") or []
    if isinstance(items, list) and items:
        for it in items:
            if isinstance(it, dict):
                _index_item(it)

    idx["cats_by_id"] = cats_by_id
    idx["cats_by_name_raw"] = cats_by_name_raw
    idx["cats_by_name_norm"] = cats_by_name_norm
    idx["cats_by_name_norm_syn"] = cats_by_name_norm_syn

    idx["items_by_id"] = items_by_id
    idx["items_by_name_raw"] = items_by_name_raw
    idx["items_by_name_norm"] = items_by_name_norm
    idx["items_by_name_norm_syn"] = items_by_name_norm_syn

    # For splitting protection: collect item names that contain " and " or " & "
    idx["and_phrases"] = [nm for nm in items_by_name_raw.keys() if (" and " in nm) or (" & " in nm)]

    menu["_index"] = idx
    return menu


def _all_category_names(menu: Dict[str, Any]) -> List[str]:
    cats = menu.get("categories") or []
    out: List[str] = []
    for c in cats:
        if not isinstance(c, dict):
            continue
        n = (c.get("name") or "").strip()
        if n:
            out.append(n)
    return out


def _items_in_category(menu: Dict[str, Any], category_id_or_name: str) -> List[Dict[str, Any]]:
    idx = (menu.get("_index") or {})
    items = list((idx.get("items_by_id") or {}).values())

    cid = (category_id_or_name or "").strip()
    if not cid:
        return []

    cats_by_id = idx.get("cats_by_id") or {}
    cats_by_name_raw = idx.get("cats_by_name_raw") or {}
    cats_by_name_norm = idx.get("cats_by_name_norm") or {}
    cats_by_name_norm_syn = idx.get("cats_by_name_norm_syn") or {}

    # resolve category
    cat_obj = (
        cats_by_id.get(cid)
        or cats_by_name_raw.get(cid.lower())
        or cats_by_name_norm.get(_normalize_text(cid, {}))
        or cats_by_name_norm_syn.get(_normalize_text(cid, _menu_synonyms(menu)))
    )
    if cat_obj:
        cid = (cat_obj.get("id") or "").strip() or cid

    out: List[Dict[str, Any]] = []
    for it in items:
        if (it.get("category_id") or "").strip() == cid:
            out.append(it)

    # back-compat nested items
    if not out and cat_obj and cat_obj.get("items"):
        out = list(cat_obj.get("items") or [])

    return out


def _find_item_in_text(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[Dict[str, Any]]:
    idx = menu.get("_index") or {}
    raw_q = (text or "").strip().lower()
    norm_q = _normalize_text(text, {})
    syn_q = _normalize_text(text, synonyms)

    def _try(lookup: Dict[str, Dict[str, Any]], q: str, cutoff: float) -> Optional[Dict[str, Any]]:
        if not q or not lookup:
            return None
        keys = list(lookup.keys())
        best = _fuzzy_best_key(keys, q, cutoff=cutoff)
        return lookup.get(best) if best else None

    # raw: most strict
    hit = _try(idx.get("items_by_name_raw") or {}, raw_q, cutoff=0.80)
    if hit:
        return hit

    # normalized without synonyms
    hit = _try(idx.get("items_by_name_norm") or {}, norm_q, cutoff=0.62)
    if hit:
        return hit

    # normalized with synonyms
    return _try(idx.get("items_by_name_norm_syn") or {}, syn_q, cutoff=0.62)


def _find_items_by_keyword(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> List[Dict[str, Any]]:
    idx = menu.get("_index") or {}

    queries = []
    raw_q = (text or "").strip().lower()
    if raw_q:
        queries.append(raw_q)

    norm_q = _normalize_text(text, {})
    if norm_q and norm_q not in queries:
        queries.append(norm_q)

    syn_q = _normalize_text(text, synonyms)
    if syn_q and syn_q not in queries:
        queries.append(syn_q)

    lookups = [
        idx.get("items_by_name_raw") or {},
        idx.get("items_by_name_norm") or {},
        idx.get("items_by_name_norm_syn") or {},
    ]

    out: List[Dict[str, Any]] = []
    for q in queries:
        for lookup in lookups:
            for nm, it in lookup.items():
                if q and q in nm and it not in out:
                    out.append(it)
    return out


def _find_category_in_text(menu: Dict[str, Any], text: str, synonyms: Dict[str, str]) -> Optional[Dict[str, Any]]:
    t_raw = (text or "").strip()
    if not t_raw:
        return None

    idx = menu.get("_index") or {}
    candidates = [
        (idx.get("cats_by_name_raw") or {}, t_raw.lower(), 0.70),
        (idx.get("cats_by_name_norm") or {}, _normalize_text(t_raw, {}), 0.70),
        (idx.get("cats_by_name_norm_syn") or {}, _normalize_text(t_raw, synonyms), 0.70),
    ]

    for lookup, q, cutoff in candidates:
        if not q:
            continue
        keys = list(lookup.keys())
        best = _fuzzy_best_key(keys, q, cutoff=cutoff)
        if best:
            return lookup.get(best)
    return None


# ----------------------------
# Conversation intent helpers (menu-only)
# ----------------------------

def _is_continue_order(msg_norm: str) -> bool:
    if not msg_norm:
        return False
    if msg_norm in _CONTINUE_ORDER:
        return True
    if msg_norm.startswith("and "):
        return True
    if msg_norm.startswith("add "):
        return True
    return False


def _concept_from_message(msg_norm: str) -> Optional[str]:
    if "drink" in msg_norm or "beverage" in msg_norm:
        return "drinks"
    if "side" in msg_norm or "extra" in msg_norm:
        return "sides"
    if "dessert" in msg_norm or "sweet" in msg_norm or "pudding" in msg_norm:
        return "desserts"
    return None


def _find_category_by_concept(menu: Dict[str, Any], concept: str, synonyms: Dict[str, str]) -> Optional[Dict[str, Any]]:
    for alias in CATEGORY_ALIASES.get(concept, []):
        cat = _find_category_in_text(menu, alias, synonyms)
        if cat:
            return cat
    return None


def _render_category(menu: Dict[str, Any], cat: Dict[str, Any], cur: str) -> str:
    items = _items_in_category(menu, (cat.get("id") or cat.get("name") or ""))
    lines: List[str] = []
    for it in items[:25]:
        name = it.get("name")
        price = it.get("base_price")
        if name is not None and price is not None:
            lines.append(f"- {name} ({cur}{float(price):.2f})")
        elif name is not None:
            lines.append(f"- {name}")
    if not lines:
        return f"{cat.get('name')} has no items yet."
    return f"{cat.get('name')} options:\n" + "\n".join(lines) + "\n\nTell me which one you want."


def _next_prompt(menu: Dict[str, Any]) -> str:
    cats = _all_category_names(menu)
    if cats:
        return f"Anything else? You can say a category (e.g. {cats[0]}), an item name, or “confirm”."
    return "Anything else? Say an item name, or “confirm”."


# ----------------------------
# Cart / State
# ----------------------------

def _load_cart(items_json: str | None) -> List[Dict[str, Any]]:
    try:
        v = json.loads(items_json or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _dump_cart(cart: List[Dict[str, Any]]) -> str:
    return json.dumps(cart, ensure_ascii=False)


def _load_state(state_json: str | None) -> Dict[str, Any]:
    try:
        v = json.loads(state_json or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _dump_state(state: Dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False)


def _recalc_line_total(line: Dict[str, Any]) -> None:
    qty = int(line.get("qty", 1) or 1)
    base = float(line.get("base_price", 0.0) or 0.0)

    extras_total = 0.0
    for e in (line.get("extras") or []):
        try:
            extras_total += float(e.get("price", 0.0) or 0.0)
        except Exception:
            pass

    mods_total = 0.0
    deltas = line.get("choice_price_deltas") or {}
    if isinstance(deltas, dict):
        for v in deltas.values():
            try:
                mods_total += float(v or 0.0)
            except Exception:
                pass

    line["line_total"] = round(qty * (base + mods_total + extras_total), 2)


def _cart_total(cart: List[Dict[str, Any]]) -> float:
    total = 0.0
    for line in cart:
        try:
            total += float(line.get("line_total", 0.0) or 0.0)
        except Exception:
            pass
    return round(total, 2)


def build_summary(cart: List[Dict[str, Any]], currency_symbol: str = "£") -> Tuple[str, float]:
    if not cart:
        return ("Your basket is empty.", 0.0)

    lines: List[str] = []
    for i, line in enumerate(cart, start=1):
        name = line.get("name", "Item")
        qty = int(line.get("qty", 1) or 1)
        choices = line.get("choices") or {}
        extras = line.get("extras") or []
        notes = line.get("notes") or ""
        line_total = float(line.get("line_total", 0.0) or 0.0)

        bits: List[str] = []
        if choices:
            def _fmt(v: Any) -> str:
                if isinstance(v, list):
                    return ", ".join([str(x) for x in v if x])
                return str(v)

            bits.append(" | ".join([f"{k}: {_fmt(v)}" for k, v in choices.items() if v]))

        if extras:
            bits.append("extras: " + ", ".join([e["name"] for e in extras if "name" in e]))
        if notes:
            bits.append(f"note: {notes}")

        detail = f" ({'; '.join(bits)})" if bits else ""
        lines.append(f"{i}. x{qty} {name}{detail} = {currency_symbol}{line_total:.2f}")

    total = _cart_total(cart)
    return ("Order summary:\n" + "\n".join(lines) + f"\n\nTotal: {currency_symbol}{total:.2f}", total)


# ----------------------------
# Modifiers / Extras matching
# ----------------------------

def _match_choice(options: List[str], msg: str, synonyms: Dict[str, str]) -> Optional[str]:
    m = _normalize_text(msg, synonyms)
    if not m:
        return None

    for opt in options:
        o = _normalize_text(opt or "", synonyms)
        if o == m:
            return opt

    for opt in options:
        o = _normalize_text(opt or "", synonyms)
        if o and o in m:
            return opt

    opts_norm = [_normalize_text(o or "", synonyms) for o in options if o]
    best = _fuzzy_best_key(opts_norm, m, cutoff=0.65)
    if not best:
        return None

    for opt in options:
        if _normalize_text(opt or "", synonyms) == best:
            return opt
    return None


def _match_multi_choices(options: List[str], msg: str, synonyms: Dict[str, str]) -> List[str]:
    m = _normalize_text(msg, synonyms)
    if not m:
        return []
    parts = _split_intents(m)
    picked: List[str] = []
    for p in parts:
        ch = _match_choice(options, p, synonyms)
        if ch and ch not in picked:
            picked.append(ch)
    return picked


def _match_extra(extras: List[Dict[str, Any]], msg: str, synonyms: Dict[str, str]) -> Optional[Dict[str, Any]]:
    m = _normalize_text(msg, synonyms)
    if not m:
        return None

    names = [(e.get("name") or "").strip() for e in extras]
    names_l = [_normalize_text(n, synonyms) for n in names if n]

    best = _fuzzy_best_key(names_l, m, cutoff=0.66)
    if not best:
        return None
    for e in extras:
        if _normalize_text((e.get("name") or "").strip(), synonyms) == best:
            return e
    return None


def _get_item_modifiers(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    mods = item.get("modifiers")
    if isinstance(mods, list) and mods:
        out: List[Dict[str, Any]] = []
        for m in mods:
            if not isinstance(m, dict):
                continue
            key = (m.get("key") or "").strip()
            if not key:
                continue
            out.append({
                "key": key,
                "prompt": (m.get("prompt") or f"Choose {key}:").strip(),
                "required": bool(m.get("required", True)),
                "multi": bool(m.get("multi", False)),
                "options": list(m.get("options") or []),
            })
        return out

    # Back-compat "options" dict
    opts = item.get("options")
    if isinstance(opts, dict) and opts:
        out = []
        for key, options in opts.items():
            out.append({
                "key": str(key),
                "prompt": f"What {key} do you want?",
                "required": True,
                "multi": False,
                "options": list(options or []),
            })
        return out

    return []


# ----------------------------
# Dynamic help / fallback
# ----------------------------

def _dynamic_help(menu: Dict[str, Any]) -> str:
    cats = _all_category_names(menu)
    idx = menu.get("_index") or {}
    items_by_id = idx.get("items_by_id") or {}

    examples: List[str] = []
    for it in list(items_by_id.values())[:6]:
        n = (it.get("name") or "").strip()
        if n:
            examples.append(n)

    lines = [
        "I didn’t catch that. Try:",
        "- “menu”",
    ]
    for c in cats[:3]:
        lines.append(f"- “{c.lower()}”")

    if examples:
        lines.append(f"- “{examples[0]}”")
    if len(examples) >= 2:
        lines.append(f"- “2x {examples[1]}”")

    lines += [
        "- “basket”",
        "- “remove <item>”",
        "- “confirm”",
    ]
    return "\n".join(lines)


# ----------------------------
# Power-user: queue remaining parts in one message
# ----------------------------

def _queue_pending_parts(state: Dict[str, Any], remaining_parts: List[str]) -> None:
    if not remaining_parts:
        return
    existing = state.get("pending_parts")
    if not isinstance(existing, list):
        existing = []
    state["pending_parts"] = existing + remaining_parts


def _pop_pending_part(state: Dict[str, Any]) -> Optional[str]:
    pending = state.get("pending_parts")
    if not isinstance(pending, list) or not pending:
        return None
    nxt = str(pending.pop(0)).strip()
    if pending:
        state["pending_parts"] = pending
    else:
        state.pop("pending_parts", None)
    return nxt if nxt else None


# ----------------------------
# Main brain (pure function)
# ----------------------------

def handle_message(
    message: str,
    items_json: str,
    menu_dict: Dict[str, Any],
    state_json: str = "{}",
) -> Tuple[str, str, str]:
    synonyms = _menu_synonyms(menu_dict)
    menu = _build_menu_index(menu_dict, synonyms)
    cur = _currency_symbol(menu)

    msg_raw = (message or "").strip()
    msg_norm = _normalize_text(msg_raw, synonyms)

    cart = _load_cart(items_json)
    state = _load_state(state_json)

    # ----------------------------
    # Commands
    # ----------------------------
    if msg_norm in {"basket", "cart", "summary", "my order", "whats my order", "what s my order", "what's my order"}:
        summary, _ = build_summary(cart, currency_symbol=cur)
        return summary, _dump_cart(cart), _dump_state(state)

    if msg_norm in {"confirm", "place order", "checkout"}:
        if not cart:
            return ("Your basket is empty. Tell me what you want first 🙂", _dump_cart(cart), _dump_state(state))
        summary, _ = build_summary(cart, currency_symbol=cur)
        return (summary + "\n\nIf that looks right, hit Confirm in the app.", _dump_cart(cart), _dump_state(state))

    if msg_norm.startswith("remove ") or msg_norm.startswith("delete "):
        target_text = msg_norm.split(" ", 1)[1].strip()
        target = _find_item_in_text(menu, target_text, synonyms)
        if not target:
            return ("Tell me which item to remove (e.g. “remove Egg Fried Rice”)."), _dump_cart(cart), _dump_state(state)

        target_id = target.get("id")
        removed_any = False

        new_cart: List[Dict[str, Any]] = []
        for ln in cart:
            if not removed_any and target_id and ln.get("item_id") == target_id:
                qty = int(ln.get("qty", 1) or 1)
                if qty > 1:
                    ln["qty"] = qty - 1
                    _recalc_line_total(ln)
                    new_cart.append(ln)
                # if qty == 1, drop the line
                removed_any = True
                continue
            new_cart.append(ln)

        if not removed_any:
            return ("That item isn’t in your basket."), _dump_cart(cart), _dump_state(state)

        cart = new_cart
        summary, _ = build_summary(cart, currency_symbol=cur)
        return ("Removed it.\n\n" + summary + "\n\n" + _next_prompt(menu)), _dump_cart(cart), _dump_state(state)

    if msg_norm in {"menu", "show menu", "what do you have", "what do you have?", "what have you got"} or "what do you have" in msg_norm:
        cats = _all_category_names(menu)
        if cats:
            return ("We have: " + ", ".join(cats) + ".\nWhich category do you want?"), _dump_cart(cart), _dump_state(state)
        return ("Tell me what you’d like (e.g. the name of an item)."), _dump_cart(cart), _dump_state(state)

    if _is_greeting_only(msg_norm):
        cats = _all_category_names(menu)
        if cats:
            return ("Hey! What can I get you?\nWe’ve got: " + ", ".join(cats) + "."), _dump_cart(cart), _dump_state(state)
        return ("Hey! What can I get you?"), _dump_cart(cart), _dump_state(state)

    # ----------------------------
    # Natural question: "what <category> do you have?"
    # ----------------------------
    m = re.search(r"\bwhat\s+(.+?)\s+(?:do you have|have you got|are there|is there)\??\b", msg_norm)
    if m:
        wanted = m.group(1).strip()
        catq = _find_category_in_text(menu, wanted, synonyms)
        if catq:
            return _render_category(menu, catq, cur), _dump_cart(cart), _dump_state(state)

        cats = _all_category_names(menu)
        if cats:
            return ("We have: " + ", ".join(cats) + ".\nWhich category do you want?"), _dump_cart(cart), _dump_state(state)

    # ----------------------------
    # If user typed a category name
    # ----------------------------
    cat = _find_category_in_text(menu, msg_norm, synonyms)
    if cat:
        return _render_category(menu, cat, cur), _dump_cart(cart), _dump_state(state)

    # ----------------------------
    # Mid-configuration: awaiting modifier
    # ----------------------------
    if state.get("mode") == "awaiting_modifier":
        line_index = int(state.get("line_index", -1))
        mod_index = int(state.get("mod_index", -1))

        if 0 <= line_index < len(cart):
            item_id = cart[line_index].get("item_id")
            item = (menu.get("_index") or {}).get("items_by_id", {}).get(item_id)
            if item:
                mods = _get_item_modifiers(item)
                if 0 <= mod_index < len(mods):
                    mod = mods[mod_index]
                    options = list(mod.get("options") or [])

                    if mod.get("multi"):
                        picked = _match_multi_choices(options, msg_raw, synonyms)
                        if not picked:
                            return (
                                "I need one or more of these options: " + ", ".join(options),
                                _dump_cart(cart),
                                _dump_state(state),
                            )
                        cart[line_index].setdefault("choices", {})
                        cart[line_index]["choices"][mod["key"]] = picked
                        cart[line_index].setdefault("choice_price_deltas", {})
                        cart[line_index]["choice_price_deltas"][mod["key"]] = sum(_extract_price_delta(x) for x in picked)
                    else:
                        chosen = _match_choice(options, msg_raw, synonyms)
                        if not chosen:
                            return (
                                "I need one of these options: " + ", ".join(options),
                                _dump_cart(cart),
                                _dump_state(state),
                            )
                        cart[line_index].setdefault("choices", {})
                        cart[line_index]["choices"][mod["key"]] = chosen
                        cart[line_index].setdefault("choice_price_deltas", {})
                        cart[line_index]["choice_price_deltas"][mod["key"]] = _extract_price_delta(chosen)

                    _recalc_line_total(cart[line_index])

                    next_idx = mod_index + 1
                    while next_idx < len(mods) and not mods[next_idx].get("required", True):
                        next_idx += 1

                    if next_idx < len(mods):
                        state.update({"mode": "awaiting_modifier", "line_index": line_index, "mod_index": next_idx})
                        nxt = mods[next_idx]
                        return (
                            f"Got it. {nxt.get('prompt')}\nOptions: " + ", ".join(nxt.get("options") or []),
                            _dump_cart(cart),
                            _dump_state(state),
                        )

                    extras = item.get("extras") or []
                    if extras:
                        state.update({"mode": "awaiting_extras", "line_index": line_index})
                        extra_lines = [
                            f"- {e['name']} ({cur}{float(e.get('price',0.0)):.2f})"
                            for e in extras if e.get("name")
                        ]
                        return (
                            "Any extras?\n" + "\n".join(extra_lines) + "\n\nSay an extra name, or “no extras”.",
                            _dump_cart(cart),
                            _dump_state(state),
                        )

                    # finished item, continue pending
                    state.pop("mode", None)
                    state.pop("line_index", None)
                    state.pop("mod_index", None)

                    nxt_part = _pop_pending_part(state)
                    if nxt_part:
                        summary, _ = build_summary(cart, currency_symbol=cur)
                        reply_prefix = "Nice.\n\n" + summary
                        r, items2, state2 = handle_message(nxt_part, _dump_cart(cart), menu_dict, _dump_state(state))
                        return reply_prefix + "\n\n" + r, items2, state2

                    summary, _ = build_summary(cart, currency_symbol=cur)
                    return "Nice.\n\n" + summary + "\n\n" + _next_prompt(menu), _dump_cart(cart), _dump_state(state)

        state = {}

    # ----------------------------
    # Mid-configuration: awaiting extras
    # ----------------------------
    if state.get("mode") == "awaiting_extras":
        line_index = int(state.get("line_index", -1))

        if msg_norm in _NO_EXTRAS:
            # IMPORTANT: do not wipe the whole state or we lose pending_parts
            state.pop("mode", None)
            state.pop("line_index", None)

            nxt_part = _pop_pending_part(state)
            if nxt_part:
                summary, _ = build_summary(cart, currency_symbol=cur)
                reply_prefix = "All good. No extras.\n\n" + summary
                r, items2, state2 = handle_message(nxt_part, _dump_cart(cart), menu_dict, _dump_state(state))
                return reply_prefix + "\n\n" + r, items2, state2

            summary, _ = build_summary(cart, currency_symbol=cur)
            return "All good. No extras.\n\n" + summary + "\n\n" + _next_prompt(menu), _dump_cart(cart), _dump_state(state)

        if 0 <= line_index < len(cart):
            item_id = cart[line_index].get("item_id")
            item = (menu.get("_index") or {}).get("items_by_id", {}).get(item_id)
            if item:
                extras = item.get("extras") or []
                chosen_extra = _match_extra(extras, msg_raw, synonyms)
                if chosen_extra:
                    cart[line_index].setdefault("extras", [])
                    cart[line_index]["extras"].append(chosen_extra)
                    _recalc_line_total(cart[line_index])
                    return (
                        f"Added extra: {chosen_extra.get('name')}. Add another extra, or say “no extras”.",
                        _dump_cart(cart),
                        _dump_state(state),
                    )

                names = [e.get("name") for e in extras if e.get("name")]
                return (
                    "Extras available: " + ", ".join(names) + ". Or say “no extras”.",
                    _dump_cart(cart),
                    _dump_state(state),
                )

        state = {}

    # ----------------------------
    # Add item flow (supports multi-part input)
    # ----------------------------

    # 1) strip filler
    cleaned_raw = _strip_filler_prefix(msg_raw)
    cleaned_lower = cleaned_raw.lower()

    # 2) protect "and/&" inside known phrases + common UK Chinese/takeaway combos
    idx = menu.get("_index") or {}
    menu_and_phrases = list(idx.get("and_phrases") or [])
    common_combos = [
        "salt and pepper", "salt & pepper",
        "sweet and sour", "sweet & sour",
        "hot and sour", "hot & sour",
    ]
    protected = _protect_and_phrases(cleaned_lower, menu_and_phrases + common_combos)

    # 3) normalize AFTER protection (so splitting works)
    msg_for_split = _normalize_text(protected, synonyms)

    parts = _split_intents(msg_for_split)
    added_any = False

    # We no longer use pending_parts here because we process ALL parts in one go.
    # (pending_parts is still used in the modifier/extras modes.)
    for part in parts:
        qty, text = _parse_qty_prefix(part)
        item = _find_item_in_text(menu, text, synonyms)

        if not item:
            matches = _find_items_by_keyword(menu, text, synonyms)
            if len(matches) > 1:
                options = [
                    f"- {m.get('name')} ({cur}{float(m.get('base_price', 0)):.2f})"
                    for m in matches[:8]
                ]
                return (
                    "Which one did you mean?\n" + "\n".join(options),
                    _dump_cart(cart),
                    _dump_state(state),
                )

            # If they typed a category (and we haven't added anything yet), show it
            cat2 = _find_category_in_text(menu, text, synonyms)
            if cat2 and not added_any:
                return _render_category(menu, cat2, cur), _dump_cart(cart), _dump_state(state)

            continue

        base_price = float(item.get("base_price", 0.0) or 0.0)
        new_line = {
            "item_id": item.get("id"),
            "name": item.get("name"),
            "qty": qty,
            "base_price": base_price,
            "choices": {},
            "choice_price_deltas": {},
            "extras": [],
            "notes": "",
            "line_total": 0.0,
        }
        _recalc_line_total(new_line)
        cart.append(new_line)
        added_any = True

        # Mark what this line still needs (but DO NOT start configuration yet)
        mods = _get_item_modifiers(item)
        if mods:
            first_required = 0
            while first_required < len(mods) and not mods[first_required].get("required", True):
                first_required += 1
            if first_required < len(mods):
                new_line["_needs_modifiers"] = True
                new_line["_first_mod_index"] = first_required

        extras = item.get("extras") or []
        if extras:
            new_line["_needs_extras"] = True

    # If we added anything, start configuring the FIRST incomplete line
    if added_any:
        # 1) Modifiers take priority
        for i, ln in enumerate(cart):
            if ln.get("_needs_modifiers"):
                item_id = ln.get("item_id")
                item_obj = (menu.get("_index") or {}).get("items_by_id", {}).get(item_id)
                if not item_obj:
                    # can't configure, skip marker
                    ln.pop("_needs_modifiers", None)
                    ln.pop("_first_mod_index", None)
                    continue

                mods = _get_item_modifiers(item_obj)
                mod_index = int(ln.get("_first_mod_index", 0) or 0)
                if mods and 0 <= mod_index < len(mods):
                    # clear markers once we enter config mode
                    ln.pop("_needs_modifiers", None)
                    ln.pop("_first_mod_index", None)

                    state.update({"mode": "awaiting_modifier", "line_index": i, "mod_index": mod_index})
                    mod = mods[mod_index]
                    return (
                        f"Nice. For your {item_obj.get('name')}, {mod.get('prompt')}\nOptions: "
                        + ", ".join(mod.get("options") or []),
                        _dump_cart(cart),
                        _dump_state(state),
                    )

                # nothing valid to ask, clear marker and continue
                ln.pop("_needs_modifiers", None)
                ln.pop("_first_mod_index", None)

        # 2) Then extras (for lines that have extras but no required modifiers left)
        for i, ln in enumerate(cart):
            if ln.get("_needs_extras"):
                item_id = ln.get("item_id")
                item_obj = (menu.get("_index") or {}).get("items_by_id", {}).get(item_id)
                if not item_obj:
                    ln.pop("_needs_extras", None)
                    continue

                extras = item_obj.get("extras") or []
                if extras:
                    ln.pop("_needs_extras", None)
                    state.update({"mode": "awaiting_extras", "line_index": i})
                    extra_lines = [
                        f"- {e['name']} ({cur}{float(e.get('price', 0.0)):.2f})"
                        for e in extras if e.get("name")
                    ]
                    return (
                        "Any extras?\n" + "\n".join(extra_lines) + "\n\nSay an extra name, or “no extras”.",
                        _dump_cart(cart),
                        _dump_state(state),
                    )

                ln.pop("_needs_extras", None)

        # 3) If nothing needs config, just summarize
        summary, _ = build_summary(cart, currency_symbol=cur)
        return "Got it.\n\n" + summary + "\n\n" + _next_prompt(menu), _dump_cart(cart), _dump_state(state)

    # ----------------------------
    # Continuation phrases
    # ----------------------------
    if _is_continue_order(msg_norm):
        concept = _concept_from_message(msg_norm)
        if concept:
            catc = _find_category_by_concept(menu, concept, synonyms)
            if catc:
                return _render_category(menu, catc, cur), _dump_cart(cart), _dump_state(state)

        cats = _all_category_names(menu)
        if cats:
            return (
                "No problem. What would you like to add?\nCategories: " + ", ".join(cats),
                _dump_cart(cart),
                _dump_state(state),
            )
        return "No problem. Tell me what item you want to add.", _dump_cart(cart), _dump_state(state)

    return _dynamic_help(menu), _dump_cart(cart), _dump_state(state)
