# app/models.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from .db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)

    # restaurant
    restaurant_slug = Column(String, index=True)

    # optional user account (future feature)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # customer info
    customer_name = Column(String)
    customer_phone = Column(String)
    customer_email = Column(String)

    # order lifecycle
    status = Column(String, default="draft")       # draft | confirmed
    kitchen_status = Column(String, default="new") # new | accepted | ready | completed

    # timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    # order data
    summary_text = Column(Text, default="")
    items_json = Column(Text, default="[]")
    state_json = Column(Text, default="{}")

