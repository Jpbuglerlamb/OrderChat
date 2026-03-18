from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

# ----------------------------
# Synonyms (base, UK-friendly)
# Values should be canonical AFTER normalization.
# ----------------------------
_DEFAULT_SYNONYMS: Dict[str, str] = {
    # General ordering shorthand
    "regular": "standard",
    "std": "standard",
    "normal": "standard",
    "plain": "plain",
    "classic": "plain",

    # Chips / fries
    "chips": "fries",
    "chip": "fries",
    "fries": "fries",
    "french fries": "fries",
    "skinny fries": "fries",
    "straight cut fries": "fries",
    "chunky chips": "fries",
    "steak fries": "fries",

    # Scottish chip shop language
    "fish supper": "fish supper",
    "sausage supper": "sausage supper",
    "smoked sausage supper": "smoked sausage supper",
    "single fish": "fish",
    "single sausage": "sausage",
    "smoke sausage": "smoked sausage",
    "smokie sausage": "smoked sausage",
    "chippy sauce": "brown sauce",
    "salt n sauce": "salt and sauce",
    "salt and sauce": "salt and sauce",
    "can of juice": "soft drink",
    "juice": "soft drink",

    # Drinks
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

    "pepsi": "pepsi",
    "pepsi max": "pepsi max",
    "pepsi maxx": "pepsi max",
    "max pepsi": "pepsi max",
    "diet pepsi": "pepsi max",

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
    "tonic": "tonic water",
    "tonic water": "tonic water",

    # Water
    "water": "still water",
    "still water": "still water",
    "tap water": "still water",
    "bottled water": "still water",
    "sparkling water": "sparkling water",
    "fizzy water": "sparkling water",
    "carbonated water": "sparkling water",

    # Common typos / variants
    "donner": "doner",
    "donar": "doner",
    "doner": "doner",
    "kebeb": "kebab",
    "pepperonni": "pepperoni",
    "peperoni": "pepperoni",
    "margarita": "margherita",
    "chilli": "chili",
    "chile": "chili",

    # Sauces
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

    # Chinese takeaway
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

    # UK shorthand
    "bev": "drink",
    "bevs": "drinks",
    "pop": "soft drink",
    "fizzy drink": "soft drink",
    "soda": "soft drink",

    # Portion sizing
    "sm": "small",
    "sml": "small",
    "small": "small",
    "med": "medium",
    "medium": "medium",
    "lg": "large",
    "lrg": "large",
    "large": "large",
}

_SPLIT_RE = re.compile(r"\s*(?:,|&|\+)\s*", re.IGNORECASE)
_QTY_RE = re.compile(r"^\s*(\d+)\s*[x×]\s*(.+?)\s*$", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]+")

_LEADING_FILLER_RE = re.compile(
    r"^\s*(?:"
    r"hi|hello|hey|hiya|yo|alright|alright mate|morning|evening|"
    r"pls|plz|please|"
    r"mate|boss|bossman|"
    r"i\s*would\s*like|i'?d\s*like|i'?ll\s*have|i\s*will\s*have|i'?ll\s*take|"
    r"can\s*i\s*get|could\s*i\s*get|may\s*i\s*have|can\s*i\s*have|"
    r"could\s*you|can\s*you|get\s*me|give\s*me|i\s*want|"
    r"put\s*me\s*down\s*for|add|have"
    r")\b[,\s]*",
    re.IGNORECASE,
)

_ARTICLES_RE = re.compile(r"^\s*(?:and\s+)?(?:a|an|the)\b[,\s]*", re.IGNORECASE)
_TRAILING_POLITE_RE = re.compile(r"\b(?:please|pls|plz)\b\.?\s*$", re.IGNORECASE)

_STATUS_QUERY_RE = re.compile(
    r"^\s*(?:"
    r"(?:what|whats|what's)\s+(?:the\s+)?status\b|"
    r"(?:where'?s|where\s+is)\s+(?:my\s+)?order\b|"
    r"(?:is|has)\s+(?:my\s+)?order\s+(?:been\s+)?(?:accepted|confirmed|started|ready|completed)\b|"
    r"(?:is\s+it\s+)?(?:ready|accepted|confirmed|preparing|completed)\b\s*(?:yet)?\s*[?.!]*|"
    r"order\s+status\b|track\s+(?:my\s+)?order\b|"
    r"any\s+update(?:s)?\b|update\s+on\s+(?:my\s+)?order\b"
    r")\s*$",
    re.IGNORECASE,
)

_STATUS_TARGET_RE = re.compile(
    r"\b(new|accepted|confirmed|preparing|ready|completed|complete|done)\b",
    re.IGNORECASE,
)

_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"what\s+(?:.*\s+)?do\s+you\s+have|"
    r"what\s+have\s+you\s+got|"
    r"what\s+.*\s+is\s+there|"
    r"what\s+are\s+the\s+options\s+for|"
    r"show\s+me|"
    r"can\s+i\s+see|"
    r"do\s+you\s+have\s+any|"
    r"any|which|whats|what's"
    r")\b[,\s]*",
    re.IGNORECASE,
)

