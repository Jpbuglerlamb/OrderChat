# app/command_router.py
from __future__ import annotations
from typing import Any, Dict

def command_to_userlike_text(cmd: Dict[str, Any]) -> str:
    intent = (cmd.get("intent") or "unknown").strip()

    if intent == "show_menu":
        return "menu"

    if intent == "show_basket":
        return "basket"

    if intent == "show_category":
        cat = cmd.get("category") or ""
        return str(cat).strip() or "menu"

    if intent == "add_item":
        name = (cmd.get("item_name") or "").strip()
        qty = int(cmd.get("qty") or 1)
        if not name:
            return ""
        if qty <= 1:
            return name
        return f"{qty}x {name}"

    if intent == "remove_item":
        name = (cmd.get("item_name") or "").strip()
        return f"remove {name}" if name else ""

    if intent == "choose_option":
        val = (cmd.get("option_value") or "").strip()
        return val

    if intent == "add_extra":
        extra = (cmd.get("extra_name") or "").strip()
        return extra

    if intent == "no_extras":
        return "no extras"

    if intent == "confirm":
        return "confirm"

    return ""
