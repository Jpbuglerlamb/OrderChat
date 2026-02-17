# app/ai_intent.py
from __future__ import annotations
import os
import json
from typing import Any, Dict
from openai import OpenAI

client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY")
)

SYSTEM = """You are an intent parser for a takeaway ordering app.
Convert the user's message into ONE JSON command that matches the provided JSON schema.
Rules:
- Never invent menu items. If unsure, use intent "unknown".
- If the system is awaiting an option/extras (state.mode), interpret short answers like "large" or "no".
- Keep it concise and robust to typos/slang.
"""

# JSON Schema for Structured Outputs
COMMAND_SCHEMA: Dict[str, Any] = {
    "name": "takeaway_command",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "intent": {
                "type": "string",
                "enum": [
                    "show_menu",
                    "show_basket",
                    "show_category",
                    "add_item",
                    "remove_item",
                    "choose_option",
                    "add_extra",
                    "no_extras",
                    "confirm",
                    "unknown",
                ],
            },
            "category": {"type": ["string", "null"]},
            "item_name": {"type": ["string", "null"]},
            "qty": {"type": ["integer", "null"], "minimum": 1},
            "option_value": {"type": ["string", "null"]},
            "extra_name": {"type": ["string", "null"]},
        },
        "required": ["intent", "category", "item_name", "qty", "option_value", "extra_name"],
    },
    "strict": True,
}

def _menu_hints(menu: Dict[str, Any]) -> Dict[str, Any]:
    # Keep hints small to control cost + latency.
    cats = []
    items = []
    for c in menu.get("categories", []) or []:
        cname = c.get("name")
        if cname:
            cats.append(cname)
        for it in c.get("items", []) or []:
            items.append({
                "name": it.get("name"),
                "options": list((it.get("options") or {}).keys()),
                "extras": [e.get("name") for e in (it.get("extras") or [])],
            })
    return {"categories": cats, "items": items[:120]}  # cap

async def interpret_message_llm(message: str, menu: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "message": message,
        "state": state,
        "menu_hints": _menu_hints(menu),
    }

    resp = await client.responses.create(
        model="gpt-5-mini",  # fast/cheap; swap to gpt-5 for harder edge cases
        input=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        # Structured Outputs: forces schema correctness
        response_format={"type": "json_schema", "json_schema": COMMAND_SCHEMA},
    )

    # Responses API gives you a convenience accessor:
    cmd = json.loads(resp.output_text)
    return cmd