_QUESTION_SUFFIX_RE = re.compile(
    r"\b(?:do\s+you\s+have|have\s+you\s+got|available|options|on\s+the\s+menu)\b\.?\s*$",
    re.IGNORECASE,
)

_GENERIC_MENU_Q_RE = re.compile(
    r"^\s*(?:"
    r"what\s+do\s+you\s+have|"
    r"what\s+have\s+you\s+got|"
    r"what\s+do\s+you\s+sell|"
    r"what\s+can\s+i\s+get|"
    r"what\s+can\s+i\s+have|"
    r"what'?s\s+on\s+the\s+menu|"
    r"show\s+(?:me\s+)?the\s+menu|"
    r"(?:the\s+)?menu"
    r")\s*[?!.\s]*$",
    re.IGNORECASE,
)

_PROTECTED_AND_PHRASES = [
    "sweet and sour",
    "salt and pepper",
    "fish and chips",
    "salt and sauce",
]

_STOPWORDS = {
    "in", "with", "the", "a", "an", "of", "style", "sauce", "meal",
    "portion", "box", "special", "side", "dish", "regular", "standard",
}

_HEADWORDS = {
    "beef", "chicken", "pork", "duck", "lamb", "doner", "kebab", "burger",
    "pizza", "rice", "noodles", "coke", "cola", "water", "fanta", "sprite",
    "chips", "fries", "prawn", "king prawn", "curry", "satay",
}


def default_synonyms() -> Dict[str, str]:
    return dict(_DEFAULT_SYNONYMS)


def _basic_normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\[[^]]*\]", " ", s)
    s = s.replace("&", " and ").replace("+", " and ")
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_filler_prefix(raw: str) -> str:
    s = (raw or "").strip()
    while True:
        s2 = _LEADING_FILLER_RE.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    s = _ARTICLES_RE.sub("", s).strip()
    s = _TRAILING_POLITE_RE.sub("", s).strip()
    return s


def strip_question_wrapper(raw: str) -> str:
    s = (raw or "").strip()
    while True:
        s2 = _QUESTION_PREFIX_RE.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    s = _QUESTION_SUFFIX_RE.sub("", s).strip()
    s = _ARTICLES_RE.sub("", s).strip()
    s = _TRAILING_POLITE_RE.sub("", s).strip()
    return s


def _apply_pattern_synonyms(s: str) -> str:
    if not s:
        return s

    s = re.sub(r"\b(coca\s*cola|coca-cola|coke|cocacola)\b", "coca cola", s)
    s = re.sub(r"\b(diet\s+coke|diet\s+coca\s*cola)\b", "diet coca cola", s)
    s = re.sub(r"\b(coke\s*zero|coca\s*cola\s*zero|zero\s*coke)\b", "coca cola zero", s)
    s = re.sub(r"\b(pepsi\s*max|pepsi\s*maxx)\b", "pepsi max", s)
    s = re.sub(r"\b(7\s*up|seven\s*up)\b", "7up", s)
    s = re.sub(r"\b(irn\s*bru|irn-bru)\b", "irn bru", s)
    s = re.sub(r"\b(fanta(?:\s*orange)?)\b", "fanta orange", s)

    s = re.sub(r"\b(sparkling\s+water|fizzy\s+water)\b", "sparkling water", s)
    s = re.sub(r"\b(still\s+water|water)\b", "still water", s)

    s = re.sub(r"\b(donner|donar)\b", "doner", s)
    s = re.sub(r"\b(peperoni|pepperonni)\b", "pepperoni", s)
    s = re.sub(r"\b(margarita)\b", "margherita", s)

    s = re.sub(r"\b(n)\b", "and", s)

    s = re.sub(r"\s+", " ", s).strip()
    return s


def _apply_dictionary_synonyms(s: str, synonyms: Dict[str, str]) -> str:
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
    s0 = _basic_normalize(s)

    if _GENERIC_MENU_Q_RE.match(s0):
        return "menu"

    s0 = strip_filler_prefix(s0)

    if _GENERIC_MENU_Q_RE.match(s0):
        return "menu"

    s0 = strip_question_wrapper(s0)

    if not s0 and _GENERIC_MENU_Q_RE.match(_basic_normalize(s)):
        return "menu"

    s0 = _basic_normalize(s0)
    s0 = _apply_pattern_synonyms(s0)
    s0 = _apply_dictionary_synonyms(s0, synonyms)
    return re.sub(r"\s+", " ", s0).strip()


def parse_qty_prefix(msg: str) -> Tuple[int, str]:
    m = _QTY_RE.match(msg or "")
    if not m:
        return 1, (msg or "").strip()
    return max(1, int(m.group(1))), (m.group(2) or "").strip()


