#app/routes/stripe_webhooks.py
from __future__ import annotations

import os

import stripe
from fastapi import APIRouter, HTTPException, Request

from app.db import SessionLocal
from app.models import Restaurant

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Missing STRIPE_WEBHOOK_SECRET")

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

    print("Webhook hit")
    print("Event type:", event_type)

    if event_type == "checkout.session.completed":
        session = data_object

        restaurant_id = (
            session.get("metadata", {}).get("restaurant_id")
            or session.get("client_reference_id")
        )
        plan_key = session.get("metadata", {}).get("plan_key")
        stripe_customer_id = session.get("customer")
        stripe_subscription_id = session.get("subscription")
        payment_status = session.get("payment_status")

        print("restaurant_id:", restaurant_id)
        print("plan_key:", plan_key)
        print("stripe_customer_id:", stripe_customer_id)
        print("stripe_subscription_id:", stripe_subscription_id)
        print("payment_status:", payment_status)

        if payment_status == "paid" and restaurant_id:
            db = SessionLocal()
            try:
                restaurant = (
                    db.query(Restaurant)
                    .filter(Restaurant.id == int(restaurant_id))
                    .first()
                )

                if not restaurant:
                    print(f"Restaurant not found: {restaurant_id}")
                else:
                    if plan_key:
                        restaurant.selected_plan = plan_key

                    restaurant.subscription_status = "active"
                    restaurant.stripe_customer_id = stripe_customer_id
                    restaurant.stripe_subscription_id = stripe_subscription_id

                    db.commit()
                    print(f"Restaurant {restaurant_id} activated ✅")

            except Exception as e:
                db.rollback()
                print(f"Webhook DB update failed: {e}")
                raise
            finally:
                db.close()

    elif event_type == "checkout.session.async_payment_succeeded":
        session = data_object

        restaurant_id = (
            session.get("metadata", {}).get("restaurant_id")
            or session.get("client_reference_id")
        )
        plan_key = session.get("metadata", {}).get("plan_key")
        stripe_customer_id = session.get("customer")
        stripe_subscription_id = session.get("subscription")

        print("Async payment succeeded")
        print("restaurant_id:", restaurant_id)

        if restaurant_id:
            db = SessionLocal()
            try:
                restaurant = (
                    db.query(Restaurant)
                    .filter(Restaurant.id == int(restaurant_id))
                    .first()
                )

                if restaurant:
                    if plan_key:
                        restaurant.selected_plan = plan_key

                    restaurant.subscription_status = "active"
                    restaurant.stripe_customer_id = stripe_customer_id
                    restaurant.stripe_subscription_id = stripe_subscription_id

                    db.commit()
                    print(f"Restaurant {restaurant_id} activated from async payment ✅")
                else:
                    print(f"Restaurant not found: {restaurant_id}")

            except Exception as e:
                db.rollback()
                print(f"Async webhook DB update failed: {e}")
                raise
            finally:
                db.close()

    elif event_type == "checkout.session.async_payment_failed":
        session = data_object

        restaurant_id = (
            session.get("metadata", {}).get("restaurant_id")
            or session.get("client_reference_id")
        )

        print("Async payment failed")
        print("restaurant_id:", restaurant_id)

        if restaurant_id:
            db = SessionLocal()
            try:
                restaurant = (
                    db.query(Restaurant)
                    .filter(Restaurant.id == int(restaurant_id))
                    .first()
                )

                if restaurant:
                    restaurant.subscription_status = "pending"
                    db.commit()
                    print(f"Restaurant {restaurant_id} kept as pending after failed async payment")
                else:
                    print(f"Restaurant not found: {restaurant_id}")

            except Exception as e:
                db.rollback()
                print(f"Async payment failed DB update error: {e}")
                raise
            finally:
                db.close()

    return {"received": True}