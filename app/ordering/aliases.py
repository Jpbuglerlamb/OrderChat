# app/ordering/aliases.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .nlp import normalize_text

# These are phrase expansions only.
# Use them when the exact combo item does NOT exist on the menu.
#
# IMPORTANT:
# - Keys should represent human ordering phrases
# - Values should be structured as a list of real menu item intents
# - Do NOT use this for phrases that should always remain a single item
PHRASE_EXPANSIONS: Dict[str, List[Dict[str, Any]]] = {
    # Chip shop / supper language
    "fish supper": [
        {"name": "fish", "qty": 1},
        {"name": "chips", "qty": 1},
    ],
    "sausage supper": [
        {"name": "sausage", "qty": 1},
        {"name": "chips", "qty": 1},
    ],
    "smoked sausage supper": [
        {"name": "smoked sausage", "qty": 1},
        {"name": "chips", "qty": 1},
    ],
    "single fish": [
        {"name": "fish", "qty": 1},
    ],
    "single sausage": [
        {"name": "sausage", "qty": 1},
    ],

    # Common direct combo phrasing
    "fish and chips": [
        {"name": "fish", "qty": 1},
        {"name": "chips", "qty": 1},
    ],
    "fish & chips": [
        {"name": "fish", "qty": 1},
        {"name": "chips", "qty": 1},
    ],

    # Extra useful shorthand
    "doner meat and chips": [
        {"name": "doner meat", "qty": 1},
        {"name": "chips", "qty": 1},
    ],
    "doner kebab and chips": [
        {"name": "doner kebab", "qty": 1},
        {"name": "chips", "qty": 1},
    ],
    "chips cheese": [
        {"name": "chips", "qty": 1},
        {"name": "cheese", "qty": 1},
    ],
    "chips and cheese": [
        {"name": "chips", "qty": 1},
        {"name": "cheese", "qty": 1},
    ],
}

# Optional normalized cache so lookup stays fast and consistent
_NORMALIZED_EXPANSIONS: Dict[str, List[Dict[str, Any]]] | None = None


def _copy_expansion(parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Return a safe copy so callers can mutate results without touching globals.
    """
    out: List[Dict[str, Any]] = []
    for part in parts:
        out.append(
            {
                "name": str(part.get("name") or "").strip(),
                "qty": max(1, int(part.get("qty") or 1)),
            }
        )
    return out


def _build_normalized_expansions(synonyms: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    normalized: Dict[str, List[Dict[str, Any]]] = {}

    for phrase, expansion in PHRASE_EXPANSIONS.items():
        norm_phrase = normalize_text(phrase, synonyms)
        if not norm_phrase:
            continue

        cleaned_parts = _copy_expansion(expansion)
        if cleaned_parts:
            normalized[norm_phrase] = cleaned_parts

    return normalized


def _get_normalized_expansions(synonyms: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
    global _NORMALIZED_EXPANSIONS

    # Safe enough for this use case. If synonyms change meaningfully per menu,
    # you can later move this cache into menu["_index"] or rebuild per call.
    if _NORMALIZED_EXPANSIONS is None:
        _NORMALIZED_EXPANSIONS = _build_normalized_expansions(synonyms)

    return _NORMALIZED_EXPANSIONS


def expand_order_phrase(text: str, synonyms: Dict[str, str]) -> Optional[List[Dict[str, Any]]]:
    """
    Return structured expansion for common takeaway phrases.

    Example:
        'fish supper'
        -> [{'name': 'fish', 'qty': 1}, {'name': 'chips', 'qty': 1}]

    Important:
    - This only expands fallback shorthand phrases
    - Caller should still prefer exact real menu items first
    """
    norm = normalize_text(text or "", synonyms)
    if not norm:
        return None

    expansions = _get_normalized_expansions(synonyms)
    found = expansions.get(norm)
    if not found:
        return None

    return _copy_expansion(found)