from __future__ import annotations

import re
from difflib import get_close_matches
from typing import Any


def canonicalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", "and")
    text = text.replace("_", " ")
    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def slugify_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def display_name_from_id(value: str) -> str:
    text = str(value or "").strip().replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.title() if text else ""


def _extra_variants(value: str) -> set[str]:
    variants: set[str] = set()
    if not value:
        return variants

    variants.add(value)
    variants.add(value.replace(" ", ""))
    variants.add(slugify_text(value))
    variants.add(value.replace(" ", "_"))

    if value.endswith("s"):
        singular = value[:-1].strip()
        if singular:
            variants.add(singular)
            variants.add(singular.replace(" ", ""))
            variants.add(singular.replace(" ", "_"))
    else:
        plural = f"{value}s"
        variants.add(plural)
        variants.add(plural.replace(" ", ""))
        variants.add(plural.replace(" ", "_"))

    return {v for v in variants if v}


def build_menu_lookup(
    menu_data: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    item_lookup: dict[str, dict[str, Any]] = {}
    alias_lookup: dict[str, str] = {}

    if not menu_data:
        return item_lookup, alias_lookup

    for item in menu_data.get("items", []) or []:
        raw_item_id = str(item.get("id") or "").strip()
        raw_item_name = str(item.get("name") or "").strip()

        if not raw_item_id:
            continue

        canonical_item_id = slugify_text(raw_item_id)
        canonical_item_name = canonicalize_text(raw_item_name)

        item_lookup[canonical_item_id] = item

        variants = set()
        variants |= _extra_variants(canonicalize_text(raw_item_id))
        variants |= _extra_variants(canonical_item_name)

        simplified_name = re.sub(
            r"\b(size|large|small|regular|portion|meal|deal|sauce|can|bottle)\b",
            "",
            canonical_item_name,
        )
        simplified_name = re.sub(r"\s+", " ", simplified_name).strip()
        variants |= _extra_variants(simplified_name)

        for variant in variants:
            alias_lookup[variant] = canonical_item_id

    manual_aliases = {
        "coke_can": "coca_cola",
        "coke": "coca_cola",
        "coca cola": "coca_cola",
        "sweet sour chicken": "sweet_sour_chicken",
        "sweet and sour chicken": "sweet_sour_chicken",
        "hot sour soup": "hot_sour_soup",
        "hot and sour soup": "hot_sour_soup",
        "chicken chow mein": "chicken_chow_mein",
        "egg fried rice": "egg_fried_rice",
        "spring rolls": "spring_rolls_2",
    }

    for raw_alias, canonical in manual_aliases.items():
        canonical_slug = slugify_text(canonical)
        if canonical_slug in item_lookup:
            alias_lookup[canonicalize_text(raw_alias)] = canonical_slug
            alias_lookup[slugify_text(raw_alias)] = canonical_slug
            alias_lookup[canonicalize_text(raw_alias).replace(" ", "")] = canonical_slug

    return item_lookup, alias_lookup


def resolve_item_to_menu(
    raw_item_id: str,
    item_lookup: dict[str, dict[str, Any]],
    alias_lookup: dict[str, str],
) -> dict[str, Any]:
    raw_value = str(raw_item_id or "").strip()
    canonical = canonicalize_text(raw_value)

    if not canonical:
        return {
            "matched": False,
            "canonical_id": None,
            "menu_name": None,
            "raw_item_id": raw_value,
            "match_method": "empty",
        }

    candidate_keys = [
        canonical,
        canonical.replace(" ", ""),
        slugify_text(raw_value),
        slugify_text(raw_value).replace("_", ""),
    ]

    if canonical.endswith("s"):
        singular = canonical[:-1].strip()
        if singular:
            candidate_keys.extend([singular, singular.replace(" ", ""), singular.replace(" ", "_")])
    else:
        plural = f"{canonical}s"
        candidate_keys.extend([plural, plural.replace(" ", ""), plural.replace(" ", "_")])

    for key in candidate_keys:
        if key in alias_lookup:
            canonical_id = alias_lookup[key]
            menu_item = item_lookup.get(canonical_id, {})
            return {
                "matched": True,
                "canonical_id": canonical_id,
                "menu_name": menu_item.get("name") or display_name_from_id(canonical_id),
                "raw_item_id": raw_value,
                "match_method": "exact",
            }

    for lookup_key, canonical_id in alias_lookup.items():
        if canonical in lookup_key or lookup_key in canonical:
            menu_item = item_lookup.get(canonical_id, {})
            return {
                "matched": True,
                "canonical_id": canonical_id,
                "menu_name": menu_item.get("name") or display_name_from_id(canonical_id),
                "raw_item_id": raw_value,
                "match_method": "containment",
            }

    possible_keys = list(alias_lookup.keys())
    close = get_close_matches(canonical, possible_keys, n=1, cutoff=0.82)
    if close:
        canonical_id = alias_lookup[close[0]]
        menu_item = item_lookup.get(canonical_id, {})
        return {
            "matched": True,
            "canonical_id": canonical_id,
            "menu_name": menu_item.get("name") or display_name_from_id(canonical_id),
            "raw_item_id": raw_value,
            "match_method": "fuzzy",
        }

    raw_tokens = set(canonical.split())
    best_key: str | None = None
    best_score = 0

    for lookup_key in possible_keys:
        lookup_tokens = set(canonicalize_text(lookup_key).split())
        overlap = len(raw_tokens & lookup_tokens)
        if overlap > best_score:
            best_score = overlap
            best_key = lookup_key

    if best_key and best_score >= 1:
        canonical_id = alias_lookup[best_key]
        menu_item = item_lookup.get(canonical_id, {})
        return {
            "matched": True,
            "canonical_id": canonical_id,
            "menu_name": menu_item.get("name") or display_name_from_id(canonical_id),
            "raw_item_id": raw_value,
            "match_method": "token_overlap",
        }

    return {
        "matched": False,
        "canonical_id": slugify_text(raw_value) or None,
        "menu_name": None,
        "raw_item_id": raw_value,
        "match_method": "unmatched",
    }