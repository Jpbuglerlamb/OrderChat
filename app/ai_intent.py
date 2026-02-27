# app/ai_intent.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from openai import AsyncOpenAI

# Create client once at import time (OK in FastAPI)
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


SYSTEM = """You are an intent parser for a takeaway ordering app.

Convert the user's message into ONE JSON command that matches the provided JSON schema.

Rules:
- Never invent menu items. Only choose from menu_hints.
- If unsure, use intent "unknown".
- If the system is awaiting a selection (state.mode), interpret short answers like "large", "no", "yes", "1", etc.
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
    Only include info the model needs to avoid inventing items.
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

    return {"categories": cats, "items": items[:160]}  # cap


def _extract_text(resp: Any) -> str:
    """
    Different SDK versions expose output differently.
    Try the common paths.
    """
    txt = getattr(resp, "output_text", None)
    if isinstance(txt, str) and txt.strip():
        return txt.strip()

    # fallback to digging output/content
    try:
        out0 = resp.output[0]
        content0 = out0.content[0]
        text = getattr(content0, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:
        pass

    return ""


async def interpret_message_llm(
    message: str,
    menu: Dict[str, Any],
    state: Dict[str, Any],
    *,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns a dict matching COMMAND_SCHEMA.
    Raises on failure (caller decides fallback).
    """
    payload = {
        "message": message,
        "state": state,
        "menu_hints": _menu_hints(menu),
    }

    resp = await client.responses.create(
        model=model or os.getenv("LLM_MODEL", "gpt-5-mini"),
        input=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_schema", "json_schema": COMMAND_SCHEMA},
    )

    text = _extract_text(resp)
    if not text:
        raise RuntimeError("OpenAI response contained no output_text")

    cmd = json.loads(text)
    if not isinstance(cmd, dict) or "intent" not in cmd:
        raise RuntimeError(f"Unexpected LLM payload: {cmd!r}")

    return cmd