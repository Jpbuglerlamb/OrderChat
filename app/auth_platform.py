from __future__ import annotations

from typing import Optional
from itsdangerous import URLSafeSerializer
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from sqlalchemy import func

from .models_platform import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# TODO: move to env var later
_serializer = URLSafeSerializer("CHANGE_ME_TO_A_RANDOM_SECRET", salt="session")


def create_user(db: Session, email: str, password: str, name: str, phone: str, address: str) -> User:
    email_norm = email.strip().lower()
    existing = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if existing:
        raise ValueError("User already exists")

    user = User(
        email=email_norm,
        password_hash=pwd_context.hash(password),
        name=name.strip(),
        phone=phone.strip(),
        address=address.strip(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def verify_user(db: Session, email: str, password: str) -> bool:
    email_norm = email.strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if not user:
        return False
    return pwd_context.verify(password, user.password_hash)


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    email_norm = email.strip().lower()
    return db.query(User).filter(func.lower(User.email) == email_norm).first()


def set_session_cookie(response, email: str) -> None:
    token = _serializer.dumps({"email": email.strip().lower()})
    response.set_cookie(
        "session",
        token,
        httponly=True,
        samesite="lax",
        secure=False,  # True once HTTPS
        max_age=60 * 60 * 24 * 14,
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie("session")


def get_session_email(request) -> Optional[str]:
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        data = _serializer.loads(token)
        return str(data.get("email") or "")
    except Exception:
        return None