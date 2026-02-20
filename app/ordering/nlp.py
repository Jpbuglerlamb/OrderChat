# app/ordering/nlp.py
from __future__ import annotations

import difflib
import re
from typing import Dict, List, Optional, Tuple

# ----------------------------
# Synonyms (base, UK-friendly)
# Keep this reasonably sized; patterns + fuzzy matching do the heavy lifting.
# ----------------------------
_DEFAULT_SYNONYMS: Dict[str, str] = {
    # chips/fries ambiguity: you can decide per restaurant later.
    "chips": "fries",
    "chip": "fries",

    # soft drinks
    "coke": "coca cola",
    "cola": "coca cola",
    "coca-cola": "coca cola",
    "cocacola": "coca cola",
    "coca cola": "coca cola",

    # water
    "water": "still water",
    "still water": "still water",

    # common typos
    "donner": "doner",
    "donar": "doner",
    "pepperonni": "pepperoni",
    "peperoni": "pepperoni",

    # pizza names (common UK misspells)
    "margarita": "margherita",
    "margherita": "margherita",
}

# ----------------------------
# Regex helpers
# ----------------------------
# Split user intent by separators while keeping it simple.
_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|\band\b)\s*", re.IGNORECASE)

# Quantity prefix: "2x burger", "2 x burger", "2× burger"
_QTY_RE = re.compile(r"^\s*(\d+)\s*[x×]\s*(.+?)\s*$", re.IGNORECASE)

# Punctuation to spaces (keep letters/numbers/spaces)
_PUNCT_RE = re.compile(r"[^\w\s]+")

# Leading filler words (UK vibes included)
_LEADING_FILLER_RE = re.compile(
    r"^\s*(?:"
    r"hi|hello|hey|hiya|yo|alright|alright mate|morning|evening|"
    r"pls|plz|please|"
    r"mate|boss|bossman|"
    r"i\s*would\s*like|i'?d\s*like|can\s*i\s*get|could\s*i\s*get|may\s*i\s*have|"
    r"can\s*i\s*have|could\s*you|can\s*you|"
    r"get\s*me|give\s*me|i\s*want"
    r")\b[,\s]*",
    re.IGNORECASE,
)

# Words to trim when they appear at the start (after filler strip)
_ARTICLES_RE = re.compile(r"^\s*(?:and\s+)?(?:a|an|the)\b[,\s]*", re.IGNORECASE)

# Trailing politeness
_TRAILING_POLITE_RE = re.compile(r"\b(?:please|pls|plz)\b\.?\s*$", re.IGNORECASE)


def default_synonyms() -> Dict[str, str]:
    """Base synonym map. Menus can extend this in menu.meta.synonyms."""
    return dict(_DEFAULT_SYNONYMS)


# ----------------------------
# Canonicalization pipeline
# ----------------------------
def _basic_normalize(s: str) -> str:
    """
    Basic cleanup:
    - lower
    - replace &/+ with 'and'
    - strip punctuation to spaces
    - collapse whitespace
    """
    s = (s or "").strip().lower()
    s = s.replace("&", " and ").replace("+", " and ")
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_filler_prefix(raw: str) -> str:
    """
    Removes greetings + ordering filler + leading articles.
    Example:
      "Hey can I get a coke please" -> "coke"
      "and a water" -> "water"
    """
    s = (raw or "").strip()

    # remove repeating filler at start (sometimes people type "hey hey can I get")
    while True:
        s2 = _LEADING_FILLER_RE.sub("", s).strip()
        if s2 == s:
            break
        s = s2

    # remove leading "and a/an/the"
    s = _ARTICLES_RE.sub("", s).strip()

    # remove trailing "please/pls"
    s = _TRAILING_POLITE_RE.sub("", s).strip()

    return s


