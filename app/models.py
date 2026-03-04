# app/models.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    # optional convenience relationship
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # ordering lifecycle
    status = Column(String, default="draft", nullable=False)  # draft | confirmed

    # kitchen lifecycle
    restaurant_slug = Column(String, index=True, default="", nullable=False)
    kitchen_status = Column(
        String,
        default="new",
        nullable=False,
    )  # new | accepted | preparing | ready | completed

    # customer details captured during checkout
    customer_name = Column(String, default="", nullable=False)
    customer_email = Column(String, default="", nullable=False)
    customer_phone = Column(String, default="", nullable=False)

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

    # optional convenience relationship
    user = relationship("User", back_populates="orders")


class StaffUser(Base):
    __tablename__ = "staff_users"

    id = Column(Integer, primary_key=True)

    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    restaurant_slug = Column(String, index=True, nullable=False)  # ties login to dataset
    role = Column(String, default="staff", nullable=False)  # staff | admin (optional)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )




