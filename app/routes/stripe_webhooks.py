# app/routes/stripe_webhooks.py
from __future__ import annotations

import os

import stripe
from fastapi import APIRouter, HTTPException, Request

from app.db import SessionLocal
from app.models import Restaurant

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()


def get_restaurant_by_event_object(db, obj) -> Restaurant | None:
    restaurant_id = (
        obj.get("metadata", {}).get("restaurant_id")
        or obj.get("client_reference_id")
    )

    if not restaurant_id:
        return None

    try:
        return (
            db.query(Restaurant)
            .filter(Restaurant.id == int(restaurant_id))
            .first()
        )
    except Exception:
        return None


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

    db = SessionLocal()
    try:
        if event_type == "checkout.session.completed":
            session = data_object

            restaurant = get_restaurant_by_event_object(db, session)
            plan_key = session.get("metadata", {}).get("plan_key")
            stripe_customer_id = session.get("customer")
            stripe_subscription_id = session.get("subscription")
            payment_status = session.get("payment_status")

            print("restaurant:", restaurant.id if restaurant else None)
            print("plan_key:", plan_key)
            print("stripe_customer_id:", stripe_customer_id)
            print("stripe_subscription_id:", stripe_subscription_id)
            print("payment_status:", payment_status)

            if restaurant:
                if plan_key:
                    restaurant.selected_plan = plan_key

                if stripe_customer_id:
                    restaurant.stripe_customer_id = stripe_customer_id

                if stripe_subscription_id:
                    restaurant.stripe_subscription_id = stripe_subscription_id

                # Important for free trials:
                # checkout can complete before any paid invoice happens
                if payment_status == "paid":
                    restaurant.subscription_status = "active"
                else:
                    restaurant.subscription_status = "trialing"

                db.commit()
                print(f"Restaurant {restaurant.id} updated from checkout.session.completed ✅")
            else:
                print("Restaurant not found for checkout.session.completed")

        elif event_type == "checkout.session.async_payment_succeeded":
            session = data_object

            restaurant = get_restaurant_by_event_object(db, session)
            plan_key = session.get("metadata", {}).get("plan_key")
            stripe_customer_id = session.get("customer")
            stripe_subscription_id = session.get("subscription")

            print("Async payment succeeded")
            print("restaurant:", restaurant.id if restaurant else None)

            if restaurant:
                if plan_key:
                    restaurant.selected_plan = plan_key

                if stripe_customer_id:
                    restaurant.stripe_customer_id = stripe_customer_id

                if stripe_subscription_id:
                    restaurant.stripe_subscription_id = stripe_subscription_id

                restaurant.subscription_status = "active"
                db.commit()
                print(f"Restaurant {restaurant.id} activated from async payment ✅")
            else:
                print("Restaurant not found for checkout.session.async_payment_succeeded")

        elif event_type == "checkout.session.async_payment_failed":
            session = data_object
            restaurant = get_restaurant_by_event_object(db, session)

            print("Async payment failed")
            print("restaurant:", restaurant.id if restaurant else None)

            if restaurant:
                restaurant.subscription_status = "pending"
                db.commit()
                print(f"Restaurant {restaurant.id} kept pending after failed async payment")
            else:
                print("Restaurant not found for checkout.session.async_payment_failed")

        elif event_type == "invoice.paid":
            invoice = data_object
            stripe_subscription_id = invoice.get("subscription")
            stripe_customer_id = invoice.get("customer")

            print("Invoice paid")
            print("stripe_subscription_id:", stripe_subscription_id)
            print("stripe_customer_id:", stripe_customer_id)

            restaurant = None

            if stripe_subscription_id:
                restaurant = (
                    db.query(Restaurant)
                    .filter(Restaurant.stripe_subscription_id == stripe_subscription_id)
                    .first()
                )

            if not restaurant and stripe_customer_id:
                restaurant = (
                    db.query(Restaurant)
                    .filter(Restaurant.stripe_customer_id == stripe_customer_id)
                    .first()
                )

            if restaurant:
                restaurant.subscription_status = "active"
                db.commit()
                print(f"Restaurant {restaurant.id} marked active from invoice.paid ✅")
            else:
                print("Restaurant not found for invoice.paid")

        elif event_type == "invoice.payment_failed":
            invoice = data_object
            stripe_subscription_id = invoice.get("subscription")
            stripe_customer_id = invoice.get("customer")

            print("Invoice payment failed")
            print("stripe_subscription_id:", stripe_subscription_id)
            print("stripe_customer_id:", stripe_customer_id)

            restaurant = None

            if stripe_subscription_id:
                restaurant = (
                    db.query(Restaurant)
                    .filter(Restaurant.stripe_subscription_id == stripe_subscription_id)
                    .first()
                )

            if not restaurant and stripe_customer_id:
                restaurant = (
                    db.query(Restaurant)
                    .filter(Restaurant.stripe_customer_id == stripe_customer_id)
                    .first()
                )

            if restaurant:
                restaurant.subscription_status = "past_due"
                db.commit()
                print(f"Restaurant {restaurant.id} marked past_due from invoice.payment_failed")
            else:
                print("Restaurant not found for invoice.payment_failed")

        elif event_type == "customer.subscription.deleted":
            subscription = data_object
            stripe_subscription_id = subscription.get("id")
            stripe_customer_id = subscription.get("customer")

            print("Subscription deleted")
            print("stripe_subscription_id:", stripe_subscription_id)
            print("stripe_customer_id:", stripe_customer_id)

            restaurant = None

            if stripe_subscription_id:
                restaurant = (
                    db.query(Restaurant)
                    .filter(Restaurant.stripe_subscription_id == stripe_subscription_id)
                    .first()
                )

            if not restaurant and stripe_customer_id:
                restaurant = (
                    db.query(Restaurant)
                    .filter(Restaurant.stripe_customer_id == stripe_customer_id)
                    .first()
                )

            if restaurant:
                restaurant.subscription_status = "cancelled"
                db.commit()
                print(f"Restaurant {restaurant.id} marked cancelled ✅")
            else:
                print("Restaurant not found for customer.subscription.deleted")

    except Exception as e:
        db.rollback()
        print(f"Webhook processing error: {e}")
        raise
    finally:
        db.close()

    return {"received": True}