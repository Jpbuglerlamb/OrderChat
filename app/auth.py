# app/auth.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt
from passlib.context import CryptContext


pwd = CryptContext(schemes=["argon2"], deprecated="auto")


def _jwt_secret() -> str:
    return os.getenv("JWT_SECRET", "dev-secret-change-me")


def _jwt_alg() -> str:
    return os.getenv("JWT_ALG", "HS256")


def _jwt_expire_minutes() -> int:
    # default 24h
    raw = os.getenv("JWT_EXPIRE_MIN", "1440")
    try:
        return int(raw)
    except Exception:
        return 1440


def hash_password(p: str) -> str:
    return pwd.hash(p)


def verify_password(p: str, h: str) -> bool:
    return pwd.verify(p, h)


def create_token(user_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=_jwt_expire_minutes())
    payload = {"sub": str(user_id), "exp": exp}
    return jwt.encode(payload, _jwt_secret(), algorithm=_jwt_alg())


def decode_token(token: str) -> Optional[int]:
    try:
        data = jwt.decode(token, _jwt_secret(), algorithms=[_jwt_alg()])
        return int(data.get("sub"))
    except Exception:
        return None
