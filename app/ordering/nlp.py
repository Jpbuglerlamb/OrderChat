# app/ordering/nlp.py
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

# ----------------------------
# Synonyms (base, UK-friendly)
# Keep this reasonably sized; patterns + fuzzy matching do the heavy lifting.
# Note: values are canonical forms AFTER normalization (we canonicalize to "and", not "&").
# ----------------------------
_DEFAULT_SYNONYMS: Dict[str, str] = {
    # ----------------------------
    # General ordering shorthand
    # ----------------------------
    "regular": "standard",
    "std": "standard",
    "normal": "standard",
    "plain": "plain",
    "classic": "plain",

    # ----------------------------
    # Chips / fries (UK)
    # ----------------------------
    "chips": "fries",
    "chip": "fries",
    "fries": "fries",
    "french fries": "fries",
    "skinny fries": "fries",
    "straight cut fries": "fries",
    "chunky chips": "fries",
    "steak fries": "fries",

    # ----------------------------
    # Drinks: Coca Cola family
    # ----------------------------
    "coke": "coca cola",
    "cola": "coca cola",
    "coca-cola": "coca cola",
    "cocacola": "coca cola",
    "coca cola": "coca cola",
    "original coke": "coca cola",
    "classic coke": "coca cola",
    "coca cola original": "coca cola",

    "diet coke": "diet coca cola",
    "diet cola": "diet coca cola",
    "coca cola diet": "diet coca cola",

    "coke zero": "coca cola zero",
    "zero coke": "coca cola zero",
    "coca cola zero": "coca cola zero",
    "coke 0": "coca cola zero",

    # ----------------------------
    # Drinks: Pepsi / others
    # ----------------------------
    "pepsi": "pepsi",
    "pepsi max": "pepsi max",
    "pepsi maxx": "pepsi max",
    "max pepsi": "pepsi max",
    "diet pepsi": "pepsi max",  # many people mean max in UK

    "7 up": "7up",
    "seven up": "7up",
    "7up": "7up",

    "irn-bru": "irn bru",
    "irn bru": "irn bru",

    "sprite": "sprite",

    "fanta": "fanta orange",
    "fanta orange": "fanta orange",

    "dr pepper": "dr pepper",
    "dr. pepper": "dr pepper",

    "lucozade": "lucozade original",
    "lucozade original": "lucozade original",
    "lucozade orange": "lucozade orange",

    "ribena": "ribena",
    "vimto": "vimto",

    "ginger beer": "ginger beer",
    "tonic": "tonic water",
    "tonic water": "tonic water",

    # ----------------------------
    # Water
    # ----------------------------
    "water": "still water",
    "still water": "still water",
    "tap water": "still water",
    "bottled water": "still water",
    "sparkling water": "sparkling water",
    "fizzy water": "sparkling water",
    "carbonated water": "sparkling water",

    # ----------------------------
    # Common typos / spelling variants (general)
    # ----------------------------
    "donner": "doner",
    "donar": "doner",
    "doner": "doner",
    "kebab": "kebab",
    "kebeb": "kebab",

    "pepperonni": "pepperoni",
    "peperoni": "pepperoni",
    "pepperoni": "pepperoni",

    "margarita": "margherita",
    "margherita": "margherita",

    "chilli": "chili",
    "chile": "chili",

    # ----------------------------
    # Sauce spellings / shorthand
    # ----------------------------
    "bbq": "barbecue",
    "barbeque": "barbecue",
    "barbecue": "barbecue",

    "mayo": "mayonnaise",
    "mayo.": "mayonnaise",
    "mayonaise": "mayonnaise",
    "mayonnaise": "mayonnaise",

    "ketchup": "tomato ketchup",
    "tomato sauce": "tomato ketchup",
    "tomato ketchup": "tomato ketchup",

    "sweet chilli": "sweet chili",
    "sweet chili": "sweet chili",

    # ----------------------------
    # Chinese takeaway: core shorthand
    # ----------------------------
    "s and s": "sweet and sour",
    "s&s": "sweet and sour",
    "sweet n sour": "sweet and sour",
    "sweet and sour": "sweet and sour",
    "sweet sour": "sweet and sour",
    "sweet and sour sauce": "sweet and sour",

    "salt n pepper": "salt and pepper",
    "salt and pepper": "salt and pepper",
    "salt pepper": "salt and pepper",
    "s and p": "salt and pepper",
    "s&p": "salt and pepper",

    "black bean": "black bean sauce",
    "blackbean": "black bean sauce",
    "black bean sauce": "black bean sauce",

    "curry sauce": "curry sauce",
    "chinese curry": "curry sauce",
    "curry": "curry sauce",

    "satay": "satay sauce",
    "satay sauce": "satay sauce",
    "peanut sauce": "satay sauce",

    "ok sauce": "ok sauce",
    "o k sauce": "ok sauce",

    "chow mein": "chow mein",
    "chowmein": "chow mein",
    "chow main": "chow mein",

    "fried rice": "fried rice",
    "egg fried rice": "egg fried rice",
    "egg fried": "egg fried rice",
    "egg rice": "egg fried rice",
    "special fried rice": "special fried rice",
    "house fried rice": "special fried rice",

    "boiled rice": "boiled rice",
    "plain rice": "boiled rice",
    "steamed rice": "boiled rice",

    "noodles": "chow mein",
    "soft noodles": "chow mein",

    "spring roll": "spring rolls",
    "spring rolls": "spring rolls",
    "veg spring rolls": "spring rolls",
    "vegetable spring rolls": "spring rolls",

    "prawn crackers": "prawn crackers",
    "prawn crackrs": "prawn crackers",
    "prawn chips": "prawn crackers",

    "dumplings": "dumplings",
    "dimsum": "dumplings",
    "dim sum": "dumplings",

    # ----------------------------
    # UK-ish shorthand people type
    # ----------------------------
    "bev": "drink",
    "bevs": "drinks",
    "pop": "soft drink",
    "fizzy drink": "soft drink",
    "soda": "soft drink",

    # ----------------------------
    # Portion sizing words (optional canonicalisation)
    # ----------------------------
    "sm": "small",
    "sml": "small",
    "small": "small",
    "med": "medium",
    "medium": "medium",
    "lg": "large",
    "lrg": "large",
    "large": "large",
}

