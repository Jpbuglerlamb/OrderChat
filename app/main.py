# app/main.py
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

# Load .env locally (safe in prod too)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from .db import Base, engine, get_db
from .models import Order, User
from .auth import create_token, decode_token, hash_password, verify_password
from .menu import load_menu
from .ordering import build_summary, handle_message
from .emailer import send_order_email

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


def _safe_json_list(raw: str | None) -> list:
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


def require_user_id(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.split(" ", 1)[1].strip()
    uid = decode_token(token)
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


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
# Menu
# -------------------
@app.get("/menu")
def menu():
    return load_menu()


# -------------------
# Order reset (DB-level)  ✅ this fixes your "Reset still shows old items"
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
        # no draft = already clean
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
# Chat ordering
# -------------------
@app.post("/chat")
async def chat(payload: ChatIn, user_id: int = Depends(require_user_id), db: Session = Depends(get_db)):
    order = get_or_create_draft(db, user_id)
    menu_dict = load_menu()

    text = payload.message

    # LLM → turn messy user text into deterministic “user-like” command
    if settings.llm_enabled and settings.openai_api_key and interpret_message_llm:
        try:
            cmd = await interpret_message_llm(
                message=payload.message,
                menu=menu_dict,
                state=_safe_json_dict(order.state_json),
            )
            candidate = command_to_userlike_text(cmd)
            if candidate:
                text = candidate
        except Exception:
            text = payload.message  # silent fallback

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
# Confirm order
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
