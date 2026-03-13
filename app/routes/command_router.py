#app/routes/command_router.py
from __future__ import annotations

import json
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Order, Restaurant, StaffUser, User
from app.ordering.brain import handle_message
from app.ordering.cart import build_summary
from app.routes.auth_platform import get_session_email
from app.security.auth import (
    create_staff_token,
    decode_staff_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.services.storage import get_json_file

try:
    from app.ai_intent import interpret_message_llm
except Exception:
    interpret_message_llm = None


router = APIRouter(tags=["commands"])

# ---------- Frontend / templates ----------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
TEMPLATES_DIR = FRONTEND_DIR / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

CHAT_HTML_PATH = TEMPLATES_DIR / "chat.html"
BASKET_HTML_PATH = TEMPLATES_DIR / "basket.html"
STAFF_HTML_PATH = TEMPLATES_DIR / "staff.html"
STAFF_LOGIN_HTML_PATH = TEMPLATES_DIR / "staff_login.html"

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
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _safe_json_list(raw: str | None) -> List[Any]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _normalize_slug(slug: str) -> str:
    return (slug or "").strip().lower()


def _currency_symbol_from_menu(menu_dict: Dict[str, Any], default: str = "GBP") -> str:
    restaurant = menu_dict.get("restaurant") or {}
    currency = str(restaurant.get("currency") or default).upper()
    return "£" if currency == "GBP" else ""


def _llm_ready() -> bool:
    return bool(interpret_message_llm)


def command_to_userlike_text(cmd: Dict[str, Any]) -> str:
    intent = (cmd.get("intent") or "unknown").strip()

    if intent == "show_menu":
        return "menu"
    if intent == "show_basket":
        return "basket"
    if intent == "show_category":
        category = cmd.get("category") or ""
        return str(category).strip() or "menu"

    if intent == "add_item":
        name = (cmd.get("item_name") or "").strip()
        try:
            qty = int(cmd.get("qty") or 1)
        except Exception:
            qty = 1
        if not name:
            return ""
        return name if qty <= 1 else f"{qty}x {name}"

    if intent == "remove_item":
        name = (cmd.get("item_name") or "").strip()
        return f"remove {name}" if name else ""

    if intent == "confirm":
        return "confirm"

    return ""


async def _apply_optional_llm_rewrite(
    text: str,
    menu_dict: Dict[str, Any],
    state_json: str,
) -> str:
    if not _llm_ready():
        return text

    try:
        cmd = await interpret_message_llm(
            message=text,
            menu=menu_dict,
            state=_safe_json_dict(state_json),
        )
        rewritten = command_to_userlike_text(cmd)
        return rewritten or text
    except Exception as exc:
        print("[LLM] rewrite failed:", repr(exc), flush=True)
        traceback.print_exc()
        return text


def get_restaurant_and_menu(db: Session, slug: str) -> tuple[Restaurant, Dict[str, Any]]:
    slug = _normalize_slug(slug)

    restaurant = db.query(Restaurant).filter(Restaurant.slug == slug).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    if not restaurant.menu_json_path:
        raise HTTPException(status_code=404, detail="Menu not found")

    try:
        menu_dict = get_json_file(restaurant.menu_json_path)
    except Exception as exc:
        print("[MENU LOAD ERROR]", slug, restaurant.menu_json_path, repr(exc), flush=True)
        raise HTTPException(status_code=404, detail="Menu could not be loaded")

    return restaurant, menu_dict


def _ensure_template_exists(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"Missing frontend file: {path}")


def get_owner_user_for_request(
    request: Request,
    db: Session,
) -> User | None:
    session_email = get_session_email(request)
    if not session_email:
        return None

    return db.query(User).filter(User.email == session_email).first()


def _staff_payload_from_cookie(request: Request) -> Dict[str, Any] | None:
    staff_token = (request.cookies.get("staff_token") or "").strip()
    if not staff_token:
        return None

    payload = decode_staff_token(staff_token)
    if not payload:
        return None

    restaurant_slug = payload.get("restaurant_slug")
    if not isinstance(restaurant_slug, str) or not restaurant_slug.strip():
        return None

    payload["restaurant_slug"] = _normalize_slug(restaurant_slug)
    return payload


def _owner_can_access_restaurant(request: Request, db: Session, restaurant: Restaurant) -> bool:
    owner_user = get_owner_user_for_request(request, db)
    return bool(owner_user and restaurant.owner_user_id == owner_user.id)


def _staff_can_access_restaurant(request: Request, slug: str) -> bool:
    payload = _staff_payload_from_cookie(request)
    if not payload:
        return False
    return payload.get("restaurant_slug") == _normalize_slug(slug)


# ---------- Auth dependencies ----------
def require_user_id(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.split(" ", 1)[1].strip()
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


def require_user_id_or_guest(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    guest_id: str | None = Cookie(default=None, alias="guest_id"),
) -> int:
    # 1) Prefer authenticated API token if present
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        user_id = decode_token(token)
        if user_id:
            return user_id

    # 2) Then try normal website session cookie
    session_email = get_session_email(request)
    if session_email:
        user = db.query(User).filter(User.email == session_email).first()
        if user:
            return user.id

    # 3) Otherwise fall back to guest cookie
    if not guest_id:
        guest_id = uuid4().hex

    guest_email = f"guest+{guest_id}@demo.local"

    user = db.query(User).filter(User.email == guest_email).first()
    if not user:
        user = User(
            name="Guest",
            email=guest_email,
            phone=None,
            password_hash=hash_password(uuid4().hex),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    response.set_cookie(
        key="guest_id",
        value=guest_id,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )

    return user.id


def require_staff(authorization: str | None = Header(default=None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = authorization.split(" ", 1)[1].strip()
    payload = decode_staff_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid staff token")

    restaurant_slug = payload.get("restaurant_slug")
    if not isinstance(restaurant_slug, str) or not restaurant_slug.strip():
        raise HTTPException(status_code=401, detail="Staff token missing restaurant")

    payload["restaurant_slug"] = _normalize_slug(restaurant_slug)
    return payload


# ---------- Order helpers ----------
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
    current_slug = _normalize_slug(slug)
    order.restaurant_slug = current_slug

    state = _safe_json_dict(order.state_json)
    previous_slug = _normalize_slug(str(state.get("restaurant_slug") or ""))

    if previous_slug and previous_slug != current_slug:
        order.items_json = "[]"
        state = {}

    state["restaurant_slug"] = current_slug
    order.state_json = json.dumps(state)


def _is_guest_email(email: str | None) -> bool:
    value = (email or "").strip().lower()
    return value.endswith("@demo.local") and value.startswith("guest+")


# ---------- "Me" endpoints ----------
class MeOut(BaseModel):
    user_id: int
    is_guest: bool
    name: Optional[str] = None
    email: Optional[str] = None
    restaurant_slug: str


@router.get("/r/{slug}/me", response_model=MeOut)
def me_for_restaurant(
    slug: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_user_id_or_guest),
):
    slug = _normalize_slug(slug)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_guest = _is_guest_email(user.email)

    return {
        "user_id": user.id,
        "is_guest": is_guest,
        "name": (user.name or None),
        "email": (None if is_guest else (user.email or None)),
        "restaurant_slug": slug,
    }


class SetNameIn(BaseModel):
    name: str


@router.post("/r/{slug}/me/name")
def set_name_for_restaurant(
    slug: str,
    payload: SetNameIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user_id: int = Depends(require_user_id_or_guest),
):
    name = (payload.name or "").strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Name too short")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.name = name
    db.add(user)
    db.commit()
    return {"ok": True, "name": user.name}


# ---------- Pages / menus ----------
@router.get("/r/{slug}", response_class=HTMLResponse)
def restaurant_chat(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    slug = _normalize_slug(slug)
    restaurant, menu_data = get_restaurant_and_menu(db, slug)

    _ensure_template_exists(CHAT_HTML_PATH)

    response = templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "restaurant": restaurant,
            "restaurant_slug": slug,
            "menu_data": menu_data,
        },
    )

    response.set_cookie(
        "last_slug",
        slug,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
    )

    return response


@router.get("/r/{slug}/health")
def restaurant_health(slug: str, db: Session = Depends(get_db)):
    slug = _normalize_slug(slug)
    restaurant, _menu = get_restaurant_and_menu(db, slug)
    return {"ok": True, "restaurant_slug": restaurant.slug}


@router.get("/r/{slug}/menu")
def restaurant_menu(slug: str, db: Session = Depends(get_db)):
    slug = _normalize_slug(slug)
    _restaurant, menu = get_restaurant_and_menu(db, slug)
    return menu


@router.get("/r/{slug}/basket", response_class=HTMLResponse)
def basket_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    slug = _normalize_slug(slug)
    restaurant, menu_data = get_restaurant_and_menu(db, slug)

    _ensure_template_exists(BASKET_HTML_PATH)

    return templates.TemplateResponse(
        "basket.html",
        {
            "request": request,
            "restaurant": restaurant,
            "restaurant_slug": slug,
            "menu_data": menu_data,
        },
    )


@router.get("/r/{slug}/staff", response_class=HTMLResponse)
def staff_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    slug = _normalize_slug(slug)
    restaurant, menu_data = get_restaurant_and_menu(db, slug)

    _ensure_template_exists(STAFF_HTML_PATH)

    # Owner can enter directly with normal platform session
    if _owner_can_access_restaurant(request, db, restaurant):
        return templates.TemplateResponse(
            "staff.html",
            {
                "request": request,
                "restaurant": restaurant,
                "restaurant_slug": slug,
                "menu_data": menu_data,
                "access_mode": "owner",
            },
        )

    # Optional: keep separate staff-token access for later
    if _staff_can_access_restaurant(request, slug):
        return templates.TemplateResponse(
            "staff.html",
            {
                "request": request,
                "restaurant": restaurant,
                "restaurant_slug": slug,
                "menu_data": menu_data,
                "access_mode": "staff",
            },
        )

    # For now, business user = dashboard user
    return RedirectResponse(url=f"/business/login?next=/r/{slug}/staff", status_code=302)


@router.get("/staff/login", response_class=HTMLResponse)
def staff_login_page(
    request: Request,
    db: Session = Depends(get_db),
):
    _ensure_template_exists(STAFF_LOGIN_HTML_PATH)

    next_url = (request.query_params.get("next") or "").strip()

    # If already signed in as owner, skip this page
    owner_user = get_owner_user_for_request(request, db)
    if owner_user and next_url.startswith("/r/") and next_url.endswith("/staff"):
        parts = next_url.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "r" and parts[2] == "staff":
            slug = _normalize_slug(parts[1])
            restaurant = db.query(Restaurant).filter(Restaurant.slug == slug).first()
            if restaurant and restaurant.owner_user_id == owner_user.id:
                return RedirectResponse(url=next_url, status_code=302)

    return templates.TemplateResponse(
        "staff_login.html",
        {
            "request": request,
            "next": next_url,
            "error": request.query_params.get("error", ""),
        },
    )


# ---------- Staff auth ----------
@router.post("/staff/login")
async def staff_login(
    request: Request,
    db: Session = Depends(get_db),
    email: Optional[str] = Form(default=None),
    password: Optional[str] = Form(default=None),
    next: Optional[str] = Form(default=""),
):
    if email is None and password is None:
        try:
            payload = await request.json()
            email = str(payload.get("email") or "").strip()
            password = str(payload.get("password") or "")
            next = str(payload.get("next") or "")
        except Exception:
            email = ""
            password = ""
            next = ""

    email = (email or "").strip()
    password = password or ""
    next = (next or "").strip()

    staff_user = db.query(StaffUser).filter(StaffUser.email == email).first()
    if not staff_user or not verify_password(password, staff_user.password_hash):
        login_url = "/staff/login?error=bad_login"
        if next:
            login_url += f"&next={next}"
        return RedirectResponse(url=login_url, status_code=302)

    staff_token = create_staff_token(
        {
            "staff_id": staff_user.id,
            "restaurant_slug": _normalize_slug(staff_user.restaurant_slug),
        }
    )

    content_type = request.headers.get("content-type", "")

    if content_type.startswith("application/x-www-form-urlencoded") or content_type.startswith("multipart/form-data"):
        redirect_url = next or f"/r/{_normalize_slug(staff_user.restaurant_slug)}/staff"
        response = RedirectResponse(url=redirect_url, status_code=302)
        response.set_cookie(
            key="guest_id",
            value=guest_id,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
            path="/",
        )
        return response

    return {"token": staff_token, "restaurant_slug": _normalize_slug(staff_user.restaurant_slug)}


# ---------- Staff API ----------
@router.get("/r/{slug}/staff/orders")
def staff_orders(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    slug = _normalize_slug(slug)
    restaurant, _menu = get_restaurant_and_menu(db, slug)

    if not _owner_can_access_restaurant(request, db, restaurant) and not _staff_can_access_restaurant(request, slug):
        raise HTTPException(status_code=403, detail="Forbidden")

    orders = (
        db.query(Order)
        .filter(Order.restaurant_slug == slug)
        .filter(Order.status == "confirmed")
        .filter(or_(Order.kitchen_status.is_(None), Order.kitchen_status != "completed"))
        .order_by(Order.created_at.desc())
        .all()
    )

    return [
        {
            "id": order.id,
            "name": order.customer_name,
            "phone": order.customer_phone,
            "summary": order.summary_text,
            "status": (order.kitchen_status or "new"),
        }
        for order in orders
    ]


@router.post("/r/{slug}/staff/orders/{order_id}/status")
def update_order_status(
    slug: str,
    order_id: int,
    data: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    slug = _normalize_slug(slug)
    restaurant, _menu = get_restaurant_and_menu(db, slug)

    if not _owner_can_access_restaurant(request, db, restaurant) and not _staff_can_access_restaurant(request, slug):
        raise HTTPException(status_code=403, detail="Forbidden")

    order = db.query(Order).filter(Order.id == order_id, Order.restaurant_slug == slug).first()
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


# ---------- Chat ----------
@router.post("/r/{slug}/chat")
async def chat_for_restaurant(
    slug: str,
    payload: dict,
    user_id: int = Depends(require_user_id_or_guest),
    db: Session = Depends(get_db),
):
    message = str(payload.get("message") or "")
    slug = _normalize_slug(slug)

    _restaurant, menu_dict = get_restaurant_and_menu(db, slug)

    order = get_or_create_draft(db, user_id)
    _ensure_order_scoped_to_restaurant(order, slug)

    if is_order_status_query(message):
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
        nice_status = {
            "new": "New (not started yet)",
            "accepted": "Accepted ✅",
            "preparing": "Preparing 🍳",
            "ready": "Ready ✅",
            "completed": "Completed ✅",
        }.get(status, status)

        return {
            "reply": f"Order status: {nice_status}",
            "order_id": order.id,
            "summary": order.summary_text,
            "items": _safe_json_list(order.items_json),
            "restaurant_slug": slug,
            "kitchen_status": status,
        }

    text = await _apply_optional_llm_rewrite(message, menu_dict, order.state_json)

    reply, updated_items_json, updated_state_json = handle_message(
        message=text,
        items_json=order.items_json,
        menu_dict=menu_dict,
        state_json=order.state_json,
    )

    order.items_json = updated_items_json

    state = _safe_json_dict(updated_state_json)
    order.state_json = json.dumps(state)

    items = _safe_json_list(order.items_json)
    symbol = _currency_symbol_from_menu(menu_dict)
    summary, _total = build_summary(items, currency_symbol=symbol)
    order.summary_text = summary
    order.updated_at = datetime.utcnow()

    if state.get("order_submitted"):
        order.status = "confirmed"
        order.kitchen_status = "new"
        order.restaurant_slug = slug

        order.customer_name = str(state.get("customer_name") or "")
        order.customer_email = str(state.get("customer_email") or "")
        order.customer_phone = str(state.get("customer_phone") or "")

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