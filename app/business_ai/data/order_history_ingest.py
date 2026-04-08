#app/business_ai/data/order_history_ingest.py
from __future__ import annotations

import csv
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from app.business_ai.utils.item_ids import slugify_text
from app.business_ai.utils.parsing import clean_text, normalise_created_at, parse_float, parse_int

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    from app.business_ai.services.order_history_vision import (
        extract_order_history_from_image_with_ai,
    )
except Exception:
    extract_order_history_from_image_with_ai = None


logger = logging.getLogger(__name__)


def looks_like_order_id(value: str) -> bool:
    return bool(re.match(r"^order_[A-Za-z0-9_-]+$", clean_text(value)))


def looks_like_datetime_line(value: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$", clean_text(value)))


def looks_like_total_line(value: str) -> bool:
    text = clean_text(value)
    return bool(re.match(r"^(?:(?:GBP|£)\s*)?[0-9]+(?:\.[0-9]+)?$", text, flags=re.I))


def parse_items_blob(items_blob: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    blob = clean_text(items_blob).replace("\n", " ")
    blob = re.sub(r"\s+", " ", blob).strip().rstrip(",")

    parts = [part.strip(" ,") for part in blob.split(",") if part.strip(" ,")]

    for part in parts:
        match = re.match(r"^([A-Za-z0-9_&/\- ]+?)\s+x(\d+)$", part, flags=re.I)
        if not match:
            continue

        item_id = slugify_text(match.group(1))
        quantity = parse_int(match.group(2), default=1)

        items.append(
            {
                "id": item_id,
                "quantity": quantity,
                "price": 0.0,
            }
        )

    return items


def parse_json_orders(file_bytes: bytes) -> list[dict[str, Any]]:
    raw = json.loads(file_bytes.decode("utf-8", errors="ignore"))

    if isinstance(raw, dict) and isinstance(raw.get("orders"), list):
        return raw["orders"]

    if isinstance(raw, list):
        return raw

    raise ValueError(
        "Uploaded JSON must be a list of orders or an object like {'orders': [...]}."
    )


def extract_rows_from_csv(file_bytes: bytes) -> list[dict[str, str]]:
    text = file_bytes.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def parse_csv_orders(file_bytes: bytes) -> list[dict[str, Any]]:
    rows = extract_rows_from_csv(file_bytes)
    if not rows:
        return []

    orders_by_id: dict[str, dict[str, Any]] = {}

    for row in rows:
        lowered = {clean_text(k).lower(): clean_text(v) for k, v in row.items()}

        order_id = lowered.get("order_id") or lowered.get("id")
        created_at = lowered.get("created_at") or lowered.get("timestamp") or lowered.get("date")
        item_id = lowered.get("item_id") or lowered.get("item") or lowered.get("item_name")
        quantity = lowered.get("quantity") or "1"
        price = lowered.get("price") or lowered.get("item_price") or "0"
        total = lowered.get("total") or lowered.get("order_total") or ""

        if not order_id or not created_at or not item_id:
            continue

        if order_id not in orders_by_id:
            orders_by_id[order_id] = {
                "id": clean_text(order_id),
                "created_at": normalise_created_at(created_at),
                "items": [],
                "total": parse_float(total, default=0.0),
            }

        orders_by_id[order_id]["items"].append(
            {
                "id": slugify_text(item_id),
                "quantity": parse_int(quantity, default=1),
                "price": parse_float(price, default=0.0),
            }
        )

    for order in orders_by_id.values():
        if not order.get("total"):
            computed_total = sum(
                parse_int(item.get("quantity"), 0) * parse_float(item.get("price"), 0.0)
                for item in order.get("items", [])
            )
            order["total"] = round(computed_total, 2)

    return list(orders_by_id.values())


def extract_text_from_pdf(file_bytes: bytes) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf is required for PDF order-history support.")

    reader = PdfReader(io.BytesIO(file_bytes))
    parts: list[str] = []

    for page in reader.pages:
        parts.append(page.extract_text() or "")

    text = "\n".join(parts).strip()
    if not text:
        raise RuntimeError("This PDF appears to be image-only or unreadable as text.")

    return text


def parse_pdf_export_rows(text: str) -> list[dict[str, Any]]:
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]

    cleaned_lines: list[str] = []
    for line in lines:
        lowered = line.lower()
        if lowered.startswith("page "):
            continue
        if lowered in {"order id", "created at", "items", "total"}:
            continue
        if lowered.startswith("order history export"):
            continue
        if lowered.startswith("generated rows:"):
            continue
        if lowered.startswith("period:"):
            continue
        if lowered.startswith("supported companion file:"):
            continue
        cleaned_lines.append(line)

    orders: list[dict[str, Any]] = []
    i = 0

    while i < len(cleaned_lines):
        line = cleaned_lines[i]

        if not looks_like_order_id(line):
            i += 1
            continue

        order_id = line
        i += 1

        if i >= len(cleaned_lines):
            break

        created_at_line = cleaned_lines[i]
        if not looks_like_datetime_line(created_at_line):
            continue

        created_at = normalise_created_at(created_at_line)
        i += 1

        item_lines: list[str] = []
        total = 0.0

        while i < len(cleaned_lines):
            current = cleaned_lines[i]

            if looks_like_order_id(current):
                break

            if looks_like_total_line(current):
                total = parse_float(current, default=0.0)
                i += 1
                break

            item_lines.append(current)
            i += 1

        items_blob = " ".join(item_lines).strip()
        items_blob = re.sub(r"\s+", " ", items_blob).rstrip(",")
        items = parse_items_blob(items_blob)

        logger.debug(
            "Parsed PDF order block",
            extra={
                "order_id": order_id,
                "created_at": created_at,
                "items_count": len(items),
                "total": total,
            },
        )

        if items:
            orders.append(
                {
                    "id": order_id,
                    "created_at": created_at,
                    "items": items,
                    "total": total,
                }
            )

    return orders


def looks_like_csv_order_headers(text: str) -> bool:
    first_lines = [clean_text(line).lower() for line in text.splitlines()[:5] if clean_text(line)]
    joined = " | ".join(first_lines)

    csv_signals = [
        "order_id",
        "created_at",
        "timestamp",
        "item_id",
        "item_name",
        "quantity",
        "price",
        "total",
    ]

    return any(signal in joined for signal in csv_signals) and "," in joined


def parse_pdf_orders(file_bytes: bytes) -> list[dict[str, Any]]:
    text = extract_text_from_pdf(file_bytes)
    logger.debug("PDF text extracted successfully")

    try:
        raw = json.loads(text)
        if isinstance(raw, dict) and isinstance(raw.get("orders"), list):
            logger.debug("PDF parser mode: JSON object")
            return raw["orders"]
        if isinstance(raw, list):
            logger.debug("PDF parser mode: JSON list")
            return raw
    except Exception:
        pass

    if looks_like_csv_order_headers(text):
        try:
            csv_orders = parse_csv_orders(text.encode("utf-8"))
            logger.debug("PDF parser mode: CSV-like")
            if csv_orders:
                return csv_orders
        except Exception:
            logger.exception("CSV-like PDF parse failed")

    export_orders = parse_pdf_export_rows(text)
    logger.debug("PDF parser mode: export blocks")

    if export_orders:
        return export_orders

    raise ValueError(
        "Could not parse PDF order history into valid orders. "
        "Please use JSON or CSV for now, or export a cleaner PDF."
    )


def parse_image_orders(file_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    if extract_order_history_from_image_with_ai is None:
        raise RuntimeError(
            "Image upload support requires app.business_ai.services.order_history_vision.extract_order_history_from_image_with_ai"
        )

    extracted = extract_order_history_from_image_with_ai(
        image_bytes=file_bytes,
        filename=filename,
    )

    if isinstance(extracted, dict) and isinstance(extracted.get("orders"), list):
        return extracted["orders"]

    if isinstance(extracted, list):
        return extracted

    raise ValueError("Image extractor did not return a valid order-history structure.")


def ingest_order_history_file_to_dataset(
    *,
    file_bytes: bytes,
    filename: str,
) -> dict[str, Any]:
    ext = Path(filename or "").suffix.lower()

    if ext == ".json":
        orders = parse_json_orders(file_bytes)
    elif ext == ".csv":
        orders = parse_csv_orders(file_bytes)
    elif ext == ".pdf":
        orders = parse_pdf_orders(file_bytes)
    elif ext in {".jpg", ".jpeg", ".png"}:
        orders = parse_image_orders(file_bytes, filename)
    else:
        raise RuntimeError(
            f"Unsupported file type: {ext}. Supported: .json, .csv, .pdf, .jpg, .jpeg, .png."
        )

    if not orders:
        raise ValueError("No valid orders were found in the uploaded file.")

    return {
        "orders": orders,
        "warnings": [],
    }


def import_orders_from_json_file(path: str):
    with open(path, "rb") as f:
        dataset = ingest_order_history_file_to_dataset(
            file_bytes=f.read(),
            filename=path,
        )
    return dataset["orders"]