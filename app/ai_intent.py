# app/ai_intent.py
from __future__ import annotations

import json
import os
from typing import Any, Dict

from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM = """You are an intent parser for a takeaway ordering app.
Convert the user's message into ONE JSON command that matches the provided JSON schema.

Rules:
- Never invent menu items. If unsure, use intent "unknown".
- If the system is awaiting an option/extras (state.mode), interpret short answers like "large" or "no".
- Keep it concise and robust to typos/slang.
"""

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
    """
    Keep hints small to control cost + latency.
    Provide enough signal for the LLM to choose valid categories/items without inventing.
    """
    cats: list[str] = []
    items: list[dict] = []

    for c in (menu.get("categories") or []):
        cname = c.get("name")
        if cname:
            cats.append(str(cname))

        for it in (c.get("items") or []):
            items.append(
                {
                    "name": it.get("name"),
                    "category": cname,
                    "options": list((it.get("options") or {}).keys()),
                    "extras": [e.get("name") for e in (it.get("extras") or [])],
                }
            )

    return {"categories": cats, "items": items[:120]}  # cap


def _extract_response_text(resp: Any) -> str:
    """
    OpenAI Python SDK response shapes can vary by version.
    Try multiple ways to pull the generated text.
    """
    txt = getattr(resp, "output_text", None)
    if isinstance(txt, str) and txt.strip():
        return txt.strip()

    try:
        # Responses API sometimes stores text in output blocks
        out0 = resp.output[0]
        content0 = out0.content[0]
        text = getattr(content0, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:
        pass

    return ""


async def interpret_message_llm(message: str, menu: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns a dict matching COMMAND_SCHEMA.
    Raises on failure (caller decides whether to fall back).
    """
    payload = {
        "message": message,
        "state": state,
        "menu_hints": _menu_hints(menu),
    }

    resp = await client.responses.create(
        model=os.getenv("LLM_MODEL", "gpt-5-mini"),
        input=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_schema", "json_schema": COMMAND_SCHEMA},
    )

    text = _extract_response_text(resp)
    if not text:
        raise RuntimeError("OpenAI response had no text content to parse")

    try:
        cmd = json.loads(text)
    except Exception as e:
        raise RuntimeError(f"Failed to JSON-parse OpenAI output: {e}. Raw: {text[:300]}") from e

    if not isinstance(cmd, dict) or "intent" not in cmd:
        raise RuntimeError(f"OpenAI returned unexpected payload: {cmd!r}")

    return cmd