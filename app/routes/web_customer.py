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

# ---- Templates live in: TakeawayDemo/frontend/templates/*.html
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # TakeawayDemo/
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---- Helpers
def _cookie_slug(request: Request) -> Optional[str]:
    """
    We store the last restaurant slug in a cookie so login/signup can redirect
    the user back to the same restaurant chat page.
    """
    slug = (request.cookies.get("last_slug") or "").strip().lower()
    return slug or None


def _redirect_to_best_chat(request: Request, welcome: bool = False) -> RedirectResponse:
    slug = _cookie_slug(request)
    if slug:
        url = f"/r/{slug}"
        if welcome:
            url += "?welcome=1"
        return RedirectResponse(url=url, status_code=302)

    # Fallback: if you have a homepage later, you can change this.
    return RedirectResponse(url="/", status_code=302)


def _common_ctx(request: Request) -> dict:
    return {
        "request": request,
        "email": get_session_email(request),
        "year": datetime.utcnow().year,
    }


# ---- Pages
@router.get("/auth/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    error = request.query_params.get("error")
    ctx = _common_ctx(request)
    ctx.update({"error": error})
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
        return RedirectResponse(url="/auth/signup?error=exists", status_code=302)

    resp = _redirect_to_best_chat(request, welcome=True)
    set_session_cookie(resp, email)
    return resp


@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request):
    error = request.query_params.get("error")
    ctx = _common_ctx(request)
    ctx.update({"error": error})
    return templates.TemplateResponse("login.html", ctx)


@router.post("/auth/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not verify_user(db, email, password):
        return RedirectResponse(url="/auth/login?error=bad_login", status_code=302)

    resp = _redirect_to_best_chat(request, welcome=True)
    set_session_cookie(resp, email)
    return resp


@router.post("/auth/logout")
def logout(request: Request):
    resp = RedirectResponse(url="/", status_code=302)
    clear_session_cookie(resp)
    return resp


# Optional tiny debug endpoint (safe-ish; remove if you want)
@router.get("/auth/dev/whoami")
def whoami(request: Request):
    return {
        "email": get_session_email(request),
        "last_slug": _cookie_slug(request),
        "templates_dir": str(TEMPLATES_DIR),
    }