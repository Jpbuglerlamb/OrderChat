# app/routes/command_router.py
from __future__ import annotations

import json
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.menu import load_menu
from app.models import Order, StaffUser, User
from app.ordering.brain import handle_message
from app.ordering.cart import build_summary
from app.ordering.menu_store import load_menu_by_slug
from app.security.auth import decode_token, hash_password, verify_password, create_staff_token, decode_staff_token

try:
    from app.ai_intent import interpret_message_llm
except Exception:
    interpret_message_llm = None

router = APIRouter(tags=["commands"])

# ---------- Frontend paths ----------
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # TakeawayDemo/
FRONTEND_DIR = PROJECT_ROOT / "frontend"
CHAT_HTML_PATH = FRONTEND_DIR / "chat.html"
BASKET_HTML_PATH = FRONTEND_DIR / "basket.html"
STAFF_HTML_PATH = FRONTEND_DIR / "staff.html"
STAFF_LOGIN_HTML_PATH = FRONTEND_DIR / "staff_login.html"


# ---------- Helpers ----------
_STATUS_Q_RE = re.compile(
    r"\b(status|order status|progress|update|ready yet|is it ready|where is my order|how long)\b",
    re.IGNORECASE,
)

def is_order_status_query(raw: str) -> bool:
    s = (raw or "").strip().lower()
    if not s:
        return False
    if s in {"status", "order status", "update", "progress"}:
        return True
    return bool(_STATUS_Q_RE.search(s))


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


def _normalize_slug(slug: str) -> str:
    return (slug or "").strip().lower()


def _currency_symbol_from_menu(menu_dict: Dict[str, Any], default: str = "GBP") -> str:
    cur = ((menu_dict.get("meta") or {}).get("currency") or default).upper()
    return "£" if cur == "GBP" else ""


def _llm_ready(openai_api_key: str | None = None) -> bool:
    # if you want to use env in here, keep it simple
    return bool(interpret_message_llm)


def command_to_userlike_text(cmd: Dict[str, Any]) -> str:
    intent = (cmd.get("intent") or "unknown").strip()

    if intent == "show_menu":
        return "menu"
    if intent == "show_basket":
        return "basket"
    if intent == "show_category":
        cat = cmd.get("category") or ""
        return str(cat).strip() or "menu"
    if intent == "add_item":
        name = (cmd.get("item_name") or "").strip()
        qty = int(cmd.get("qty") or 1)
        if not name:
            return ""
        return name if qty <= 1 else f"{qty}x {name}"
    if intent == "remove_item":
        name = (cmd.get("item_name") or "").strip()
        return f"remove {name}" if name else ""
    if intent == "confirm":
        return "confirm"

    return ""


async def _apply_optional_llm_rewrite(text: str, menu_dict: Dict[str, Any], state_json: str) -> str:
    if not _llm_ready():
        return text
    try:
        cmd = await interpret_message_llm(message=text, menu=menu_dict, state=_safe_json_dict(state_json))
        rewritten = command_to_userlike_text(cmd)
        return rewritten or text
    except Exception as e:
        print("[LLM] rewrite failed:", repr(e), flush=True)
        traceback.print_exc()
        return text


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
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        uid = decode_token(token)
        if uid:
            return uid

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
    order.restaurant_slug = curr_slug

    state = _safe_json_dict(order.state_json)
    prev_slug = _normalize_slug(str(state.get("restaurant_slug") or ""))

    if prev_slug and prev_slug != curr_slug:
        order.items_json = "[]"
        state = {}

    state["restaurant_slug"] = curr_slug
    order.state_json = json.dumps(state)


# ---------- Routes you previously had in main.py ----------
@router.get("/r/{slug}", response_class=HTMLResponse)
def restaurant_page(slug: str):
    slug = _normalize_slug(slug)
    menu = load_menu_by_slug(slug)
    if not menu:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if not CHAT_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Missing frontend file: {CHAT_HTML_PATH}")
    return CHAT_HTML_PATH.read_text(encoding="utf-8")


@router.get("/r/{slug}/menu")
def restaurant_menu(slug: str):
    slug = _normalize_slug(slug)
    menu = load_menu_by_slug(slug)
    if not menu:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return menu


@router.get("/r/{slug}/basket", response_class=HTMLResponse)
def basket_page(slug: str):
    slug = _normalize_slug(slug)
    menu = load_menu_by_slug(slug)
    if not menu:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if not BASKET_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Missing frontend file: {BASKET_HTML_PATH}")
    return BASKET_HTML_PATH.read_text(encoding="utf-8")


@router.get("/r/{slug}/staff", response_class=HTMLResponse)
def staff_page(slug: str):
    slug = _normalize_slug(slug)
    menu = load_menu_by_slug(slug)
    if not menu:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if not STAFF_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Missing frontend file: {STAFF_HTML_PATH}")
    return STAFF_HTML_PATH.read_text(encoding="utf-8")


@router.get("/staff/login", response_class=HTMLResponse)
def staff_login_page():
    if not STAFF_LOGIN_HTML_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Missing frontend file: {STAFF_LOGIN_HTML_PATH}")
    return STAFF_LOGIN_HTML_PATH.read_text(encoding="utf-8")


@router.post("/staff/login")
def staff_login(payload: dict, db: Session = Depends(get_db)):
    # expects {"email": "...", "password": "..."}
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "")
    s = db.query(StaffUser).filter(StaffUser.email == email).first()
    if not s or not verify_password(password, s.password_hash):
        raise HTTPException(status_code=401, detail="Bad credentials")

    token = create_staff_token({"staff_id": s.id, "restaurant_slug": _normalize_slug(s.restaurant_slug)})
    return {"token": token, "restaurant_slug": _normalize_slug(s.restaurant_slug)}


@router.post("/r/{slug}/chat")
async def chat_for_restaurant(
    slug: str,
    payload: dict,
    user_id: int = Depends(require_user_id_or_guest),
    db: Session = Depends(get_db),
):
    msg = str(payload.get("message") or "")
    slug = _normalize_slug(slug)

    menu_dict = load_menu_by_slug(slug)
    if not menu_dict:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    order = get_or_create_draft(db, user_id)
    _ensure_order_scoped_to_restaurant(order, slug)

    if is_order_status_query(msg):
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

    text = await _apply_optional_llm_rewrite(msg, menu_dict, order.state_json)

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

    return {"reply": reply, "order_id": order.id, "summary": order.summary_text, "items": items, "restaurant_slug": slug}