# ----------------------------
# Regex helpers
# ----------------------------

# Split user intent by separators.
# IMPORTANT: do NOT split on "and" here; we handle "and" splitting with protection logic
# so compounds like "sweet and sour" don't get broken.
_SPLIT_RE = re.compile(r"\s*(?:,|&|\+)\s*", re.IGNORECASE)

# Quantity prefix: "2x burger", "2 x burger", "2× burger"
_QTY_RE = re.compile(r"^\s*(\d+)\s*[x×]\s*(.+?)\s*$", re.IGNORECASE)

# Punctuation to spaces (keep letters/numbers/spaces/underscores)
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

# ----------------------------
# Category / browse question stripping
# ----------------------------

# Question-ish prefixes that wrap category browsing intent.
# Keep it short and high-signal; you can expand as you see real messages.
_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"what\s+(?:.*\s+)?do\s+you\s+have|"
    r"what\s+have\s+you\s+got|"
    r"what\s+.*\s+is\s+there|"
    r"what\s+are\s+the\s+options\s+for|"
    r"show\s+me|"
    r"can\s+i\s+see|"
    r"do\s+you\s+have\s+any|"
    r"any|"
    r"which|"
    r"whats|what's"
    r")\b[,\s]*",
    re.IGNORECASE,
)

# Optional trailing browse suffixes
_QUESTION_SUFFIX_RE = re.compile(
    r"\b(?:do\s+you\s+have|have\s+you\s+got|available|options|on\s+the\s+menu)\b\.?\s*$",
    re.IGNORECASE,
)


def strip_question_wrapper(raw: str) -> str:
    """
    Removes common browse/question wrappers so category extraction can work.
    Examples:
      "What rice do you have?" -> "rice"
      "Show me the rice options" -> "rice options" (still useful)
      "Do you have any drinks?" -> "drinks"
    """
    s = (raw or "").strip()

    # remove leading question wrappers (possibly repeating)
    while True:
        s2 = _QUESTION_PREFIX_RE.sub("", s).strip()
        if s2 == s:
            break
        s = s2

    # remove trailing browse words (light touch)
    s = _QUESTION_SUFFIX_RE.sub("", s).strip()

    # remove leading articles again, just in case
    s = _ARTICLES_RE.sub("", s).strip()

    # remove trailing please/pls
    s = _TRAILING_POLITE_RE.sub("", s).strip()

    return s
# Phrases containing "and" that should NOT be split into multiple intents.
# (Add more as you discover them in real orders.)
_PROTECTED_AND_PHRASES = [
    "sweet and sour",
    "salt and pepper",
    "black and white",
    "fish and chips",  # common UK order phrase
]


def default_synonyms() -> Dict[str, str]:
    """Base synonym map. Menus can extend this in menu.meta.synonyms."""
    return dict(_DEFAULT_SYNONYMS)


