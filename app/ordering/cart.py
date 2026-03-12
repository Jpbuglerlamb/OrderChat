# app/ordering/cart.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple


_PRICE_DELTA_RE = re.compile(r"\(\s*\+?\s*£?\s*(\d+(?:\.\d{1,2})?)\s*\)", re.IGNORECASE)


def load_cart(items_json: str | None) -> List[Dict[str, Any]]:
    try:
        v = json.loads(items_json or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def dump_cart(cart: List[Dict[str, Any]]) -> str:
    return json.dumps(cart, ensure_ascii=False)


def load_state(state_json: str | None) -> Dict[str, Any]:
    try:
        v = json.loads(state_json or "{}")
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def dump_state(state: Dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return default


def _extract_price_delta(value: Any) -> float:
    """
    Extracts a price delta from strings like:
      'Large (+£2.00)'
      'Egg Fried Rice (+£1.50)'
      'Chips (+1.50)'
    Returns 0.0 if none found.
    """
    text = str(value or "").strip()
    if not text:
        return 0.0

    m = _PRICE_DELTA_RE.search(text)
    if not m:
        return 0.0

    try:
        return float(m.group(1))
    except Exception:
        return 0.0


def _choices_total(choices: Any) -> float:
    """
    choices can look like:
      {"size": "Large (+£2.00)", "side": "Egg Fried Rice (+£1.50)"}
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


def recalc_line_total(line: Dict[str, Any]) -> None:
    qty = max(1, int(line.get("qty", 1) or 1))
    base = _safe_float(line.get("base_price"), 0.0)
    choices_total = _choices_total(line.get("choices"))
    extras_total = _extras_total(line.get("extras"))

    per_item_total = base + choices_total + extras_total
    line["line_total"] = round(qty * per_item_total, 2)


def cart_total(cart: List[Dict[str, Any]]) -> float:
    total = 0.0
    for x in cart:
        try:
            total += float(x.get("line_total", 0.0) or 0.0)
        except Exception:
            pass
    return round(total, 2)


def _format_choices(choices: Any) -> List[str]:
    if not isinstance(choices, dict) or not choices:
        return []

    out: List[str] = []
    for key, value in choices.items():
        label = str(key or "").replace("_", " ").strip().title() or "Choice"
        if isinstance(value, list):
            joined = ", ".join(str(v) for v in value if str(v).strip())
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
            name = str(extra.get("name") or "Extra").strip()
            price = _safe_float(extra.get("price"), 0.0)
            out.append(f"   - Extra: {name} ({currency_symbol}{price:.2f})")
        else:
            text = str(extra or "").strip()
            if text:
                out.append(f"   - Extra: {text}")
    return out


def build_summary(cart: List[Dict[str, Any]], currency_symbol: str = "£") -> Tuple[str, float]:
    if not cart:
        return ("Your basket is empty.", 0.0)

    lines: List[str] = []
    for i, line in enumerate(cart, start=1):
        qty = max(1, int(line.get("qty", 1) or 1))
        name = str(line.get("name", "Item")).strip() or "Item"
        lt = _safe_float(line.get("line_total"), 0.0)

        lines.append(f"{i}. x{qty} {name} = {currency_symbol}{lt:.2f}")

        choice_lines = _format_choices(line.get("choices"))
        extra_lines = _format_extras(line.get("extras"), currency_symbol)

        lines.extend(choice_lines)
        lines.extend(extra_lines)

    total = cart_total(cart)
    return ("Order summary:\n" + "\n".join(lines) + f"\n\nTotal: {currency_symbol}{total:.2f}", total)