def _apply_pattern_synonyms(s: str) -> str:
    """
    Pattern-based normalization for high-coverage UK inputs.
    Run after _basic_normalize + strip_filler_prefix.
    """
    if not s:
        return s

    # --- Drinks (brands + variants) ---
    s = re.sub(r"\b(coca\s*cola|coca-cola|coke|cocacola)\b", "coca cola", s)
    s = re.sub(r"\b(diet\s+coke|diet\s+coca\s*cola)\b", "diet coca cola", s)
    s = re.sub(r"\b(coke\s*zero|coca\s*cola\s*zero|zero\s*coke)\b", "coca cola zero", s)

    s = re.sub(r"\b(pepsi|max)\b", "pepsi max", s)
    s = re.sub(r"\b(7\s*up|seven\s*up)\b", "7up", s)
    s = re.sub(r"\b(irn\s*bru|irn-bru)\b", "irn bru", s)
    s = re.sub(r"\b(fanta(?:\s*orange)?)\b", "fanta orange", s)
    s = re.sub(r"\b(sprite)\b", "sprite", s)

    # --- Water ---
    s = re.sub(r"\b(sparkling\s+water|fizzy\s+water)\b", "sparkling water", s)
    s = re.sub(r"\b(still\s+water|water)\b", "still water", s)

    # --- Common UK spellings / typos ---
    s = re.sub(r"\b(donner|donar)\b", "doner", s)
    s = re.sub(r"\b(peperoni|pepperonni)\b", "pepperoni", s)
    s = re.sub(r"\b(margarita)\b", "margherita", s)

    # --- Common “&”/spacing edge cases ---
    # “salt&pepper” -> “salt and pepper”
    s = re.sub(r"\bsalt\s*and\s*pepper\b", "salt and pepper", s)

    # collapse again
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _apply_dictionary_synonyms(s: str, synonyms: Dict[str, str]) -> str:
    """
    Applies synonym replacements safely.
    We apply longer keys first to avoid partial replacement conflicts.
    Uses word boundaries to avoid weird mid-word hits.
    """
    if not s:
        return s

    syn = synonyms or {}
    # Sort keys longest-first (e.g., "coke zero" before "coke")
    keys = sorted((k for k in syn.keys() if k), key=lambda x: len(str(x)), reverse=True)

    for k in keys:
        v = syn.get(k)
        if not v:
            continue
        k_norm = _basic_normalize(str(k))
        v_norm = _basic_normalize(str(v))

        # word boundary replace
        # Note: use (?<!\w) / (?!\w) rather than \b for cases with numbers like "7up"
        pattern = rf"(?<!\w){re.escape(k_norm)}(?!\w)"
        s = re.sub(pattern, v_norm, s)

    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_text(s: str, synonyms: Dict[str, str]) -> str:
    """
    Main normalization used by ordering flow.
    Pipeline:
      basic normalize -> strip filler -> pattern synonyms -> dictionary synonyms -> collapse
    """
    s = _basic_normalize(s)
    s = strip_filler_prefix(s)
    s = _basic_normalize(s)  # re-normalize after stripping
    s = _apply_pattern_synonyms(s)
    s = _apply_dictionary_synonyms(s, synonyms)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------
# Quantity + intent parsing
# ----------------------------
def parse_qty_prefix(msg: str) -> Tuple[int, str]:
    m = _QTY_RE.match(msg or "")
    if not m:
        return 1, (msg or "").strip()
    return max(1, int(m.group(1))), (m.group(2) or "").strip()


def split_intents(msg_norm: str) -> List[str]:
    """
    Split by commas, &, +, and "and".
    If nothing splits, returns [msg_norm].
    """
    parts = [p.strip() for p in _SPLIT_RE.split(msg_norm or "") if p and p.strip()]
    return parts or [msg_norm or ""]


# ----------------------------
# Fuzzy matching
# ----------------------------
def fuzzy_best_key(keys: List[str], query: str, cutoff: float = 0.72) -> Optional[str]:
    if not query or not keys:
        return None
    q = query.strip().lower()
    if q in keys:
        return q
    matches = difflib.get_close_matches(q, keys, n=1, cutoff=cutoff)
    return matches[0] if matches else None