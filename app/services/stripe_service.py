import os
import stripe

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")

if not STRIPE_SECRET_KEY:
    raise RuntimeError("Missing STRIPE_SECRET_KEY")

if not STRIPE_PRICE_ID:
    raise RuntimeError("Missing STRIPE_PRICE_ID")

stripe.api_key = STRIPE_SECRET_KEY


def create_checkout_session(base_url: str) -> str:
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        line_items=[
            {
                "price": STRIPE_PRICE_ID,
                "quantity": 1,
            }
        ],
        success_url=f"{base_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}/billing/cancel",
    )
    return session.url