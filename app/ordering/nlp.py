# app/ordering/nlp.py
from __future__ import annotations

import difflib
import re
from typing import Dict, List, Optional, Tuple

_DEFAULT_SYNONYMS: Dict[str, str] = {
    "chips": "fries",
    "chip": "fries",
    "coke": "coca cola",
    "cola": "coca cola",
    "water": "still water",
    "donner": "doner",
    "pepperonni": "pepperoni",
    "margarita": "margherita",
    "coca-cola": "coca cola",
    "cocacola": "coca cola",
}

_SPLIT_RE = re.compile(r"\s*(?:,|&|\+| and )\s*", re.IGNORECASE)
_QTY_RE = re.compile(r"^\s*(\d+)\s*[x×]\s*(.+?)\s*$", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")


def normalize_text(s: str, synonyms: Dict[str, str]) -> str:
    s = (s or "").strip().lower()
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()

    for k, v in (synonyms or {}).items():
        if not k:
            continue
        s = re.sub(rf"\b{re.escape(str(k).lower())}\b", str(v).lower(), s)

    return re.sub(r"\s+", " ", s).strip()


def strip_filler_prefix(raw: str) -> str:
    s = (raw or "").strip()

    # greetings
    s = re.sub(r"^\s*(hi|hello|hey)\b[,\s]*", "", s, flags=re.I)

    # common ordering filler
    s = re.sub(
        r"^\s*(i\s*would\s*like|i'?d\s*like|can\s*i\s*get|could\s*i\s*get|may\s*i\s*have)\b[,\s]*",
        "",
        s,
        flags=re.I,
    )

    # ✅ continuation filler: "and a coke" -> "coke"
    s = re.sub(r"^\s*and\b[,\s]*", "", s, flags=re.I)
    s = re.sub(r"^\s*(a|an)\b[,\s]*", "", s, flags=re.I)

    # trailing politeness
    s = re.sub(r"\bplease\b\s*$", "", s, flags=re.I)

    return s.strip()


def parse_qty_prefix(msg: str) -> Tuple[int, str]:
    m = _QTY_RE.match(msg or "")
    if not m:
        return 1, (msg or "").strip()
    return max(1, int(m.group(1))), (m.group(2) or "").strip()


def split_intents(msg_norm: str) -> List[str]:
    parts = [p.strip() for p in _SPLIT_RE.split(msg_norm or "") if p and p.strip()]
    return parts or [msg_norm or ""]


def fuzzy_best_key(keys: List[str], query: str, cutoff: float = 0.72) -> Optional[str]:
    if not query or not keys:
        return None
    q = query.strip().lower()
    if q in keys:
        return q
    matches = difflib.get_close_matches(q, keys, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def default_synonyms() -> Dict[str, str]:
    return dict(_DEFAULT_SYNONYMS)