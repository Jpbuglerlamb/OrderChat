from __future__ import annotations

import json
from pathlib import Path

from app.services.qr_service import build_restaurant_public_url, generate_qr_png_bytes

# Change this to your current base URL
BASE_URL = "https://jpaiplatform.com"

# Your menus live here
MENUS_DIR = Path(__file__).resolve().parents[1] / "data"

OUT_DIR = Path(__file__).resolve().parents[1] / "qrcodes"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    menu_paths = list(MENUS_DIR.rglob("menu.json"))
    if not menu_paths:
        raise SystemExit(f"No menu.json files found under {MENUS_DIR}")

    made = 0
    for p in menu_paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        slug = ((data.get("restaurant") or {}).get("slug") or "").strip().lower()
        if not slug:
            print(f"SKIP (no restaurant.slug): {p}")
            continue

        url = build_restaurant_public_url(BASE_URL, slug)
        qr_bytes = generate_qr_png_bytes(url)

        folder = p.parent.name
        out_path = OUT_DIR / f"{folder}__{slug}.png"
        out_path.write_bytes(qr_bytes)

        print(f"OK  {slug}  ->  {out_path}  ({url})")
        made += 1

    print(f"\nDone. Generated {made} QR codes in: {OUT_DIR}")


if __name__ == "__main__":
    main()