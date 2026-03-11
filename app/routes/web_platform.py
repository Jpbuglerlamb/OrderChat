# app/routes/web_platform.py
from pathlib import Path
import json
import os
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

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


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@router.get("/customer", response_class=HTMLResponse)
def customer_page(request: Request):
    return templates.TemplateResponse("customer_home.html", {"request": request})


@router.get("/business", response_class=HTMLResponse)
def business_page(request: Request):
    return templates.TemplateResponse("business_home.html", {"request": request})


@router.get("/business/signup", response_class=HTMLResponse)
def business_signup_page(request: Request):
    return templates.TemplateResponse(
        "business_signup.html",
        {"request": request, "error": None}
    )


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
    menu_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    # 1. Check duplicate email
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        return templates.TemplateResponse(
            "business_signup.html",
            {
                "request": request,
                "error": "An account with that email already exists."
            },
            status_code=400,
        )

    # 2. Build unique slug
    base_slug = slugify(business_name)
    slug = unique_slug(db, base_slug)

    # 3. Read uploaded file bytes once
    original_filename = menu_file.filename or "menu_upload.bin"
    file_bytes = await menu_file.read()

    # 4. Upload raw menu file to S3
    raw_menu_s3_key = build_s3_menu_key(slug, original_filename)
    upload_file_bytes(
        data=file_bytes,
        key=raw_menu_s3_key,
        content_type=menu_file.content_type or "application/octet-stream",
    )

    # 5. Convert uploaded file into normalized menu dataset
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
                "error": f"Menu could not be parsed: {str(e)}"
            },
            status_code=400,
        )

    # 6. Upload generated menu.json to S3
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

    # 7. Generate QR code and upload to S3
    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip()
    if not public_base_url:
        return templates.TemplateResponse(
            "business_signup.html",
            {
                "request": request,
                "error": "PUBLIC_BASE_URL is missing from environment variables."
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

    # 8. Create user
    user = User(
        name=name,
        email=email,
        phone=phone or None,
        address=address,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.flush()

    # 9. Create restaurant
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

    return RedirectResponse(
        url=f"/business/onboarding-complete?slug={slug}",
        status_code=303,
    )


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


@router.get("/debug/users")
def debug_users(db: Session = Depends(get_db)):
    rows = db.query(User).all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "phone": u.phone,
            "address": u.address,
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
            "address": r.address,
            "opening_hours": r.opening_hours,
            "menu_upload_path": r.menu_upload_path,
            "menu_json_path": r.menu_json_path,
            "qr_code_path": r.qr_code_path,
        }
        for r in rows
    ]


@router.get("/debug/restaurant-files")
def debug_restaurant_files(db: Session = Depends(get_db)):
    rows = db.query(Restaurant).all()
    return [
        {
            "slug": r.slug,
            "menu_upload_path": r.menu_upload_path,
            "menu_exists_in_s3": file_exists(r.menu_upload_path) if r.menu_upload_path else False,
            "menu_json_path": r.menu_json_path,
            "menu_json_exists_in_s3": file_exists(r.menu_json_path) if r.menu_json_path else False,
            "qr_code_path": r.qr_code_path,
            "qr_exists_in_s3": file_exists(r.qr_code_path) if r.qr_code_path else False,
        }
        for r in rows
    ]