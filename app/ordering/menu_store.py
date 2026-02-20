from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .menu import build_menu_index, menu_synonyms

# Resolve menus directory robustly:
# 1) If MENUS_DIR env var is set, use it
# 2) Otherwise default to "<repo_root>/TakeawayDemo"
_THIS_FILE = Path(__file__).resolve()
APP_DIR  = _THIS_FILE.parents[1]
PROJECT_ROOT = APP_DIR.parent
DEFAULT_MENUS_DIR = PROJECT_ROOT / "data"

MENUS_DIR = Path(os.getenv("MENUS_DIR", str(DEFAULT_MENUS_DIR))).resolve()
print("\n===== MENU DEBUG =====")
print("menu_store file:", __file__)
print("MENUS_DIR:", MENUS_DIR)
print("MENUS_DIR exists:", MENUS_DIR.exists())

for p in MENUS_DIR.rglob("menu.json"):
    print("FOUND:", p)

print("======================\n")

_MENU_CACHE: Dict[str, Dict[str, Any]] = {}


def load_menu_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    slug = (slug or "").strip().lower()
    if not slug:
        return None

    # 1) cache hit
    if slug in _MENU_CACHE:
        return _MENU_CACHE[slug]

    # 2) scan menus on disk (simple + reliable for now)
    if not MENUS_DIR.exists():
        return None

    for path in MENUS_DIR.rglob("menu.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        mslug = ((data.get("meta") or {}).get("slug") or "").strip().lower()
        if mslug == slug:
            syn = menu_synonyms(data)
            build_menu_index(data, syn)

            _MENU_CACHE[slug] = data
            return data

    return None