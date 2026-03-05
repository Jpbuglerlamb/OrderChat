# app/models.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    # Basic profile
    name = Column(String(120), nullable=False)
    email = Column(String(320), unique=True, index=True, nullable=False)
    phone = Column(String(40), nullable=True)
    address = Column(String(255), nullable=True)

    # IMPORTANT: store hashes in TEXT so they never truncate
    password_hash = Column(Text, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # ordering lifecycle
    status = Column(String(20), default="draft", nullable=False)  # draft | confirmed

    # restaurant + kitchen lifecycle
    restaurant_slug = Column(String(120), index=True, default="", nullable=False)

    # allow NULL so your staff query that includes NULL kitchen_status can work
    # (you set it to "new" on confirmation anyway)
    kitchen_status = Column(String(30), default=None, nullable=True)  # new|accepted|preparing|ready|completed

    # customer details captured during checkout
    customer_name = Column(String(120), default="", nullable=False)
    customer_email = Column(String(320), default="", nullable=False)
    customer_phone = Column(String(40), default="", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    summary_text = Column(Text, default="", nullable=False)
    items_json = Column(Text, default="[]", nullable=False)
    state_json = Column(Text, default="{}", nullable=False)

    user = relationship("User", back_populates="orders")


class StaffUser(Base):
    __tablename__ = "staff_users"

    id = Column(Integer, primary_key=True)

    email = Column(String(320), unique=True, index=True, nullable=False)

    # IMPORTANT: store hashes in TEXT so they never truncate
    password_hash = Column(Text, nullable=False)

    restaurant_slug = Column(String(120), index=True, nullable=False)
    role = Column(String(30), default="staff", nullable=False)  # staff | admin

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )