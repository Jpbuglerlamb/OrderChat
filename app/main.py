# app/main.py
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

# Load .env locally (safe in prod too)
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from .auth import create_token, decode_token, hash_password, verify_password
from .db import Base, engine, get_db
from .emailer import send_order_email
from .menu import load_menu  # legacy single-menu loader
from .models import Order, User
from .ordering.brain import handle_message
from .ordering.cart import build_summary
from .ordering.menu_store import load_menu_by_slug  # multi-restaurant loader

# Optional AI layer
from .command_router import command_to_userlike_text

try:
    from .ai_intent import interpret_message_llm
except Exception:
    interpret_message_llm = None


# -------------------
# Config (env-driven)
# -------------------
class Settings(BaseModel):
    llm_enabled: bool = os.getenv("LLM_ENABLED", "1").strip().lower() not in {"0", "false"}
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()

    orders_email_to: str = os.getenv("ORDERS_EMAIL_TO", "Lambjoan11@gmail.com")
    currency_default: str = os.getenv("CURRENCY", "GBP")


settings = Settings()

app = FastAPI(
    title="Takeaway Ordering API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

Base.metadata.create_all(bind=engine)

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]  # TakeawayDemo/
FRONTEND_DIR = PROJECT_ROOT / "frontend"
CHAT_HTML_PATH = FRONTEND_DIR / "chat.html"


# -------------------
# Schemas
# -------------------
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


# -------------------
# Helpers
# -------------------
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
    """
    For QR/web users:
      - If Bearer token present and valid -> use that user_id.
      - Else -> create/reuse a Guest user backed by a cookie.
    """
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
            password_hash=hash_password(uuid4().hex),  # random, not used
        )
        db.add(u)
        db.commit()
        db.refresh(u)

    # Refresh cookie (30 days)
    response.set_cookie(
        key="guest_id",
        value=guest_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )

    return u.id


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
    """
    Prevent a user's draft order mixing across restaurants.
    We store restaurant_slug in state_json. If it changes, we reset the draft.
    """
    curr_slug = _normalize_slug(slug)
    state = _safe_json_dict(order.state_json)
    prev_slug = _normalize_slug(str(state.get("restaurant_slug") or ""))

    if prev_slug and prev_slug != curr_slug:
        # New restaurant: wipe draft clean
        order.items_json = "[]"
        state = {}

    state["restaurant_slug"] = curr_slug
    order.state_json = json.dumps(state)


async def _apply_optional_llm_rewrite(text: str, menu_dict: Dict[str, Any], state_json: str) -> str:
    """
    Optional LLM layer to convert messy user text into deterministic, user-like commands.
    Silent fallback on any error.
    """
    if not (settings.llm_enabled and settings.openai_api_key and interpret_message_llm):
        return text

    try:
        cmd = await interpret_message_llm(
            message=text,
            menu=menu_dict,
            state=_safe_json_dict(state_json),
        )
        candidate = command_to_userlike_text(cmd)
        return candidate or text
    except Exception:
        return text


# -------------------
# Health
# -------------------
@app.get("/")
def root():
    return {"ok": True, "service": "takeaway-api"}


# -------------------
# Auth
# -------------------
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


# -------------------
# Restaurant routing (slug)
# -------------------
@app.get("/r/{slug}", response_class=HTMLResponse)
def restaurant_page(slug: str):
    """
    QR opens this.
    If restaurant exists -> serve chat UI.
    """
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


# -------------------
# Menu (legacy single-menu)
# -------------------
@app.get("/menu")
def menu():
    return load_menu()


# -------------------
# Order reset (DB-level) ✅ fixes "Reset still shows old items"
# -------------------
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


# -------------------
# Chat ordering (legacy single-menu)
# -------------------
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

    return {
        "reply": reply,
        "order_id": order.id,
        "summary": order.summary_text,
        "items": items,
    }


# -------------------
# Chat ordering (restaurant-scoped)  ✅ guest-ready
# -------------------
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

    # Prevent mixing draft orders across restaurants
    _ensure_order_scoped_to_restaurant(order, slug)

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

    return {
        "reply": reply,
        "order_id": order.id,
        "summary": order.summary_text,
        "items": items,
        "restaurant_slug": slug,
    }


# -------------------
# Confirm order (legacy single-menu)
# -------------------
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
    body = (
        f"Customer: {u.name}\n"
        f"Email: {u.email}\n"
        f"Phone: {u.phone}\n\n"
        f"{order.summary_text}"
    )

    send_order_email(
        to_email=settings.orders_email_to,
        subject=subject,
        body=body,
    )

    return {"ok": True, "order_id": order.id, "status": order.status}