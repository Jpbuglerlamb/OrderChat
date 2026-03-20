# app/services/stripe_services.py
from __future__ import annotations

import os

import stripe

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_MONTHLY_PRICE_ID = os.getenv("STRIPE_MONTHLY_PRICE_ID")
STRIPE_YEARLY_PRICE_ID = os.getenv("STRIPE_YEARLY_PRICE_ID")

if not STRIPE_SECRET_KEY:
    raise RuntimeError("Missing STRIPE_SECRET_KEY")

if not STRIPE_MONTHLY_PRICE_ID:
    raise RuntimeError("Missing STRIPE_MONTHLY_PRICE_ID")

if not STRIPE_YEARLY_PRICE_ID:
    raise RuntimeError("Missing STRIPE_YEARLY_PRICE_ID")

stripe.api_key = STRIPE_SECRET_KEY


def get_price_id_for_plan(plan: str) -> str:
    normalized_plan = (plan or "").strip().lower()

    if normalized_plan == "yearly":
        return STRIPE_YEARLY_PRICE_ID

    return STRIPE_MONTHLY_PRICE_ID


def create_checkout_session(
    *,
    base_url: str,
    plan: str,
    restaurant_id: int,
    customer_email: str,
) -> str:
    normalized_plan = (plan or "").strip().lower()
    price_id = get_price_id_for_plan(normalized_plan)

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        line_items=[
            {
                "price": price_id,
                "quantity": 1,
            }
        ],
        customer_email=customer_email,
        client_reference_id=str(restaurant_id),
        metadata={
            "restaurant_id": str(restaurant_id),
            "plan_key": normalized_plan,
        },
        subscription_data={
            "trial_period_days": 30,
            "metadata": {
                "restaurant_id": str(restaurant_id),
                "plan_key": normalized_plan,
            },
        },
        success_url=f"{base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}/billing/cancel",
    )

    return session.url


def create_billing_portal_session(
    *,
    stripe_customer_id: str,
    return_url: str,
) -> str:
    session = stripe.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=return_url,
    )
    return session.url