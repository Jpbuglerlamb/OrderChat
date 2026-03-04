# app/routes/api_auth.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_db
from app.models import User
from app.security.auth import hash_password, verify_password, create_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

class SignupIn(BaseModel):
    name: str
    email: EmailStr
    phone: str | None = None
    password: str

class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/signup")
def signup(payload: SignupIn, db: Session = Depends(get_db)):
    email_norm = payload.email.strip().lower()
    existing = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already exists")

    u = User(
        name=payload.name.strip(),
        email=email_norm,
        phone=(payload.phone.strip() if payload.phone else None),
        password_hash=hash_password(payload.password),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"ok": True}


@router.post("/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    email_norm = payload.email.strip().lower()
    u = db.query(User).filter(func.lower(User.email) == email_norm).first()
    if not u or not verify_password(payload.password, u.password_hash):
        raise HTTPException(status_code=401, detail="Bad credentials")
    return {"token": create_token(u.id)}