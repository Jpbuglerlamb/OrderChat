# app/services/menu_ingest.py
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any


# ----------------------------
# BASIC HELPERS
# ----------------------------

def slugify(value: str) -> str:
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "item"


def slugify_dash(value: str) -> str:
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "restaurant"


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def parse_price(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)

    text = clean_text(value).replace("£", "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d{1,2})?", text)
    if not match:
        raise ValueError(f"Could not parse price from: {value}")
    return float(match.group(0))


def prompt_for_option(key: str) -> str:
    key = clean_text(key).lower()
    prompts = {
        "size": "Choose size:",
        "dip": "Choose a dip:",
        "side": "Choose a side:",
        "drink": "Choose a drink:",
        "drink_choice": "Choose a drink:",
        "spice": "Spice level:",
        "spice_level": "Spice level:",
        "sauce": "Choose a sauce:",
        "salad": "Choose a salad:",
        "notes": "Any preference?",
        "type": "Choose:",
    }
    return prompts.get(key, f"Choose {key.replace('_', ' ')}:")


# ----------------------------
# NORMALIZERS TO NEW SCHEMA
# ----------------------------

def normalize_extras(extras: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    extras = extras or []
    out: list[dict[str, Any]] = []

    for extra in extras:
        if not isinstance(extra, dict):
            continue

        name = clean_text(extra.get("name"))
        if not name:
            continue

        out.append(
            {
                "name": name,
                "price": parse_price(extra.get("price", 0)),
            }
        )

    return out


def normalize_modifiers_from_options(
    options: dict[str, list[str]] | None,
) -> list[dict[str, Any]]:
    options = options or {}
    out: list[dict[str, Any]] = []

    for raw_key, values in options.items():
        key = slugify(raw_key)
        cleaned_values = [clean_text(v) for v in (values or []) if clean_text(v)]
        if not cleaned_values:
            continue

        out.append(
            {
                "key": key,
                "prompt": prompt_for_option(key),
                "required": True,
                "multi": False,
                "options": cleaned_values,
            }
        )

    return out


def normalize_modifiers(
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    # New format already has modifiers
    raw_modifiers = item.get("modifiers")
    if isinstance(raw_modifiers, list):
        out: list[dict[str, Any]] = []
        for mod in raw_modifiers:
            if not isinstance(mod, dict):
                continue

            key = clean_text(mod.get("key") or slugify(mod.get("prompt", "option")))
            prompt = clean_text(mod.get("prompt") or "Choose an option:")
            required = bool(mod.get("required", True))
            multi = bool(mod.get("multi", False))
            options = [clean_text(v) for v in mod.get("options", []) if clean_text(v)]

            if not key or not options:
                continue

            out.append(
                {
                    "key": key,
                    "prompt": prompt,
                    "required": required,
                    "multi": multi,
                    "options": options,
                }
            )
        return out

    # Old format had options dict
    raw_options = item.get("options")
    if isinstance(raw_options, dict):
        return normalize_modifiers_from_options(raw_options)

    return []


def normalize_item(item: dict[str, Any], category_id: str) -> dict[str, Any]:
    name = clean_text(item.get("name"))
    if not name:
        raise ValueError("Item is missing name")

    item_id = clean_text(item.get("id")) or slugify(name)

    base_price_raw = item.get("base_price", item.get("price", 0))

    return {
        "id": item_id,
        "name": name,
        "category_id": category_id,
        "base_price": parse_price(base_price_raw),
        "modifiers": normalize_modifiers(item),
        "extras": normalize_extras(item.get("extras")),
    }


def categories_items_to_canonical(
    *,
    business_name: str,
    email: str,
    phone: str,
    address: str,
    opening_hours: str,
    categories: list[dict[str, Any]],
    slug: str | None = None,
    currency: str = "GBP",
    pickup_only: bool = True,
    notes_allowed: bool = True,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    slug = slug or slugify_dash(business_name)

    out_categories: list[dict[str, str]] = []
    out_items: list[dict[str, Any]] = []
    seen_category_ids: set[str] = set()
    seen_item_ids: set[str] = set()

    for category in categories:
        cat_name = clean_text(category.get("name"))
        if not cat_name:
            continue

        cat_id = clean_text(category.get("id")) or slugify(cat_name)

        if cat_id not in seen_category_ids:
            out_categories.append({"id": cat_id, "name": cat_name})
            seen_category_ids.add(cat_id)

        for raw_item in category.get("items", []) or []:
            if not isinstance(raw_item, dict):
                continue

            item = normalize_item(raw_item, cat_id)

            # Prevent duplicate ids by suffixing
            original_id = item["id"]
            counter = 2
            while item["id"] in seen_item_ids:
                item["id"] = f"{original_id}_{counter}"
                counter += 1

            seen_item_ids.add(item["id"])
            out_items.append(item)

    return {
        "meta": {
            "slug": slug,
            "currency": currency,
            "notes_allowed": notes_allowed,
            "order_email": email,
            # Kept for convenience, harmless for consumers that ignore them
            "business_name": business_name,
            "phone": phone,
            "address": address,
            "opening_hours": opening_hours,
            "pickup_only": pickup_only,
        },
        "categories": out_categories,
        "items": out_items,
        "warnings": warnings or [],
    }


def save_menu_json(menu_data: dict[str, Any], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(menu_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


# ----------------------------
# FILE EXTRACTION
# ----------------------------

def extract_text_from_txt(file_bytes: bytes) -> str:
    return file_bytes.decode("utf-8", errors="ignore")


def extract_rows_from_csv(file_bytes: bytes) -> list[dict[str, str]]:
    text = file_bytes.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("pypdf is required to parse PDF menus.") from e

    reader = PdfReader(io.BytesIO(file_bytes))
    parts: list[str] = []

    for page in reader.pages:
        parts.append(page.extract_text() or "")

    text = "\n".join(parts).strip()
    if not text:
        raise RuntimeError(
            "This PDF appears to be image-only or unreadable as text. "
            "Use OCR/AI vision/manual review for this file."
        )
    return text


# ----------------------------
# PATTERN HELPERS
# ----------------------------

PRICE_RE = re.compile(r"£?\s*(\d+(?:\.\d{1,2})?)")
ITEM_WITH_PRICE_RE = re.compile(
    r"^(?:\d+[\).\s-]+)?(.+?)(?:\s*[.\-·]{2,}\s*|\s+-\s+|\s+)\£?\s*(\d+(?:\.\d{1,2})?)$"
)
SIZE_UPGRADE_RE = re.compile(
    r"^(small|regular|large|xl|meal|can|bottle)\s*\+?\s*£?\s*(\d+(?:\.\d{1,2})?)$",
    re.I,
)
EXTRA_LINE_RE = re.compile(r"^(?:add|extra)\s+(.+?)\s+£?\s*(\d+(?:\.\d{1,2})?)$", re.I)
OPTION_HINT_RE = re.compile(
    r"^(?:choice of\s+)?(sauces?|salad|size|sizes|drinks?|spice level|cheese|type|dip|side|notes)[:\-]\s*(.+)$",
    re.I,
)


def looks_like_category(line: str) -> bool:
    line = line.strip()
    if not line:
        return False
    if PRICE_RE.search(line):
        return False
    if len(line) > 40:
        return False

    known = {
        "wraps",
        "kebabs",
        "burgers",
        "sides",
        "drinks",
        "starters",
        "pizzas",
        "calzones",
        "soups",
        "mains",
        "rice",
        "rice & noodles",
        "rice and noodles",
        "noodles",
        "desserts",
    }

    return line.isupper() or line.istitle() or line.lower() in known


def clean_item_name(name: str) -> str:
    name = name.strip(" -.\t")
    name = re.sub(r"^(?:\d+[\).\s-]+)", "", name)
    return name.strip()


def option_name_to_key(option_name: str) -> str:
    option_name = clean_text(option_name).lower()

    if "sauce" in option_name:
        return "sauce"
    if "salad" in option_name:
        return "salad"
    if "size" in option_name:
        return "size"
    if "drink" in option_name:
        return "drink"
    if "spice" in option_name:
        return "spice"
    if "dip" in option_name:
        return "dip"
    if "side" in option_name:
        return "side"
    if "type" in option_name:
        return "type"
    if "note" in option_name:
        return "notes"

    return slugify(option_name)


# ----------------------------
# PARSERS
# ----------------------------

def parse_existing_json_menu(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """
    Returns:
      categories_in_nested_form,
      extracted_meta,
      warnings

    Supports:
    1. Old format:
       {
         "restaurant": {...},
         "categories": [{"name": "...", "items": [...]}]
       }

    2. New format:
       {
         "meta": {...},
         "categories": [{"id": "...", "name": "..."}],
         "items": [{"category_id": "...", ...}]
       }
    """
    warnings: list[str] = []

    # Old nested format
    if (
        isinstance(raw.get("categories"), list)
        and raw["categories"]
        and isinstance(raw["categories"][0], dict)
        and "items" in raw["categories"][0]
    ):
        extracted_meta: dict[str, Any] = {}

        restaurant = raw.get("restaurant") or {}
        if isinstance(restaurant, dict):
            extracted_meta = {
                "slug": restaurant.get("slug"),
                "currency": restaurant.get("currency"),
                "order_email": restaurant.get("email"),
                "business_name": restaurant.get("name"),
                "phone": restaurant.get("phone"),
                "address": restaurant.get("address"),
                "opening_hours": restaurant.get("opening_hours"),
                "pickup_only": restaurant.get("pickup_only"),
            }

        return raw["categories"], extracted_meta, raw.get("warnings", []) or warnings

    # New flat format
    if isinstance(raw.get("categories"), list) and isinstance(raw.get("items"), list):
        categories_by_id: dict[str, dict[str, Any]] = {}

        for cat in raw["categories"]:
            if not isinstance(cat, dict):
                continue
            cat_name = clean_text(cat.get("name"))
            if not cat_name:
                continue
            cat_id = clean_text(cat.get("id")) or slugify(cat_name)
            categories_by_id[cat_id] = {
                "id": cat_id,
                "name": cat_name,
                "items": [],
            }

        for item in raw["items"]:
            if not isinstance(item, dict):
                continue

            cat_id = clean_text(item.get("category_id")) or "uncategorized"
            if cat_id not in categories_by_id:
                categories_by_id[cat_id] = {
                    "id": cat_id,
                    "name": cat_id.replace("_", " ").title(),
                    "items": [],
                }

            categories_by_id[cat_id]["items"].append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "base_price": item.get("base_price", 0),
                    "modifiers": item.get("modifiers", []),
                    "extras": item.get("extras", []),
                }
            )

        extracted_meta = raw.get("meta", {}) if isinstance(raw.get("meta"), dict) else {}
        return list(categories_by_id.values()), extracted_meta, raw.get("warnings", []) or warnings

    raise ValueError("JSON file does not contain a recognizable menu structure.")


def parse_csv_menu(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    if not rows:
        return [], []

    categories_map: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    for idx, row in enumerate(rows, start=2):
        lowered = {str(k).strip().lower(): (v or "").strip() for k, v in row.items()}

        category_name = lowered.get("category") or lowered.get("category_name")
        item_name = lowered.get("name") or lowered.get("item_name")
        price = lowered.get("price") or lowered.get("base_price")

        if not category_name or not item_name or not price:
            warnings.append(f"CSV row {idx}: missing category/name/price")
            continue

        options: dict[str, list[str]] = {}
        modifiers: list[dict[str, Any]] = []
        extras: list[dict[str, Any]] = []

        options_json = lowered.get("options_json", "")
        modifiers_json = lowered.get("modifiers_json", "")
        extras_json = lowered.get("extras_json", "")

        if modifiers_json:
            try:
                loaded = json.loads(modifiers_json)
                if isinstance(loaded, list):
                    modifiers = loaded
            except Exception:
                warnings.append(f"CSV row {idx}: invalid modifiers_json")

        if options_json:
            try:
                loaded = json.loads(options_json)
                if isinstance(loaded, dict):
                    options = loaded
            except Exception:
                warnings.append(f"CSV row {idx}: invalid options_json")

        if extras_json:
            try:
                loaded = json.loads(extras_json)
                if isinstance(loaded, list):
                    extras = loaded
            except Exception:
                warnings.append(f"CSV row {idx}: invalid extras_json")

        if category_name not in categories_map:
            categories_map[category_name] = {
                "name": category_name,
                "items": [],
            }

        item_payload: dict[str, Any] = {
            "name": item_name,
            "base_price": price,
            "extras": extras,
        }

        if modifiers:
            item_payload["modifiers"] = modifiers
        else:
            item_payload["options"] = options

        categories_map[category_name]["items"].append(item_payload)

    return list(categories_map.values()), warnings


def parse_text_menu_heuristic(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    categories: list[dict[str, Any]] = []
    warnings: list[str] = []
    current_category: dict[str, Any] | None = None
    last_item: dict[str, Any] | None = None

    def ensure_category(name: str = "Menu") -> dict[str, Any]:
        nonlocal current_category
        if current_category is None:
            current_category = {"name": name, "items": []}
            categories.append(current_category)
        return current_category

    for line in lines:
        if looks_like_category(line):
            current_category = {"name": line.title(), "items": []}
            categories.append(current_category)
            last_item = None
            continue

        option_match = OPTION_HINT_RE.match(line)
        if option_match and last_item is not None:
            option_name = option_match.group(1).lower().strip()
            option_values = [v.strip() for v in option_match.group(2).split(",") if v.strip()]
            normalized_key = option_name_to_key(option_name)

            last_item.setdefault("options", {})
            last_item["options"][normalized_key] = option_values
            continue

        extra_match = EXTRA_LINE_RE.match(line)
        if extra_match and last_item is not None:
            last_item.setdefault("extras", [])
            last_item["extras"].append(
                {
                    "name": extra_match.group(1).strip().title(),
                    "price": extra_match.group(2),
                }
            )
            continue

        item_match = ITEM_WITH_PRICE_RE.match(line)
        if item_match:
            category = ensure_category()
            item_name = clean_item_name(item_match.group(1))
            price = item_match.group(2)

            last_item = {
                "name": item_name,
                "base_price": price,
                "options": {},
                "extras": [],
            }
            category["items"].append(last_item)
            continue

        size_match = SIZE_UPGRADE_RE.match(line)
        if size_match and last_item is not None:
            size_name = size_match.group(1).title()
            price_delta = float(size_match.group(2))

            last_item.setdefault("options", {})
            existing_sizes = last_item["options"].get("size", ["Regular"])
            if "Regular" not in existing_sizes:
                existing_sizes.insert(0, "Regular")
            existing_sizes.append(f"{size_name} (+£{price_delta:.2f})")
            last_item["options"]["size"] = existing_sizes
            continue

        if PRICE_RE.fullmatch(line.replace("£", "").strip()) and last_item is None and categories:
            warnings.append(f"Standalone price line could not be attached: {line}")
            continue

        price_match = PRICE_RE.search(line)
        if price_match:
            category = ensure_category()
            raw_price = price_match.group(1)
            item_name = clean_item_name(line[:price_match.start()] or line.replace(price_match.group(0), ""))

            if item_name:
                last_item = {
                    "name": item_name,
                    "base_price": raw_price,
                    "options": {},
                    "extras": [],
                }
                category["items"].append(last_item)
            else:
                warnings.append(f"Could not determine item name from line: {line}")
            continue

        if last_item is not None:
            warnings.append(f"Unattached line after item '{last_item['name']}': {line}")
        else:
            warnings.append(f"Unparsed line: {line}")

    categories = [c for c in categories if c.get("items")]
    return categories, warnings


# ----------------------------
# MAIN INGESTION
# ----------------------------

def ingest_menu_file_to_dataset(
    *,
    file_bytes: bytes,
    filename: str,
    business_name: str,
    email: str,
    phone: str,
    address: str,
    opening_hours: str,
    currency: str = "GBP",
    pickup_only: bool = True,
) -> dict[str, Any]:
    ext = Path(filename).suffix.lower()

    extracted_meta: dict[str, Any] = {}
    warnings: list[str] = []
    categories: list[dict[str, Any]] = []

    if ext == ".json":
        raw = json.loads(file_bytes.decode("utf-8", errors="ignore"))
        categories, extracted_meta, warnings = parse_existing_json_menu(raw)

    elif ext == ".csv":
        rows = extract_rows_from_csv(file_bytes)
        categories, warnings = parse_csv_menu(rows)

    elif ext == ".txt":
        text = extract_text_from_txt(file_bytes)
        categories, warnings = parse_text_menu_heuristic(text)

    elif ext == ".pdf":
        text = extract_text_from_pdf(file_bytes)
        categories, warnings = parse_text_menu_heuristic(text)

    else:
        raise RuntimeError(
            f"Unsupported file type: {ext}. "
            "Supported: .json, .csv, .txt, .pdf (text-based PDFs only)."
        )

    effective_slug = clean_text(extracted_meta.get("slug")) or None
    effective_currency = clean_text(extracted_meta.get("currency")) or currency
    effective_email = clean_text(extracted_meta.get("order_email")) or email
    effective_business_name = clean_text(extracted_meta.get("business_name")) or business_name
    effective_phone = clean_text(extracted_meta.get("phone")) or phone
    effective_address = clean_text(extracted_meta.get("address")) or address
    effective_opening_hours = clean_text(extracted_meta.get("opening_hours")) or opening_hours
    effective_pickup_only = bool(extracted_meta.get("pickup_only", pickup_only))
    effective_notes_allowed = bool(extracted_meta.get("notes_allowed", True))

    return categories_items_to_canonical(
        business_name=effective_business_name,
        email=effective_email,
        phone=effective_phone,
        address=effective_address,
        opening_hours=effective_opening_hours,
        categories=categories,
        slug=effective_slug,
        currency=effective_currency,
        pickup_only=effective_pickup_only,
        notes_allowed=effective_notes_allowed,
        warnings=warnings,
    )