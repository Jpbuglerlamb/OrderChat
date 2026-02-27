from __future__ import annotations

from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.ordering.cart import load_cart, dump_cart, recalc_line_total
from app.ordering.menu import build_menu_index, menu_synonyms, currency_symbol, find_item


router = APIRouter()


# --- You MUST adapt these 2 functions to your existing session store ---
def get_session_state(request: Request) -> Dict[str, Any]:
    """
    Return mutable session dict containing at least:
      - items_json: str
      - state_json: str (optional)
    Replace this with your existing session storage.
    """
    s = getattr(request.app.state, "sessions", None)
    if s is None:
        request.app.state.sessions = {}
        s = request.app.state.sessions

    # simplest demo key (replace with your real session id / cookie)
    key = request.client.host + ":" + (request.headers.get("user-agent") or "")
    if key not in s:
        s[key] = {"items_json": "[]", "state_json": "{}"}
    return s[key]


def set_items_json(request: Request, items_json: str) -> None:
    sess = get_session_state(request)
    sess["items_json"] = items_json


# --- API models ---
class UpdateQtyIn(BaseModel):
    item_id: str
    delta: int


class RemoveIn(BaseModel):
    item_id: str


def _cart_payload(menu_dict: Dict[str, Any], cart: List[Dict[str, Any]]) -> Dict[str, Any]:
    cur = currency_symbol(menu_dict)
    total = 0.0
    for ln in cart:
        try:
            total += float(ln.get("line_total") or 0.0)
        except Exception:
            pass

    return {
        "currency_symbol": cur,
        "total": round(total, 2),
        "items": [
            {
                "item_id": str(ln.get("item_id") or ""),
                "name": str(ln.get("name") or "Item"),
                "qty": int(ln.get("qty") or 1),
                "base_price": float(ln.get("base_price") or 0.0),
                "line_total": float(ln.get("line_total") or 0.0),
            }
            for ln in cart
        ],
    }


@router.get("/r/{slug}/cart")
def get_cart(slug: str, request: Request):
    # however you load restaurant menu_dict today:
    menu_dict = request.app.state.menu_store.get(slug)  # <-- adapt
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Unknown restaurant")

    sess = get_session_state(request)
    cart = load_cart(sess.get("items_json") or "[]")
    return _cart_payload(menu_dict, cart)


@router.post("/r/{slug}/cart/update")
def update_qty(slug: str, request: Request, data: UpdateQtyIn):
    menu_dict = request.app.state.menu_store.get(slug)  # <-- adapt
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Unknown restaurant")

    sess = get_session_state(request)
    cart = load_cart(sess.get("items_json") or "[]")

    item_id = (data.item_id or "").strip()
    delta = int(data.delta or 0)
    if not item_id or delta == 0:
        raise HTTPException(status_code=400, detail="item_id and delta required")

    # Try to find existing line
    for ln in cart:
        if str(ln.get("item_id") or "") == item_id:
            ln["qty"] = max(0, int(ln.get("qty") or 1) + delta)
            if ln["qty"] <= 0:
                cart = [x for x in cart if str(x.get("item_id") or "") != item_id]
            else:
                recalc_line_total(ln)
            items_json = dump_cart(cart)
            set_items_json(request, items_json)
            return _cart_payload(menu_dict, cart)

    # If not in cart and delta is positive, add it (nice UX)
    if delta > 0:
        synonyms = menu_synonyms(menu_dict)
        menu = build_menu_index(menu_dict, synonyms)
        item = find_item(menu, item_id, synonyms)  # assumes item_id works
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        new_line = {
            "item_id": str(item.get("id", "")),
            "name": str(item.get("name") or item.get("title") or item.get("item") or "Item"),
            "qty": delta,
            "base_price": float(item.get("base_price", 0.0) or 0.0),
            "choices": {},
            "extras": [],
            "line_total": 0.0,
        }
        recalc_line_total(new_line)
        cart.append(new_line)

    items_json = dump_cart(cart)
    set_items_json(request, items_json)
    return _cart_payload(menu_dict, cart)


@router.post("/r/{slug}/cart/remove")
def remove_line(slug: str, request: Request, data: RemoveIn):
    menu_dict = request.app.state.menu_store.get(slug)  # <-- adapt
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Unknown restaurant")

    sess = get_session_state(request)
    cart = load_cart(sess.get("items_json") or "[]")

    item_id = (data.item_id or "").strip()
    cart = [ln for ln in cart if str(ln.get("item_id") or "") != item_id]

    items_json = dump_cart(cart)
    set_items_json(request, items_json)
    return _cart_payload(menu_dict, cart)


@router.post("/r/{slug}/cart/clear")
def clear_cart(slug: str, request: Request):
    menu_dict = request.app.state.menu_store.get(slug)  # <-- adapt
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Unknown restaurant")

    set_items_json(request, "[]")
    return _cart_payload(menu_dict, [])