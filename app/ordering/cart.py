# app/ordering/cart.py
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple


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


def recalc_line_total(line: Dict[str, Any]) -> None:
    qty = int(line.get("qty", 1) or 1)
    base = float(line.get("base_price", 0.0) or 0.0)
    line["line_total"] = round(qty * base, 2)


def cart_total(cart: List[Dict[str, Any]]) -> float:
    total = 0.0
    for x in cart:
        try:
            total += float(x.get("line_total", 0.0) or 0.0)
        except Exception:
            pass
    return round(total, 2)


def build_summary(cart: List[Dict[str, Any]], currency_symbol: str = "£") -> Tuple[str, float]:
    if not cart:
        return ("Your basket is empty.", 0.0)

    lines: List[str] = []
    for i, line in enumerate(cart, start=1):
        qty = int(line.get("qty", 1) or 1)
        name = str(line.get("name", "Item"))
        lt = float(line.get("line_total", 0.0) or 0.0)
        lines.append(f"{i}. x{qty} {name} = {currency_symbol}{lt:.2f}")

    total = cart_total(cart)
    return ("Order summary:\n" + "\n".join(lines) + f"\n\nTotal: {currency_symbol}{total:.2f}", total)