def _protect_and_phrases(s: str) -> str:
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
    s = (msg_norm or "").strip()
    if not s:
        return [""]

    s = _protect_and_phrases(s)
    chunks = [c.strip() for c in _SPLIT_RE.split(s) if c and c.strip()]

    parts: List[str] = []
    for c in chunks:
        sub = [p.strip() for p in re.split(r"\s+\band\b\s+", c, flags=re.IGNORECASE) if p and p.strip()]
        parts.extend(sub or [c])

    parts = [_unprotect_and_phrases(p) for p in parts]
    return parts or [msg_norm or ""]


def is_order_status_query(raw: str) -> bool:
    s = (raw or "").strip()
    if not s:
        return False
    s0 = _basic_normalize(s)
    s0 = strip_filler_prefix(s0)
    s0 = strip_question_wrapper(s0)
    s0 = _basic_normalize(s0)
    return bool(_STATUS_QUERY_RE.match(s0))


def extract_status_target(raw: str) -> Optional[str]:
    s = _basic_normalize(strip_question_wrapper(strip_filler_prefix(raw or "")))
    m = _STATUS_TARGET_RE.search(s)
    if not m:
        return None
    t = m.group(1).lower().strip()
    if t in {"complete", "done"}:
        return "completed"
    if t == "confirmed":
        return "accepted"
    return t


def fuzzy_best_key(keys: List[str], query: str, cutoff: float = 0.68) -> Optional[str]:
    if not query or not keys:
        return None
    q = query.strip().lower()
    if q in keys:
        return q
    matches = difflib.get_close_matches(q, keys, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def tokenize_for_match(text: str) -> Set[str]:
    if not text:
        return set()
    tokens = set()
    for tok in text.split():
        tok = tok.strip()
        if not tok or tok in _STOPWORDS:
            continue
        tokens.add(tok)
    return tokens


def token_overlap_score(query_tokens: Set[str], candidate_tokens: Set[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    common = query_tokens & candidate_tokens
    if not common:
        return 0.0
    return len(common) / max(len(candidate_tokens), 1)


def head_token(text: str) -> Optional[str]:
    tokens = [t for t in text.split() if t and t not in _STOPWORDS]
    return tokens[0] if tokens else None


def generate_aliases(name: str, synonyms: Dict[str, str]) -> List[str]:
    """
    Generate useful search aliases for a menu item.
    Example:
      'Beef in Black Bean Sauce'
      -> [
          'beef in black bean sauce',
          'beef black bean sauce',
          'black bean sauce',
          'black bean',
          'beef',
          'beef black bean'
         ]
    """
    base = normalize_text(name, synonyms)
    if not base:
        return []

    aliases: Set[str] = {base}

    compact = re.sub(r"\b(in|with|and|the|a|an)\b", " ", base)
    compact = re.sub(r"\s+", " ", compact).strip()
    if compact:
        aliases.add(compact)

    if " sauce" in base:
        aliases.add(base.replace(" sauce", "").strip())

    tokens = [t for t in base.split() if t and t not in _STOPWORDS]

    if tokens:
        aliases.add(tokens[0])
        if len(tokens) >= 2:
            aliases.add(" ".join(tokens[:2]))
            aliases.add(" ".join(tokens[-2:]))
        if len(tokens) >= 3:
            aliases.add(" ".join(tokens[:3]))

    # noun-ish trailing phrases
    for phrase in (
        "black bean sauce",
        "sweet and sour",
        "salt and pepper",
        "egg fried rice",
        "fried rice",
        "chow mein",
        "satay sauce",
        "curry sauce",
        "spring rolls",
        "prawn crackers",
    ):
        if phrase in base:
            aliases.add(phrase)
            aliases.add(phrase.replace(" sauce", ""))

    return sorted(a for a in aliases if a)


def score_candidate(query: str, aliases: List[str], candidate_name: str) -> float:
    q = query.strip()
    if not q or not aliases:
        return 0.0

    q_tokens = tokenize_for_match(q)
    q_head = head_token(q)
    best = 0.0

    for alias in aliases:
        alias_tokens = tokenize_for_match(alias)

        # exact
        if q == alias:
            best = max(best, 1.0)

        # substring / contained phrase
        if q and alias and (q in alias or alias in q):
            best = max(best, 0.93)

        # sequence similarity
        best = max(best, similarity(q, alias))

        # token overlap
        overlap = token_overlap_score(q_tokens, alias_tokens)
        best = max(best, overlap * 0.9)

        # strong headword boost: "beef" -> beef dish
        a_head = head_token(alias)
        if q_head and a_head and q_head == a_head and q_head in _HEADWORDS:
            best = max(best, 0.88)

        # single-token exact
        if len(q_tokens) == 1 and q_tokens == alias_tokens:
            best = max(best, 0.95)

    # slight extra comparison against raw candidate name
    best = max(best, similarity(q, candidate_name))
    return min(best, 1.0)