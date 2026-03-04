# app/routes/web_customer.py
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db

from .auth_platform import (
    create_user,
    verify_user,
    set_session_cookie,
    clear_session_cookie,
    get_session_email,
)

router = APIRouter(tags=["web-auth"])

# Point templates at project_root/frontend (NOT app/frontend)
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # TakeawayDemo/
TEMPLATES_DIR = PROJECT_ROOT / "app" / "frontend"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/auth/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
            "error": error,
            "email": get_session_email(request),
            "year": datetime.utcnow().year,
        },
    )


@router.post("/auth/signup")
def signup(
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

    # After signup, send them to chat (welcome flag optional)
    resp = RedirectResponse(url="/chat?welcome=1", status_code=302)
    set_session_cookie(resp, email)
    return resp


@router.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request):
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
            "email": get_session_email(request),
            "year": datetime.utcnow().year,
        },
    )


@router.post("/auth/login")
def login(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not verify_user(db, email, password):
        return RedirectResponse(url="/auth/login?error=bad_login", status_code=302)

    resp = RedirectResponse(url="/chat", status_code=302)
    set_session_cookie(resp, email)
    return resp


@router.post("/auth/logout")
def logout():
    resp = RedirectResponse(url="/", status_code=302)
    clear_session_cookie(resp)
    return resp