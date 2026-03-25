# app/ordering/cart.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple


_PRICE_DELTA_RE = re.compile(
    r"\(\s*\+?\s*£?\s*(\d+(?:\.\d{1,2})?)\s*\)",
    re.IGNORECASE,
)


def load_cart(items_json: str | None) -> List[Dict[str, Any]]:
    try:
        value = json.loads(items_json or "[]")
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]
    except Exception:
        return []


def dump_cart(cart: List[Dict[str, Any]]) -> str:
    return json.dumps(cart or [], ensure_ascii=False)


def load_state(state_json: str | None) -> Dict[str, Any]:
    try:
        value = json.loads(state_json or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def dump_state(state: Dict[str, Any]) -> str:
    return json.dumps(state or {}, ensure_ascii=False)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            cleaned = value.strip().replace("£", "").replace(",", "")
            if not cleaned:
                return default
            return float(cleaned)
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return default
            return int(float(cleaned))
        return int(value)
    except Exception:
        return default


def _extract_price_delta(value: Any) -> float:
    """
    Extract a price delta from strings like:
      'Large (+£2.00)'
      'Egg Fried Rice (+£1.50)'
      'Chips (+1.50)'
    Returns 0.0 if none found.
    """
    text = str(value or "").strip()
    if not text:
        return 0.0

    match = _PRICE_DELTA_RE.search(text)
    if not match:
        return 0.0

    try:
        return float(match.group(1))
    except Exception:
        return 0.0


def _choices_total(choices: Any) -> float:
    """
    choices can look like:
      {"size": "Large (+£2.00)", "side": "Egg Fried Rice (+£1.50)"}
      {"sauces": ["Curry (+£0.50)", "Garlic Mayo (+£0.50)"]}
    """
    if not isinstance(choices, dict):
        return 0.0

    total = 0.0

    for _key, value in choices.items():
        if isinstance(value, list):
            for v in value:
                total += _extract_price_delta(v)
        else:
            total += _extract_price_delta(value)

    return round(total, 2)


def _extras_total(extras: Any) -> float:
    """
    extras can look like:
      [{"name": "Extra Dip", "price": 0.70}]
    or cart-line selected extras with same shape.
    """
    if not isinstance(extras, list):
        return 0.0

    total = 0.0

    for extra in extras:
        if isinstance(extra, dict):
            total += _safe_float(extra.get("price"), 0.0)
        else:
            total += _extract_price_delta(extra)

    return round(total, 2)


def _normalize_choice_value(value: Any) -> Any:
    if isinstance(value, list):
        out: List[str] = []
        for v in value:
            text = str(v or "").strip()
            if text:
                out.append(text)
        return out

    text = str(value or "").strip()
    return text if text else ""


def _normalize_extras(extras: Any) -> List[Any]:
    if not isinstance(extras, list):
        return []

    out: List[Any] = []

    for extra in extras:
        if isinstance(extra, dict):
            name = str(extra.get("name") or "Extra").strip() or "Extra"
            price = round(_safe_float(extra.get("price"), 0.0), 2)
            out.append({"name": name, "price": price})
        else:
            text = str(extra or "").strip()
            if text:
                out.append(text)

    return out


def sanitize_line(line: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a cart line into the expected shape.
    Safe to call on partially broken / old cart data.
    """
    item_id = str(line.get("item_id") or "").strip()
    name = str(line.get("name") or line.get("title") or line.get("item") or "Item").strip() or "Item"
    qty = max(1, _safe_int(line.get("qty"), 1))
    base_price = round(_safe_float(line.get("base_price"), 0.0), 2)

    raw_choices = line.get("choices")
    choices: Dict[str, Any] = {}
    if isinstance(raw_choices, dict):
        for key, value in raw_choices.items():
            key_str = str(key or "").strip()
            if not key_str:
                continue
            normalized_value = _normalize_choice_value(value)
            if normalized_value == "" or normalized_value == []:
                continue
            choices[key_str] = normalized_value

    extras = _normalize_extras(line.get("extras"))

    clean_line: Dict[str, Any] = {
        "item_id": item_id,
        "name": name,
        "qty": qty,
        "base_price": base_price,
        "choices": choices,
        "extras": extras,
        "line_total": 0.0,
    }

    recalc_line_total(clean_line)
    return clean_line


def sanitize_cart(cart: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in cart or []:
        if not isinstance(line, dict):
            continue
        out.append(sanitize_line(line))
    return out


def recalc_line_total(line: Dict[str, Any]) -> None:
    qty = max(1, _safe_int(line.get("qty"), 1))
    base = _safe_float(line.get("base_price"), 0.0)
    choices_total = _choices_total(line.get("choices"))
    extras_total = _extras_total(line.get("extras"))

    per_item_total = max(0.0, base + choices_total + extras_total)
    line["qty"] = qty
    line["base_price"] = round(base, 2)
    line["line_total"] = round(qty * per_item_total, 2)


def cart_total(cart: List[Dict[str, Any]]) -> float:
    total = 0.0
    for line in cart or []:
        if not isinstance(line, dict):
            continue
        line_total = line.get("line_total")
        if line_total is None:
            temp = sanitize_line(line)
            total += _safe_float(temp.get("line_total"), 0.0)
        else:
            total += _safe_float(line_total, 0.0)
    return round(total, 2)


def _format_choices(choices: Any) -> List[str]:
    if not isinstance(choices, dict) or not choices:
        return []

    out: List[str] = []

    for key, value in choices.items():
        label = str(key or "").replace("_", " ").strip().title() or "Choice"

        if isinstance(value, list):
            joined = ", ".join(str(v).strip() for v in value if str(v).strip())
            if joined:
                out.append(f"   - {label}: {joined}")
        else:
            value_str = str(value or "").strip()
            if value_str:
                out.append(f"   - {label}: {value_str}")

    return out


def _format_extras(extras: Any, currency_symbol: str) -> List[str]:
    if not isinstance(extras, list) or not extras:
        return []

    out: List[str] = []

    for extra in extras:
        if isinstance(extra, dict):
            name = str(extra.get("name") or "Extra").strip() or "Extra"
            price = _safe_float(extra.get("price"), 0.0)
            out.append(f"   - Extra: {name} ({currency_symbol}{price:.2f})")
        else:
            text = str(extra or "").strip()
            if text:
                out.append(f"   - Extra: {text}")

    return out


def build_summary(cart: List[Dict[str, Any]], currency_symbol: str = "£") -> Tuple[str, float]:
    clean_cart = sanitize_cart(cart)

    if not clean_cart:
        return "Your basket is empty.", 0.0

    lines: List[str] = []

    for i, line in enumerate(clean_cart, start=1):
        qty = max(1, _safe_int(line.get("qty"), 1))
        name = str(line.get("name") or "Item").strip() or "Item"
        line_total = _safe_float(line.get("line_total"), 0.0)

        lines.append(f"{i}. x{qty} {name} = {currency_symbol}{line_total:.2f}")
        lines.extend(_format_choices(line.get("choices")))
        lines.extend(_format_extras(line.get("extras"), currency_symbol))

    total = cart_total(clean_cart)
    summary = "Order summary:\n" + "\n".join(lines) + f"\n\nTotal: {currency_symbol}{total:.2f}"
    return summary, total