# app/routes/cart_api.py
from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4
from typing import Any, Dict, List

from fastapi import APIRouter, Request, HTTPException, Depends, Response, Header, Cookie
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User, Order
from app.security.auth import decode_token, hash_password

from app.ordering.cart import load_cart, dump_cart, recalc_line_total
from app.ordering.menu import build_menu_index, menu_synonyms, currency_symbol, find_item
from app.ordering.menu_store import load_menu_by_slug

router = APIRouter()


# -------------------------
# Auth / guest identity (DB-backed cart)
# -------------------------
def require_user_id_or_guest(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    guest_id: str | None = Cookie(default=None, alias="guest_id"),
) -> int:
    # 1) Prefer authenticated user if token exists
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        uid = decode_token(token)
        if uid:
            return uid

    # 2) Otherwise cookie-based guest identity
    if not guest_id:
        guest_id = uuid4().hex

    guest_email = f"guest+{guest_id}@demo.local"

    u = db.query(User).filter(User.email == guest_email).first()
    if not u:
        u = User(
            name="Guest",
            email=guest_email,
            phone=None,
            password_hash=hash_password(uuid4().hex),
        )
        db.add(u)
        db.commit()
        db.refresh(u)

    response.set_cookie(
        key="guest_id",
        value=guest_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return u.id


def _safe_json_dict(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def get_or_create_draft(db: Session, user_id: int) -> Order:
    order = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.status == "draft")
        .order_by(Order.id.desc())
        .first()
    )
    if order:
        order.items_json = order.items_json or "[]"
        order.state_json = order.state_json or "{}"
        order.summary_text = order.summary_text or ""
        return order

    order = Order(
        user_id=user_id,
        status="draft",
        items_json="[]",
        state_json="{}",
        summary_text="",
        updated_at=datetime.utcnow(),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _ensure_order_scoped_to_restaurant(order: Order, slug: str) -> None:
    slug = (slug or "").strip().lower()
    state = _safe_json_dict(order.state_json)
    prev_slug = (str(state.get("restaurant_slug") or "")).strip().lower()

    # if restaurant changes, clear cart
    if prev_slug and prev_slug != slug:
        order.items_json = "[]"
        state = {}

    state["restaurant_slug"] = slug
    order.state_json = json.dumps(state, ensure_ascii=False)


# -------------------------
# API models
# -------------------------
class UpdateQtyIn(BaseModel):
    item_id: str
    delta: int


class RemoveIn(BaseModel):
    item_id: str


# -------------------------
# Payload
# -------------------------
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


# -------------------------
# Routes (DB-backed)
# -------------------------
@router.get("/r/{slug}/cart")
def get_cart(
    slug: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_user_id_or_guest),
):
    slug = (slug or "").strip().lower()
    menu_dict = load_menu_by_slug(slug)
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Unknown restaurant")

    order = get_or_create_draft(db, user_id)
    _ensure_order_scoped_to_restaurant(order, slug)
    db.add(order)
    db.commit()

    cart = load_cart(order.items_json)
    return _cart_payload(menu_dict, cart)


@router.post("/r/{slug}/cart/update")
def update_qty(
    slug: str,
    request: Request,
    response: Response,
    data: UpdateQtyIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_user_id_or_guest),
):
    slug = (slug or "").strip().lower()
    menu_dict = load_menu_by_slug(slug)
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Unknown restaurant")

    order = get_or_create_draft(db, user_id)
    _ensure_order_scoped_to_restaurant(order, slug)

    cart = load_cart(order.items_json)

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

            order.items_json = dump_cart(cart)
            order.updated_at = datetime.utcnow()
            db.add(order)
            db.commit()
            return _cart_payload(menu_dict, cart)

    # If not in cart and delta is positive, add it
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

    order.items_json = dump_cart(cart)
    order.updated_at = datetime.utcnow()
    db.add(order)
    db.commit()
    return _cart_payload(menu_dict, cart)


@router.post("/r/{slug}/cart/remove")
def remove_line(
    slug: str,
    request: Request,
    response: Response,
    data: RemoveIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_user_id_or_guest),
):
    slug = (slug or "").strip().lower()
    menu_dict = load_menu_by_slug(slug)
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Unknown restaurant")

    order = get_or_create_draft(db, user_id)
    _ensure_order_scoped_to_restaurant(order, slug)

    cart = load_cart(order.items_json)
    item_id = (data.item_id or "").strip()

    cart = [ln for ln in cart if str(ln.get("item_id") or "") != item_id]

    order.items_json = dump_cart(cart)
    order.updated_at = datetime.utcnow()
    db.add(order)
    db.commit()
    return _cart_payload(menu_dict, cart)


@router.post("/r/{slug}/cart/clear")
def clear_cart(
    slug: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_user_id_or_guest),
):
    slug = (slug or "").strip().lower()
    menu_dict = load_menu_by_slug(slug)
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Unknown restaurant")

    order = get_or_create_draft(db, user_id)
    _ensure_order_scoped_to_restaurant(order, slug)

    order.items_json = "[]"
    order.updated_at = datetime.utcnow()
    db.add(order)
    db.commit()
    return _cart_payload(menu_dict, [])