# ----------------------------
# Canonicalization pipeline
# ----------------------------
def _basic_normalize(s: str) -> str:
    """
    Basic cleanup:
    - unicode normalize
    - lower
    - strip bracketed descriptors (menu fluff like "(Hong Kong Style)")
    - replace &/+ with "and"
    - strip punctuation to spaces
    - collapse whitespace
    """
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKC", s)

    # remove bracketed descriptors: "(...)" and "[...]"
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\[[^]]*\]", " ", s)

    # normalize separators to words
    s = s.replace("&", " and ").replace("+", " and ")

    # punctuation -> spaces
    s = _PUNCT_RE.sub(" ", s)

    # collapse whitespace
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

    s = re.sub(r"\b(pepsi\s*max|pepsi\s*maxx|pepsi\s*max)\b", "pepsi max", s)
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

    # --- Common "n" shorthand ---
    s = re.sub(r"\b(n)\b", "and", s)

    # collapse again
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _apply_dictionary_synonyms(s: str, synonyms: Dict[str, str]) -> str:
    """
    Applies synonym replacements safely.
    We apply longer keys first to avoid partial replacement conflicts.
    Uses (?<!\\w) and (?!\\w) rather than \\b for cases with numbers like "7up".
    """
    if not s:
        return s

    syn = synonyms or {}
    keys = sorted((k for k in syn.keys() if k), key=lambda x: len(str(x)), reverse=True)

    for k in keys:
        v = syn.get(k)
        if not v:
            continue

        k_norm = _basic_normalize(str(k))
        v_norm = _basic_normalize(str(v))

        pattern = rf"(?<!\w){re.escape(k_norm)}(?!\w)"
        s = re.sub(pattern, v_norm, s)

    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_text(s: str, synonyms: Dict[str, str]) -> str:
    """
    Main normalization used by ordering flow.
    Pipeline:
      basic normalize -> strip filler -> strip question wrapper -> re-normalize -> pattern synonyms -> dictionary synonyms -> collapse
    """
    s = _basic_normalize(s)
    s = strip_filler_prefix(s)
    s = strip_question_wrapper(s)  # ✅ add this
    s = _basic_normalize(s)  # re-normalize after stripping
    s = _apply_pattern_synonyms(s)
    s = _apply_dictionary_synonyms(s, synonyms)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------
# Quantity + intent parsing
# ----------------------------
def parse_qty_prefix(msg: str) -> Tuple[int, str]:
    """
    Parse quantity prefixes like "2x burger", "2 x burger", "2× burger".
    Returns (qty, rest_of_message).
    """
    m = _QTY_RE.match(msg or "")
    if not m:
        return 1, (msg or "").strip()
    return max(1, int(m.group(1))), (m.group(2) or "").strip()


def _protect_and_phrases(s: str) -> str:
    """
    Protect known compound phrases containing 'and' so intent splitting won't break them.
    We replace ' and ' with ' _and_ ' inside those phrases temporarily.
    """
    if not s:
        return s
    out = s
    for phrase in _PROTECTED_AND_PHRASES:
        ph = _basic_normalize(phrase)
        if not ph:
            continue
        protected = ph.replace(" and ", " _and_ ")
        out = re.sub(rf"(?<!\w){re.escape(ph)}(?!\w)", protected, out)
    return out


def _unprotect_and_phrases(s: str) -> str:
    return (s or "").replace(" _and_ ", " and ")


def split_intents(msg_norm: str) -> List[str]:
    """
    Split user intent by commas, &, +, and also natural language "and",
    BUT without breaking common compound dish phrases like "sweet and sour".

    Strategy:
      1) protect compound phrases containing "and"
      2) split by , & +
      3) then split by " and " (word) only
      4) unprotect
    """
    s = (msg_norm or "").strip()
    if not s:
        return [""]

    s = _protect_and_phrases(s)

    # split on explicit separators first
    chunks = [c.strip() for c in _SPLIT_RE.split(s) if c and c.strip()]

    # then split remaining on " and " (natural language lists)
    parts: List[str] = []
    for c in chunks:
        sub = [p.strip() for p in re.split(r"\s+\band\b\s+", c, flags=re.IGNORECASE) if p and p.strip()]
        parts.extend(sub or [c])

    parts = [_unprotect_and_phrases(p) for p in parts]
    return parts or [msg_norm or ""]


# ----------------------------
# Fuzzy matching
# ----------------------------
def fuzzy_best_key(keys: List[str], query: str, cutoff: float = 0.68) -> Optional[str]:
    """
    Return best matching key from a list using difflib.
    cutoff is lowered slightly for food ordering tolerance.
    """
    if not query or not keys:
        return None
    q = query.strip().lower()
    if q in keys:
        return q
    matches = difflib.get_close_matches(q, keys, n=1, cutoff=cutoff)
    return matches[0] if matches else None