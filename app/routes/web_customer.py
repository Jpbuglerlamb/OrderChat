# app/routes/web_customer.py
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from .auth_platform import (
    clear_session_cookie,
    create_user,
    get_session_email,
    set_session_cookie,
    verify_user,
)

router = APIRouter(tags=["web-auth"])

# Templates live in: TakeawayDemo/frontend/templates/*.html
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # TakeawayDemo/
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------- Helpers ----------------
def _cookie_slug(request: Request) -> Optional[str]:
    """
    Cookie set by the chat frontend (chat.html) so auth can redirect back
    to the correct restaurant chat.
    """
    slug = (request.cookies.get("last_slug") or "").strip().lower()
    return slug or None


def _safe_next(request: Request) -> Optional[str]:
    """
    Accept a next=/r/<slug> path, reject anything else (prevents open redirects).
    """
    nxt = (request.query_params.get("next") or "").strip()
    if nxt.startswith("/r/"):
        return nxt
    return None


def _add_welcome(url: str, welcome: bool) -> str:
    if not welcome:
        return url
    join = "&" if "?" in url else "?"
    return f"{url}{join}welcome=1"


def _redirect_to_best_chat(request: Request, welcome: bool = False) -> RedirectResponse:
    """
    Redirect priority:
    1) ?next=/r/<slug> (safe internal)
    2) cookie last_slug
    3) fallback /
    """
    nxt = _safe_next(request)
    if nxt:
        return RedirectResponse(url=_add_welcome(nxt, welcome), status_code=302)

    slug = _cookie_slug(request)
    if slug:
        return RedirectResponse(url=_add_welcome(f"/r/{slug}", welcome), status_code=302)

    return RedirectResponse(url="/", status_code=302)


def _common_ctx(request: Request) -> dict:
    return {
        "request": request,
        "email": get_session_email(request),
        "year": datetime.utcnow().year,
    }


def _auth_error_redirect(base_path: str, code: str, request: Request) -> RedirectResponse:
    """
    Keep `next` param when bouncing back to login/signup with an error.
    """
    nxt = _safe_next(request)
    url = f"{base_path}?error={code}"
    if nxt:
        url += f"&next={nxt}"
    return RedirectResponse(url=url, status_code=302)


# ---------------- Pages ----------------
@router.get("/auth/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    ctx = _common_ctx(request)
    ctx["error"] = request.query_params.get("error")
    ctx["next"] = _safe_next(request) or ""  # optional, for templates
    return templates.TemplateResponse("signup.html", ctx)


@router.post("/auth/signup")
def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    address: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        create_user(
            db,
            email=email,
            password=password,
            name=name,
            phone=phone,
            address=address,
        )
    except ValueError:
        return _auth_error_redirect("/auth/signup", "exists", request)

    resp = _redirect_to_best_chat(request, welcome=True)
    set_session_cookie(resp, email)
    return resp


@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request):
    ctx = _common_ctx(request)
    ctx["error"] = request.query_params.get("error")
    ctx["next"] = _safe_next(request) or ""  # optional, for templates
    return templates.TemplateResponse("login.html", ctx)


@router.post("/auth/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not verify_user(db, email, password):
        return _auth_error_redirect("/auth/login", "bad_login", request)

    resp = _redirect_to_best_chat(request, welcome=True)
    set_session_cookie(resp, email)
    return resp


@router.post("/auth/logout")
def logout():
    resp = RedirectResponse(url="/", status_code=302)
    clear_session_cookie(resp)
    return resp


# Optional debug endpoint (remove in prod)
@router.get("/auth/dev/whoami")
def whoami(request: Request):
    return {
        "email": get_session_email(request),
        "last_slug": _cookie_slug(request),
        "next": _safe_next(request),
        "templates_dir": str(TEMPLATES_DIR),
    }