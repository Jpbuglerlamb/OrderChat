# app/auth.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import jwt
import os
from typing import Any, Dict, Optional



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

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALG = "HS256"
JWT_TTL_DAYS = int(os.getenv("JWT_TTL_DAYS", "14"))

def create_staff_token(payload: Dict[str, Any]) -> str:
    data = dict(payload)
    data["type"] = "staff"
    data["exp"] = datetime.utcnow() + timedelta(days=JWT_TTL_DAYS)
    return jwt.encode(data, JWT_SECRET, algorithm=JWT_ALG)

def decode_staff_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        if not isinstance(data, dict) or data.get("type") != "staff":
            return None
        return data
    except Exception:
        return None