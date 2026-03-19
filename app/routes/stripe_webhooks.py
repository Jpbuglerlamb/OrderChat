#app/routes/stripe_webhooks.py
from __future__ import annotations

import os

import stripe
from fastapi import APIRouter, Request, HTTPException

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed":
        session = data_object

        # pull out metadata you set when creating the checkout session
        restaurant_id = session.get("metadata", {}).get("restaurant_id")
        plan_key = session.get("metadata", {}).get("plan_key")

        # example fields you may want
        stripe_customer_id = session.get("customer")
        subscription_id = session.get("subscription")
        payment_status = session.get("payment_status")

        # TODO:
        # - look up restaurant in DB
        # - mark paid/active if payment_status == "paid"
        # - save stripe_customer_id / subscription_id / plan_key

    elif event_type == "checkout.session.async_payment_succeeded":
        session = data_object
        # handle delayed methods if needed

    elif event_type == "checkout.session.async_payment_failed":
        session = data_object
        # handle failed delayed payments if needed

    return {"received": True}