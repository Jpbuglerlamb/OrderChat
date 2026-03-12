from pathlib import Path
import json
import os
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.routes.auth_platform import set_session_cookie
from app.db import get_db
from app.models import User, Restaurant
from app.security.auth import hash_password
from app.services.menu_ingest import ingest_menu_file_to_dataset
from app.services.qr_service import build_restaurant_public_url, generate_qr_png_bytes
from app.services.storage import upload_file_bytes, file_exists, generate_download_url

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "restaurant"


def unique_slug(db: Session, base_slug: str) -> str:
    slug = base_slug
    counter = 2

    while db.query(Restaurant).filter(Restaurant.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    return slug


def build_s3_menu_key(slug: str, filename: str) -> str:
    safe_name = (filename or "menu_upload.bin").replace(" ", "_")
    return f"restaurants/{slug}/uploads/{uuid4()}_{safe_name}"


# --------------------------------
# Basic Pages
# --------------------------------

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@router.get("/customer", response_class=HTMLResponse)
def customer_page(request: Request):
    return templates.TemplateResponse("customer_home.html", {"request": request})


@router.get("/business", response_class=HTMLResponse)
def business_page(request: Request):
    return templates.TemplateResponse("business_home.html", {"request": request})


# --------------------------------
# Pricing Page
# --------------------------------

@router.get("/business/pricing", response_class=HTMLResponse)
def business_pricing_page(request: Request):
    return templates.TemplateResponse(
        "business_pricing.html",
        {"request": request},
    )


# --------------------------------
# Signup Page
# --------------------------------

@router.get("/business/signup", response_class=HTMLResponse)
def business_signup_page(request: Request, plan: str = ""):
    return templates.TemplateResponse(
        "business_signup.html",
        {
            "request": request,
            "error": None,
            "plan": plan,
        },
    )


# --------------------------------
# Signup Submit
# --------------------------------

@router.post("/business/signup", response_class=HTMLResponse)
async def business_signup_submit(
    request: Request,
    name: str = Form(...),
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    phone: str = Form(""),
    address: str = Form(...),
    opening_hours: str = Form(...),
    plan: str = Form("starter"),
    menu_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):

    existing_user = db.query(User).filter(User.email == email).first()

    if existing_user:
        return templates.TemplateResponse(
            "business_signup.html",
            {
                "request": request,
                "error": "An account with that email already exists.",
                "plan": plan,
            },
            status_code=400,
        )

    base_slug = slugify(business_name)
    slug = unique_slug(db, base_slug)

    original_filename = menu_file.filename or "menu_upload.bin"
    file_bytes = await menu_file.read()

    raw_menu_s3_key = build_s3_menu_key(slug, original_filename)

    upload_file_bytes(
        data=file_bytes,
        key=raw_menu_s3_key,
        content_type=menu_file.content_type or "application/octet-stream",
    )

    try:
        menu_dataset = ingest_menu_file_to_dataset(
            file_bytes=file_bytes,
            filename=original_filename,
            business_name=business_name,
            email=email,
            phone=phone or "",
            address=address,
            opening_hours=opening_hours,
            currency="GBP",
            pickup_only=True,
        )
    except Exception as e:
        return templates.TemplateResponse(
            "business_signup.html",
            {
                "request": request,
                "error": f"Menu could not be parsed: {str(e)}",
                "plan": plan,
            },
            status_code=400,
        )

    menu_json_bytes = json.dumps(
        menu_dataset,
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")

    menu_json_s3_key = f"restaurants/{slug}/menu.json"

    upload_file_bytes(
        data=menu_json_bytes,
        key=menu_json_s3_key,
        content_type="application/json",
    )

    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()

    if not public_base_url:
        return templates.TemplateResponse(
            "business_signup.html",
            {
                "request": request,
                "error": "PUBLIC_BASE_URL is missing from environment variables.",
                "plan": plan,
            },
            status_code=500,
        )

    restaurant_public_url = build_restaurant_public_url(public_base_url, slug)
    qr_png_bytes = generate_qr_png_bytes(restaurant_public_url)

    qr_s3_key = f"restaurants/{slug}/qr.png"

    upload_file_bytes(
        data=qr_png_bytes,
        key=qr_s3_key,
        content_type="image/png",
    )

    user = User(
        name=name,
        email=email,
        phone=phone or None,
        address=address,
        password_hash=hash_password(password),
    )

    db.add(user)
    db.flush()

    restaurant = Restaurant(
        owner_user_id=user.id,
        name=business_name,
        slug=slug,
        phone=phone or "",
        address=address,
        opening_hours=opening_hours,
        menu_upload_path=raw_menu_s3_key,
        menu_json_path=menu_json_s3_key,
        qr_code_path=qr_s3_key,
    )

    db.add(restaurant)
    db.commit()

    response = RedirectResponse(
        url=f"/business/onboarding-complete?slug={slug}",
        status_code=303,
    )
    set_session_cookie(response, email)
    return response


# --------------------------------
# Onboarding Complete
# --------------------------------

@router.get("/business/onboarding-complete", response_class=HTMLResponse)
def onboarding_complete(
    request: Request,
    slug: str = "",
    db: Session = Depends(get_db),
):
    restaurant = db.query(Restaurant).filter(Restaurant.slug == slug).first()

    qr_download_url = None

    if restaurant and restaurant.qr_code_path:
        try:
            qr_download_url = generate_download_url(restaurant.qr_code_path)
        except Exception:
            qr_download_url = None

    return templates.TemplateResponse(
        "business_onboarding_complete.html",
        {
            "request": request,
            "slug": slug,
            "restaurant": restaurant,
            "qr_download_url": qr_download_url,
        },
    )


# --------------------------------
# Debug
# --------------------------------

@router.get("/debug/users")
def debug_users(db: Session = Depends(get_db)):
    rows = db.query(User).all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
        }
        for u in rows
    ]


@router.get("/debug/restaurants")
def debug_restaurants(db: Session = Depends(get_db)):
    rows = db.query(Restaurant).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "slug": r.slug,
        }
        for r in rows
    ]