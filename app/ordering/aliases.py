from __future__ import annotations

from typing import Any, Dict, List

from .nlp import normalize_text


# These are phrase expansions only.
# Use them when the exact combo item does NOT exist on the menu.
PHRASE_EXPANSIONS: Dict[str, List[Dict[str, Any]]] = {
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
    "fish and chips": [
        {"name": "fish", "qty": 1},
        {"name": "chips", "qty": 1},
    ],
    "fish & chips": [
        {"name": "fish", "qty": 1},
        {"name": "chips", "qty": 1},
    ],
}


def expand_order_phrase(text: str, synonyms: Dict[str, str]) -> List[Dict[str, Any]] | None:
    """
    Return structured expansion for common takeaway phrases.
    Example:
        'fish supper' -> [{'name': 'fish', 'qty': 1}, {'name': 'chips', 'qty': 1}]
    """
    norm = normalize_text(text or "", synonyms)
    if not norm:
        return None

    return PHRASE_EXPANSIONS.get(norm)