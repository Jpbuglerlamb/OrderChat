#app/routes/auth_platform.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security.auth import hash_password, verify_password

router = APIRouter(tags=["auth-platform"])

COOKIE_NAME = os.getenv("WEB_SESSION_COOKIE", "jpai_session")
COOKIE_TTL_DAYS = int(os.getenv("WEB_SESSION_TTL_DAYS", "14"))

# IMPORTANT: set WEB_SESSION_SECRET in production
SECRET = os.getenv("WEB_SESSION_SECRET", "dev-session-secret-change-me")
serializer = URLSafeSerializer(SECRET, salt="jpai-web-session")


def _sign_email(email: str) -> str:
    return serializer.dumps({"email": email})


def _unsign_email(token: str) -> Optional[str]:
    try:
        data = serializer.loads(token)
        email = (data.get("email") or "").strip().lower()
        return email or None
    except BadSignature:
        return None
    except Exception:
        return None


def _cookie_secure() -> bool:
    return os.getenv("WEB_SESSION_SECURE", "0").strip() == "1"


def _cookie_expires() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=COOKIE_TTL_DAYS)


def set_session_cookie(resp: Response, email: str) -> None:
    token = _sign_email(email.strip().lower())
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=60 * 60 * 24 * COOKIE_TTL_DAYS,
        expires=_cookie_expires(),
        path="/",
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(
        key=COOKIE_NAME,
        path="/",
    )


def get_session_email(request: Request) -> Optional[str]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return _unsign_email(token)


def get_current_platform_user(request: Request, db: Session) -> Optional[User]:
    email = get_session_email(request)
    if not email:
        return None
    return db.query(User).filter(func.lower(User.email) == email.lower()).first()


def create_user(
    db: Session,
    email: str,
    password: str,
    name: str,
    phone: str,
    address: str,
) -> User:
    email_norm = email.strip().lower()

    existing = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if existing:
        raise ValueError("User already exists")

    u = User(
        email=email_norm,
        password_hash=hash_password(password),
        name=name.strip() or "User",
        phone=(phone.strip() or None),
        address=(address.strip() or None),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def verify_user(db: Session, email: str, password: str) -> bool:
    email_norm = email.strip().lower()
    u = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if not u:
        return False
    return verify_password(password, u.password_hash)


@router.post("/business/auth/login")
def login(
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/business"),
    db: Session = Depends(get_db),
):
    email_norm = email.strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email_norm).first()

    if not user or not verify_password(password, user.password_hash):
        login_url = "/business/login?error=bad_login"
        if next:
            login_url += f"&next={next}"
        return RedirectResponse(url=login_url, status_code=302)

    redirect = RedirectResponse(url=next or "/business", status_code=302)
    set_session_cookie(redirect, user.email)
    return redirect


@router.post("/business/auth/logout")
def logout():
    response = RedirectResponse(url="/business", status_code=302)
    clear_session_cookie(response)
    return response