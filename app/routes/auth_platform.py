# app/routes/auth_platform.py
from __future__ import annotations

import os
from typing import Optional

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import User
from app.security.auth import hash_password, verify_password

COOKIE_NAME = os.getenv("WEB_SESSION_COOKIE", "jpai_session")
COOKIE_TTL_DAYS = int(os.getenv("WEB_SESSION_TTL_DAYS", "14"))

# IMPORTANT: set WEB_SESSION_SECRET in Render
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
    """
    Render is HTTPS, localhost often isn't.
    Control via env:
      WEB_SESSION_SECURE=1  -> Secure cookies
      WEB_SESSION_SECURE=0  -> Non-secure cookies (default)
    """
    return os.getenv("WEB_SESSION_SECURE", "0").strip() == "1"


def set_session_cookie(resp: Response, email: str) -> None:
    token = _sign_email(email.strip().lower())
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        max_age=60 * 60 * 24 * COOKIE_TTL_DAYS,
    )


def clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE_NAME)


def get_session_email(request: Request) -> Optional[str]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return _unsign_email(token)


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