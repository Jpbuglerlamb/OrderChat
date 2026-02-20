from __future__ import annotations

import json
from pathlib import Path

import qrcode

# Change this to your current base URL
BASE_URL = "https://orderchat-eidt.onrender.com"

# Your menus live here (based on your project tree)
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
        slug = ((data.get("meta") or {}).get("slug") or "").strip().lower()
        if not slug:
            print(f"SKIP (no meta.slug): {p}")
            continue

        url = f"{BASE_URL}/r/{slug}"
        img = qrcode.make(url)

        # Name files by folder + slug so it's obvious
        folder = p.parent.name
        out_path = OUT_DIR / f"{folder}__{slug}.png"
        img.save(out_path)

        print(f"OK  {slug}  ->  {out_path}  ({url})")
        made += 1

    print(f"\nDone. Generated {made} QR codes in: {OUT_DIR}")

if __name__ == "__main__":
    main()