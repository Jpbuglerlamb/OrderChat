# app/main.py
from __future__ import annotations

import json
import os
import traceback
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from .cart_api import router as cart_router
from .db import Base, engine, get_db
from .emailer import send_order_email
from .menu import load_menu  # legacy single-menu loader
from .models import Order, StaffUser, User
from .ordering.brain import handle_message
from .ordering.cart import build_summary
from .ordering.menu_store import load_menu_by_slug  # multi-restaurant loader

# NEW platform auth (make sure these files exist in app/)
from . import models_platform  # noqa: F401
from .auth_routes import router as auth_router

# Load .env locally (safe in prod too)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from .auth import (
    create_token,
    decode_token,
    hash_password,
    verify_password,
    create_staff_token,
    decode_staff_token,
)

try:
    from .ai_intent import interpret_message_llm
except Exception:
    interpret_message_llm = None


# -------------------------
# Settings
# -------------------------
class Settings(BaseModel):
    llm_enabled: bool = os.getenv("LLM_ENABLED", "1").strip().lower() not in {"0", "false"}
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    orders_email_to: str = os.getenv("ORDERS_EMAIL_TO", "Lambjoan11@gmail.com")
    currency_default: str = os.getenv("CURRENCY", "GBP")


settings = Settings()


# -------------------------
# Create app ONCE
# -------------------------
app = FastAPI(
    title="Takeaway Ordering API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# -------------------------
# Routers
# -------------------------
app.include_router(cart_router)
app.include_router(auth_router)

# -------------------------
# DB init (after models imported)
# -------------------------
Base.metadata.create_all(bind=engine)

# -------------------------
# Frontend paths
# -------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # TakeawayDemo/
FRONTEND_DIR = PROJECT_ROOT / "frontend"
CHAT_HTML_PATH = FRONTEND_DIR / "chat.html"
BASKET_HTML_PATH = FRONTEND_DIR / "basket.html"
STAFF_HTML_PATH = FRONTEND_DIR / "staff.html"

# -------------------------
# Schemas
# -------------------------
class SignupIn(BaseModel):
    name: str
    email: EmailStr
    phone: str | None = None
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ChatIn(BaseModel):
    message: str


class StaffLoginIn(BaseModel):
    email: EmailStr
    password: str

# --- Order status intent ---
_STATUS_Q_RE = re.compile(
    r"\b(status|order status|progress|update|ready yet|is it ready|where is my order|how long)\b",
    re.IGNORECASE,
)

def is_order_status_query(raw: str) -> bool:
    s = (raw or "").strip().lower()
    if not s:
        return False

    # common short forms
    if s in {"status", "order status", "update", "progress"}:
        return True

    return bool(_STATUS_Q_RE.search(s))
# -------------------------
# Helpers
# -------------------------
def _safe_json_dict(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _safe_json_list(raw: str | None) -> List[Any]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _currency_symbol_from_menu(menu_dict: Dict[str, Any]) -> str:
    cur = ((menu_dict.get("meta") or {}).get("currency") or settings.currency_default).upper()
    return "£" if cur == "GBP" else ""


def _normalize_slug(slug: str) -> str:
    return (slug or "").strip().lower()


def _business_order_email(menu_dict: Dict[str, Any]) -> str:
    meta = menu_dict.get("meta") or {}
    if isinstance(meta, dict):
        for k in ("order_email", "email", "contact_email"):
            v = meta.get(k)
            if isinstance(v, str) and "@" in v:
                return v.strip()
    return settings.orders_email_to


def _llm_ready() -> bool:
    return bool(settings.llm_enabled and settings.openai_api_key and interpret_message_llm)


def _cmd_to_brain_text(cmd: Dict[str, Any], original_text: str) -> str:
    intent = (cmd.get("intent") or "unknown").strip()
    category = (cmd.get("category") or "").strip()
    item_name = (cmd.get("item_name") or "").strip()
    qty = cmd.get("qty")

    if intent == "show_menu":
        return "menu"
    if intent == "show_basket":
        return "basket"
    if intent == "show_category" and category:
        return category
    if intent == "add_item" and item_name:
        if isinstance(qty, int) and qty > 1:
            return f"{qty} {item_name}"
        return item_name
    if intent == "remove_item" and item_name:
        return f"remove {item_name}"

    return original_text


async def _apply_optional_llm_rewrite(text: str, menu_dict: Dict[str, Any], state_json: str) -> str:
    if not _llm_ready():
        return text
    try:
        cmd = await interpret_message_llm(
            message=text,
            menu=menu_dict,
            state=_safe_json_dict(state_json),
        )
        return _cmd_to_brain_text(cmd, text)
    except Exception as e:
        print("[LLM] rewrite failed:", repr(e), flush=True)
        traceback.print_exc()
        return text


# -------------------------
# Auth dependencies
# -------------------------
def require_user_id(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.split(" ", 1)[1].strip()
    uid = decode_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


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

    # 2) Otherwise, cookie-based guest identity
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


def require_staff(authorization: str | None = Header(default=None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.split(" ", 1)[1].strip()
    payload = decode_staff_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid staff token")

    rs = payload.get("restaurant_slug")
    if not isinstance(rs, str) or not rs.strip():
        raise HTTPException(status_code=401, detail="Staff token missing restaurant")

    payload["restaurant_slug"] = _normalize_slug(rs)
    return payload


# -------------------------
# Order helpers
# -------------------------
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
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _ensure_order_scoped_to_restaurant(order: Order, slug: str) -> None:
    curr_slug = _normalize_slug(slug)

    # store on the row for staff queries
    order.restaurant_slug = curr_slug

    state = _safe_json_dict(order.state_json)
    prev_slug = _normalize_slug(str(state.get("restaurant_slug") or ""))

    if prev_slug and prev_slug != curr_slug:
        order.items_json = "[]"
        state = {}

    state["restaurant_slug"] = curr_slug
    order.state_json = json.dumps(state)


# -------------------------
# Routes: health
# -------------------------
@app.get("/")
def root():
    return {"ok": True, "service": "takeaway-api"}


@app.head("/")
def root_head():
    return Response(status_code=200)


# -------------------------
# Routes: customer auth
# -------------------------
@app.post("/auth/signup")
def signup(payload: SignupIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already exists")

    u = User(
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        password_hash=hash_password(payload.password),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"ok": True}


@app.post("/auth/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    u = db.query(User).filter(User.email == payload.email).first()
    if not u or not verify_password(payload.password, u.password_hash):
        raise HTTPException(status_code=401, detail="Bad credentials")
    return {"token": create_token(u.id)}


# -------------------------
# Routes: staff auth
# -------------------------
@app.post("/staff/login")
def staff_login(payload: StaffLoginIn, db: Session = Depends(get_db)):
    s = db.query(StaffUser).filter(StaffUser.email == payload.email).first()
    if not s or not verify_password(payload.password, s.password_hash):
        raise HTTPException(status_code=401, detail="Bad credentials")

    token = create_staff_token(
        {
            "staff_id": s.id,
            "restaurant_slug": _normalize_slug(s.restaurant_slug),
        }
    )
    return {"token": token, "restaurant_slug": _normalize_slug(s.restaurant_slug)}


# -------------------------
# Routes: restaurant frontend + menu
# -------------------------
@app.get("/r/{slug}", response_class=HTMLResponse)
def restaurant_page(slug: str):
    slug = _normalize_slug(slug)
    menu = load_menu_by_slug(slug)
    if not menu:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    if not CHAT_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Missing frontend file: {CHAT_HTML_PATH}")

    return CHAT_HTML_PATH.read_text(encoding="utf-8")


@app.get("/r/{slug}/health")
def restaurant_health(slug: str):
    slug = _normalize_slug(slug)
    menu = load_menu_by_slug(slug)
    if not menu:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return {"ok": True, "restaurant": (menu.get("meta") or {}).get("slug") or slug}


@app.get("/r/{slug}/menu")
def restaurant_menu(slug: str):
    slug = _normalize_slug(slug)
    menu = load_menu_by_slug(slug)
    if not menu:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return menu


@app.get("/menu")
def menu():
    return load_menu()


# -------------------------
# Routes: basket + staff HTML
# -------------------------
@app.get("/r/{slug}/basket", response_class=HTMLResponse)
def basket_page(slug: str):
    slug = _normalize_slug(slug)
    menu = load_menu_by_slug(slug)
    if not menu:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    if not BASKET_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Missing frontend file: {BASKET_HTML_PATH}")

    return BASKET_HTML_PATH.read_text(encoding="utf-8")


@app.get("/r/{slug}/staff", response_class=HTMLResponse)
def staff_page(slug: str):
    slug = _normalize_slug(slug)
    menu = load_menu_by_slug(slug)
    if not menu:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    if not STAFF_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Missing frontend file: {STAFF_HTML_PATH}")

    return STAFF_HTML_PATH.read_text(encoding="utf-8")

STAFF_LOGIN_HTML_PATH = FRONTEND_DIR / "staff_login.html"

@app.get("/staff/login", response_class=HTMLResponse)
def staff_login_page():
    if not STAFF_LOGIN_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Missing frontend file: {STAFF_LOGIN_HTML_PATH}")
    return STAFF_LOGIN_HTML_PATH.read_text(encoding="utf-8")

# -------------------------
# Routes: order reset (legacy)
# -------------------------
@app.post("/order/reset")
def reset_order(user_id: int = Depends(require_user_id), db: Session = Depends(get_db)):
    order = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.status == "draft")
        .order_by(Order.id.desc())
        .first()
    )
    if not order:
        return {"ok": True, "message": "No draft to reset"}

    order.items_json = "[]"
    order.state_json = "{}"
    order.summary_text = ""
    order.updated_at = datetime.utcnow()

    db.add(order)
    db.commit()
    db.refresh(order)

    return {"ok": True, "message": "Draft reset"}


# -------------------------
# Routes: chat (legacy single menu)
# -------------------------
@app.post("/chat")
async def chat(payload: ChatIn, user_id: int = Depends(require_user_id), db: Session = Depends(get_db)):
    order = get_or_create_draft(db, user_id)
    menu_dict = load_menu()

    text = await _apply_optional_llm_rewrite(payload.message, menu_dict, order.state_json)

    reply, updated_items_json, updated_state_json = handle_message(
        message=text,
        items_json=order.items_json,
        menu_dict=menu_dict,
        state_json=order.state_json,
    )

    order.items_json = updated_items_json
    order.state_json = updated_state_json

    items = _safe_json_list(order.items_json)
    symbol = _currency_symbol_from_menu(menu_dict)
    summary, _total = build_summary(items, currency_symbol=symbol)

    order.summary_text = summary
    order.updated_at = datetime.utcnow()

    db.add(order)
    db.commit()
    db.refresh(order)

    return {"reply": reply, "order_id": order.id, "summary": order.summary_text, "items": items}


# -------------------------
# Routes: chat per restaurant
# -------------------------
@app.post("/r/{slug}/chat")
async def chat_for_restaurant(
    slug: str,
    payload: ChatIn,
    user_id: int = Depends(require_user_id_or_guest),
    db: Session = Depends(get_db),
):
    slug = _normalize_slug(slug)

    menu_dict = load_menu_by_slug(slug)
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    order = get_or_create_draft(db, user_id)
    _ensure_order_scoped_to_restaurant(order, slug)

    # -------------------------
    # Customer asking for order status (kitchen progress)
    # -------------------------
    if is_order_status_query(payload.message):
        # No confirmed order yet
        if (order.status or "").lower() != "confirmed":
            return {
                "reply": "You haven’t placed an order yet. Type “checkout” when you’re ready 🙂",
                "order_id": order.id,
                "summary": order.summary_text,
                "items": _safe_json_list(order.items_json),
                "restaurant_slug": slug,
                "kitchen_status": None,
            }

        status = (order.kitchen_status or "new").strip().lower()

        nice = {
            "new": "New (not started yet)",
            "accepted": "Accepted ✅",
            "preparing": "Preparing 🍳",
            "ready": "Ready ✅",
            "completed": "Completed ✅",
        }.get(status, status)

        return {
            "reply": f"Order status: {nice}",
            "order_id": order.id,
            "summary": order.summary_text,
            "items": _safe_json_list(order.items_json),
            "restaurant_slug": slug,
            "kitchen_status": status,
        }

    text = await _apply_optional_llm_rewrite(payload.message, menu_dict, order.state_json)

    reply, updated_items_json, updated_state_json = handle_message(
        message=text,
        items_json=order.items_json,
        menu_dict=menu_dict,
        state_json=order.state_json,
    )

    state = _safe_json_dict(updated_state_json)

    # Always store updated cart/state first
    order.items_json = updated_items_json
    order.state_json = json.dumps(state)

    # Build summary from items
    items = _safe_json_list(order.items_json)
    symbol = _currency_symbol_from_menu(menu_dict)
    summary, _total = build_summary(items, currency_symbol=symbol)
    order.summary_text = summary
    order.updated_at = datetime.utcnow()

    # Finalize if submitted
    if state.get("order_submitted"):
        order.status = "confirmed"
        order.kitchen_status = "new"
        order.restaurant_slug = slug

        order.customer_name = str(state.get("customer_name") or "")
        order.customer_email = str(state.get("customer_email") or "")
        order.customer_phone = str(state.get("customer_phone") or "")

        # Prevent re-submit
        state.pop("order_submitted", None)
        order.state_json = json.dumps(state)

    db.add(order)
    db.commit()
    db.refresh(order)

    return {
        "reply": reply,
        "order_id": order.id,
        "summary": order.summary_text,
        "items": items,
        "restaurant_slug": slug,
    }


# -------------------------
# Routes: confirm (legacy)
# -------------------------
@app.post("/order/confirm")
def confirm(user_id: int = Depends(require_user_id), db: Session = Depends(get_db)):
    order = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.status == "draft")
        .order_by(Order.id.desc())
        .first()
    )
    if not order:
        raise HTTPException(status_code=400, detail="No draft order to confirm")

    items = _safe_json_list(order.items_json)
    if not items:
        raise HTTPException(status_code=400, detail="Order is empty")

    menu_dict = load_menu()
    symbol = _currency_symbol_from_menu(menu_dict)
    summary, _total = build_summary(items, currency_symbol=symbol)

    order.summary_text = summary
    order.status = "confirmed"
    order.updated_at = datetime.utcnow()
    order.state_json = "{}"

    db.add(order)
    db.commit()
    db.refresh(order)

    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    subject = f"New Takeaway Order (Order #{order.id})"
    body = f"Customer: {u.name}\nEmail: {u.email}\nPhone: {u.phone}\n\n{order.summary_text}"

    send_order_email(to_email=settings.orders_email_to, subject=subject, body=body)
    return {"ok": True, "order_id": order.id, "status": order.status}


# -------------------------
# Routes: confirm per restaurant
# -------------------------
@app.post("/r/{slug}/order/confirm")
def confirm_restaurant_order(
    slug: str,
    user_id: int = Depends(require_user_id_or_guest),
    db: Session = Depends(get_db),
):
    slug = _normalize_slug(slug)
    menu_dict = load_menu_by_slug(slug)
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    order = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.status == "draft")
        .order_by(Order.id.desc())
        .first()
    )
    if not order:
        raise HTTPException(status_code=400, detail="No draft order to confirm")

    _ensure_order_scoped_to_restaurant(order, slug)

    items = _safe_json_list(order.items_json)
    if not items:
        raise HTTPException(status_code=400, detail="Order is empty")

    symbol = _currency_symbol_from_menu(menu_dict)
    summary, _total = build_summary(items, currency_symbol=symbol)

    order.summary_text = summary
    order.status = "confirmed"
    order.updated_at = datetime.utcnow()
    order.state_json = "{}"

    db.add(order)
    db.commit()
    db.refresh(order)

    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    to_email = _business_order_email(menu_dict)
    subject = f"New Order ({slug}) · Order #{order.id}"
    body = (
        f"Restaurant: {slug}\n"
        f"Customer: {u.name}\nEmail: {u.email}\nPhone: {u.phone}\n\n"
        f"{order.summary_text}"
    )

    send_order_email(to_email=to_email, subject=subject, body=body)
    return {"ok": True, "order_id": order.id, "status": order.status}


# -------------------------
# Routes: staff API (PROTECTED)
# -------------------------
@app.get("/r/{slug}/staff/orders")
def staff_orders(
    slug: str,
    db: Session = Depends(get_db),
    staff: Dict[str, Any] = Depends(require_staff),
):
    slug = _normalize_slug(slug)

    if _normalize_slug(staff.get("restaurant_slug", "")) != slug:
        raise HTTPException(status_code=403, detail="Forbidden")

    orders = (
        db.query(Order)
        .filter(Order.restaurant_slug == slug)
        .filter(Order.kitchen_status != "completed")
        .order_by(Order.created_at.desc())
        .all()
    )
    return [
        {
            "id": o.id,
            "name": o.customer_name,
            "phone": o.customer_phone,
            "summary": o.summary_text,
            "status": o.kitchen_status,
        }
        for o in orders
    ]


@app.post("/r/{slug}/staff/orders/{order_id}/status")
def update_order_status(
    slug: str,
    order_id: int,
    data: dict,
    db: Session = Depends(get_db),
    staff: Dict[str, Any] = Depends(require_staff),
):
    slug = _normalize_slug(slug)

    if _normalize_slug(staff.get("restaurant_slug", "")) != slug:
        raise HTTPException(status_code=403, detail="Forbidden")

    order = (
        db.query(Order)
        .filter(Order.id == order_id, Order.restaurant_slug == slug)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    new_status = data.get("status")
    if isinstance(new_status, str) and new_status.strip():
        order.kitchen_status = new_status.strip()

    order.updated_at = datetime.utcnow()
    db.add(order)
    db.commit()
    db.refresh(order)

    return {"ok": True, "id": order.id, "status": order.kitchen_status}

@app.get("/create-demo-staff")
def create_demo_staff(db: Session = Depends(get_db)):
    from .models import StaffUser
    from .auth import hash_password

    existing = db.query(StaffUser).filter(
        StaffUser.email == "staff@chinese.com"
    ).first()

    if existing:
        return {"status": "already exists"}

    u = StaffUser(
        email="staff@chinese.com",
        password_hash=hash_password("1234"),
        restaurant_slug="chinese-demo",
    )

    db.add(u)
    db.commit()

    return {"status": "created"}