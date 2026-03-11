# app/services/menu_ingest.py
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "item"


def slugify_dash(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "restaurant"


def parse_price(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace("£", "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d{1,2})?", text)
    if not match:
        raise ValueError(f"Could not parse price from: {value}")
    return float(match.group(0))


def clean_text(value: Any) -> str:
    return str(value).strip()


def normalize_options(options: dict[str, list[str]] | None) -> dict[str, list[str]]:
    options = options or {}
    out: dict[str, list[str]] = {}
    for key, values in options.items():
        out[clean_text(key)] = [clean_text(v) for v in values]
    return out


def normalize_extras(extras: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    extras = extras or []
    out = []
    for extra in extras:
        out.append(
            {
                "name": clean_text(extra["name"]),
                "price": parse_price(extra["price"]),
            }
        )
    return out


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    name = clean_text(item["name"])
    item_id = clean_text(item.get("id") or slugify(name))

    return {
        "id": item_id,
        "name": name,
        "base_price": parse_price(item["base_price"]),
        "options": normalize_options(item.get("options")),
        "extras": normalize_extras(item.get("extras")),
    }


def normalize_category(category: dict[str, Any]) -> dict[str, Any]:
    name = clean_text(category["name"])
    category_id = clean_text(category.get("id") or slugify(name))
    items = category.get("items") or []

    return {
        "id": category_id,
        "name": name,
        "items": [normalize_item(item) for item in items],
    }


def build_menu_dataset(
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
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    slug = slug or slugify_dash(business_name)

    return {
        "restaurant": {
            "slug": slug,
            "name": business_name,
            "currency": currency,
            "pickup_only": pickup_only,
            "email": email,
            "phone": phone,
            "address": address,
            "opening_hours": opening_hours,
        },
        "categories": [normalize_category(cat) for cat in categories],
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
    r"^(?:choice of\s+)?(sauces?|salad|size|sizes|drinks?|spice level|cheese)[:\-]\s*(.+)$",
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
    return (
        line.isupper()
        or line.istitle()
        or line.lower() in {"wraps", "kebabs", "burgers", "sides", "drinks", "starters", "pizzas", "calzones"}
    )


def clean_item_name(name: str) -> str:
    name = name.strip(" -.\t")
    name = re.sub(r"^(?:\d+[\).\s-]+)", "", name)
    return name.strip()


# ----------------------------
# PARSERS
# ----------------------------

def parse_existing_json_menu(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    if "categories" in raw and isinstance(raw["categories"], list):
        return raw["categories"], []
    raise ValueError("JSON file does not contain a recognizable 'categories' structure.")


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
        extras: list[dict[str, Any]] = []

        options_json = lowered.get("options_json", "")
        extras_json = lowered.get("extras_json", "")

        if options_json:
            try:
                options = json.loads(options_json)
            except Exception:
                warnings.append(f"CSV row {idx}: invalid options_json")

        if extras_json:
            try:
                extras = json.loads(extras_json)
            except Exception:
                warnings.append(f"CSV row {idx}: invalid extras_json")

        if category_name not in categories_map:
            categories_map[category_name] = {
                "name": category_name,
                "items": [],
            }

        categories_map[category_name]["items"].append(
            {
                "name": item_name,
                "base_price": price,
                "options": options,
                "extras": extras,
            }
        )

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
        # category
        if looks_like_category(line):
            current_category = {"name": line.title(), "items": []}
            categories.append(current_category)
            last_item = None
            continue

        # option hints tied to previous item
        option_match = OPTION_HINT_RE.match(line)
        if option_match and last_item is not None:
            option_name = option_match.group(1).lower().strip()
            option_values = [v.strip() for v in option_match.group(2).split(",") if v.strip()]

            normalized_key = "options"
            if "sauce" in option_name:
                normalized_key = "sauce"
            elif "salad" in option_name:
                normalized_key = "salad"
            elif "size" in option_name:
                normalized_key = "size"
            elif "drink" in option_name:
                normalized_key = "drink_choice"
            elif "spice" in option_name:
                normalized_key = "spice_level"
            else:
                normalized_key = option_name.replace(" ", "_")

            last_item.setdefault("options", {})
            last_item["options"][normalized_key] = option_values
            continue

        # extras tied to previous item
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

        # normal item line with price
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

        # fallback: item name on one line, price on next line
        if PRICE_RE.fullmatch(line.replace("£", "").strip()) and last_item is None and categories:
            warnings.append(f"Standalone price line could not be attached: {line}")
            continue

        # if line contains a price anywhere, attempt looser parse
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

        # possible continuation or note
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

    if ext == ".json":
        raw = json.loads(file_bytes.decode("utf-8", errors="ignore"))
        categories, warnings = parse_existing_json_menu(raw)

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

    return build_menu_dataset(
        business_name=business_name,
        email=email,
        phone=phone,
        address=address,
        opening_hours=opening_hours,
        categories=categories,
        currency=currency,
        pickup_only=pickup_only,
        warnings=warnings,
    )