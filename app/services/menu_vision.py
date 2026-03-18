from __future__ import annotations

import base64
import json
import re
from typing import Any

from openai import OpenAI

client = OpenAI()


def _guess_image_mime(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".jpg") or name.endswith(".jpeg"):
        return "image/jpeg"
    if name.endswith(".webp"):
        # Some APIs/tools are stricter with data URIs.
        # If this gives trouble in your setup, convert WEBP to PNG before sending.
        return "image/webp"
    return "image/jpeg"


def _strip_code_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def extract_menu_from_image_with_ai(
    *,
    image_bytes: bytes,
    filename: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Returns:
      (categories, warnings)

    categories format:
    [
      {
        "name": "Category name",
        "items": [
          {
            "name": "Fish Supper",
            "base_price": "8.50",
            "options": {},
            "extras": []
          }
        ]
      }
    ]
    """
    mime = _guess_image_mime(filename)
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    prompt = """
Extract a takeaway / restaurant menu from this image.

Return JSON only. No markdown. No explanation.

Use exactly this schema:
{
  "categories": [
    {
      "name": "Category name",
      "items": [
        {
          "name": "Item name",
          "base_price": "0.00",
          "options": {},
          "extras": []
        }
      ]
    }
  ],
  "warnings": []
}

Rules:
- Extract only actual purchasable menu content.
- Ignore addresses, phone numbers, opening hours, minimum order, delivery notes, branding text, and marketing text.
- Keep prices as strings like "6.50".
- Put visible option groups into "options", for example:
  "options": {"size": ["Small", "Large (+£1.50)"]}
- Put visible paid extras into "extras", for example:
  "extras": [{"name": "Cheese", "price": "0.50"}]
- If category headings are visible, group items under them.
- If no clear category exists, use a fallback category called "Menu".
- Do not invent items or prices.
- If a line is unclear but likely a menu item, include your best guess and add a short note in "warnings".
- Preserve meal deals / suppers / combos exactly as written if they appear as menu items.
"""

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": data_url,
                        "detail": "high",
                    },
                ],
            }
        ],
    )

    raw_text = getattr(response, "output_text", "") or ""
    raw_text = _strip_code_fences(raw_text)

    if not raw_text:
        raise RuntimeError("Vision extraction returned empty output.")

    try:
        data = json.loads(raw_text)
    except Exception as e:
        raise RuntimeError(f"Vision output was not valid JSON: {raw_text[:800]}") from e

    categories = data.get("categories")
    warnings = data.get("warnings", [])

    if not isinstance(categories, list):
        raise RuntimeError("Vision output did not contain a valid 'categories' list.")

    if not isinstance(warnings, list):
        warnings = [str(warnings)]

    return categories, [str(w).strip() for w in warnings if str(w).strip()]