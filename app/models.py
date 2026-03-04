# app/models.py
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from .db import Base


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # ordering lifecycle
    status = Column(String, default="draft")  # draft | confirmed

    # kitchen lifecycle
    restaurant_slug = Column(String, index=True, default="")
    kitchen_status = Column(String, default="new")  # new | accepted | preparing | completed

    # customer details captured during checkout
    customer_name = Column(String, default="")
    customer_email = Column(String, default="")
    customer_phone = Column(String, default="")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    summary_text = Column(Text, default="")
    items_json = Column(Text, default="[]")
    state_json = Column(Text, default="{}")


class StaffUser(Base):
    __tablename__ = "staff_users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)

    restaurant_slug = Column(String, index=True, nullable=False)  # ties login to dataset
    role = Column(String, default="staff")  # optional: staff/admin

    created_at = Column(DateTime, default=datetime.utcnow)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)





